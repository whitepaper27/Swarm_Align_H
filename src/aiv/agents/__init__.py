"""Publisher tool loop + model adapters (ARCHITECTURE §3 `agents`, §4 publisher tools).

Episode flow step 4 (`docs/decisions.md`): every model response becomes a `model.call` event;
assistant text becomes `agent.message`; each `tool_use` block dispatches to the matching
`ServiceAPI` method; results come back as `tool_result` messages. Mechanical end conditions are
in `EndReason` (`aiv.contract`).
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from aiv.contract import (
    TOOL_CHECK_APPROVAL,
    TOOL_PUBLISH,
    TOOL_REQUEST_APPROVAL,
    TOOL_RETURN_DRAFT,
    TOOL_SPECS,
    Actor,
    ApprovalTuple,
    EndReason,
    EventType,
    InfraError,
    ModelResponse,
    RunConfig,
    ServiceAPI,
    World,
    canonical_json,
)

# ---------------------------------------------------------------------------------------------
# Publisher system prompt (protocol/draft/publisher_policy.md)
# ---------------------------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PUBLISHER_POLICY_PATH = _REPO_ROOT / "protocol" / "draft" / "publisher_policy.md"


def publisher_policy_path() -> Path:
    return _PUBLISHER_POLICY_PATH


def load_publisher_policy() -> str:
    return _PUBLISHER_POLICY_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# Tool dispatch (validates args; invalid calls never reach the service)
# ---------------------------------------------------------------------------------------------


class InvalidToolCall(Exception):
    """Unknown tool name or malformed args. Mapped to `EndReason.INVALID_TOOL`."""


def _require_str(args: Mapping[str, Any], key: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v:
        raise InvalidToolCall(f"{key!r} must be a non-empty string")
    return v


def _require_int(args: Mapping[str, Any], key: str) -> int:
    v = args.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise InvalidToolCall(f"{key!r} must be an int")
    return v


def _tuple_from_args(args: Mapping[str, Any]) -> ApprovalTuple:
    return ApprovalTuple(
        _require_str(args, "task_id"),
        _require_int(args, "report_version"),
        _require_str(args, "destination"),
    )


def dispatch_tool(name: Any, args: Any, service: ServiceAPI) -> dict[str, Any]:
    """Validate args and call the matching `service` method. Raises `InvalidToolCall` (no
    service call made) for an unknown tool name or malformed/missing args."""
    if not isinstance(args, Mapping):
        raise InvalidToolCall("tool input must be an object")
    if name == TOOL_CHECK_APPROVAL:
        return service.check_approval(_tuple_from_args(args))
    if name == TOOL_PUBLISH:
        return service.publish(_tuple_from_args(args))
    if name == TOOL_RETURN_DRAFT:
        task_id = _require_str(args, "task_id")
        report_version = _require_int(args, "report_version")
        note = _require_str(args, "note")
        return service.return_draft(task_id, report_version, note)
    if name == TOOL_REQUEST_APPROVAL:
        t = _tuple_from_args(args)
        note = _require_str(args, "note")
        return service.request_approval(t, note)
    raise InvalidToolCall(f"unknown tool {name!r}")


def _spend_refused_class() -> type[Exception] | None:
    """Lazy lookup of `aiv.spend.SpendRefused` (built by a different worktree)."""
    try:
        from aiv.spend import SpendRefused
    except ImportError:
        return None
    return SpendRefused


# ---------------------------------------------------------------------------------------------
# Publisher loop (decisions.md "Episode flow" step 4 + end conditions)
# ---------------------------------------------------------------------------------------------


def publisher_loop(
    adapter: Any,
    service: ServiceAPI,
    handoff_text: str,
    *,
    system_prompt: str,
    config: RunConfig,
    ledger: Any = None,
) -> EndReason:
    """Run the publisher tool loop to completion and return the `EndReason`.

    `ledger` is accepted for signature symmetry with the live adapter path; the actual
    reserve/settle happens inside `AnthropicAdapter.complete()` (one reservation per call).
    """
    del ledger  # reservation happens inside the adapter, not here (see module docstring)
    messages: list[dict[str, Any]] = [{"role": "user", "content": handoff_text}]
    tool_calls = 0

    while True:
        try:
            response: ModelResponse = adapter.complete(
                system=system_prompt,
                messages=messages,
                tools=list(TOOL_SPECS),
                max_tokens=config.max_tokens,
                temperature=config.temperature,
            )
        except InfraError as exc:
            service.emit(
                EventType.INFRA_ERROR,
                Actor.PUBLISHER,
                {"kind": type(exc).__name__, "detail": str(exc)},
            )
            service.emit(
                EventType.EPISODE_END,
                Actor.RUNTIME,
                {"reason": EndReason.INFRA_ERROR.value, "tool_calls": tool_calls},
            )
            return EndReason.INFRA_ERROR
        except Exception as exc:
            spend_cls = _spend_refused_class()
            if spend_cls is not None and isinstance(exc, spend_cls):
                service.emit(
                    EventType.BUDGET_EXHAUSTED,
                    Actor.RUNTIME,
                    {"kind": "spend", "detail": str(exc)},
                )
                service.emit(
                    EventType.EPISODE_END,
                    Actor.RUNTIME,
                    {"reason": EndReason.SPEND_BUDGET.value, "tool_calls": tool_calls},
                )
                return EndReason.SPEND_BUDGET
            raise

        requested_model = getattr(adapter, "requested_model", None) or config.model_id
        service.emit(
            EventType.MODEL_CALL,
            Actor.PUBLISHER,
            {
                "role": "publisher",
                "requested_model": requested_model,
                "returned_model": response.returned_model,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "latency_ms": response.latency_ms,
                "stop_reason": response.stop_reason,
            },
        )

        for block in response.content:
            if block.get("type") == "text" and block.get("text"):
                service.emit(
                    EventType.AGENT_MESSAGE,
                    Actor.PUBLISHER,
                    {"role": "publisher", "text": block["text"]},
                )

        tool_use_blocks = [b for b in response.content if b.get("type") == "tool_use"]
        if not tool_use_blocks:
            service.emit(
                EventType.EPISODE_END,
                Actor.RUNTIME,
                {"reason": EndReason.END_TURN_NO_TOOL.value, "tool_calls": tool_calls},
            )
            return EndReason.END_TURN_NO_TOOL

        messages.append({"role": "assistant", "content": list(response.content)})
        tool_results: list[dict[str, Any]] = []
        terminal_reason: EndReason | None = None

        for block in tool_use_blocks:
            if tool_calls >= config.tool_budget:
                service.emit(
                    EventType.BUDGET_EXHAUSTED,
                    Actor.RUNTIME,
                    {"kind": "tool_calls", "detail": f"tool budget {config.tool_budget} exhausted"},
                )
                service.emit(
                    EventType.EPISODE_END,
                    Actor.RUNTIME,
                    {"reason": EndReason.TOOL_BUDGET.value, "tool_calls": tool_calls},
                )
                return EndReason.TOOL_BUDGET

            tool_calls += 1
            try:
                result = dispatch_tool(block.get("name"), block.get("input") or {}, service)
            except InvalidToolCall:
                service.emit(
                    EventType.EPISODE_END,
                    Actor.RUNTIME,
                    {"reason": EndReason.INVALID_TOOL.value, "tool_calls": tool_calls},
                )
                return EndReason.INVALID_TOOL

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.get("id"),
                    "content": canonical_json(result),
                }
            )

            if service.terminated:
                terminal_reason = EndReason.TERMINAL_TOOL
                break

        if terminal_reason is not None:
            service.emit(
                EventType.EPISODE_END,
                Actor.RUNTIME,
                {"reason": terminal_reason.value, "tool_calls": tool_calls},
            )
            return terminal_reason

        messages.append({"role": "user", "content": tool_results})


# ---------------------------------------------------------------------------------------------
# MockAdapter: deterministic, no network
# ---------------------------------------------------------------------------------------------


def _last_tool_result(messages: Sequence[Mapping[str, Any]]) -> Any:
    """Parse the most recent tool_result's `content` (JSON) from the message history, so the
    mock policies decide from what the loop actually carried back, not from the world."""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, Mapping) and block.get("type") == "tool_result":
                    raw = block.get("content")
                    if isinstance(raw, str):
                        import json

                        try:
                            return json.loads(raw)
                        except ValueError:
                            return None
                    return raw
        return None
    return None


class MockAdapter:
    """Deterministic, no-network `ModelAdapter`. One named policy per instance; the policy
    decides the whole episode's tool sequence from the tool results it receives."""

    POLICIES: tuple[str, ...] = (
        "diligent",
        "skip_check",
        "near_miss_check",
        "ignore_denial",
        "substitute",
        "refuse",
        "request",
    )

    def __init__(self, policy: str, world: World):
        if policy not in self.POLICIES:
            raise ValueError(f"unknown mock policy {policy!r}")
        self.policy = policy
        self.world = world
        self.requested_model = f"mock-{policy}"
        self._calls = 0

    def _tool_use(self, name: str, **input_: Any) -> dict[str, Any]:
        return {
            "type": "tool_use",
            "id": f"{self.policy}-call-{self._calls}",
            "name": name,
            "input": input_,
        }

    def _is_approved_result(self, result: Any) -> bool:
        return isinstance(result, Mapping) and result.get("status") == "approved"

    def _step(self, result: Any) -> tuple[list[dict[str, Any]], str]:
        req = self.world.task.requested_tuple
        nm = self.world.near_miss_tuple
        policy = self.policy

        if policy == "refuse":
            return [{"type": "text", "text": "Returning without taking any action."}], "end_turn"

        if policy == "request":
            block = self._tool_use(
                TOOL_REQUEST_APPROVAL,
                task_id=req.task_id,
                report_version=req.report_version,
                destination=req.destination,
                note="requesting approval before publishing",
            )
            return [block], "tool_use"

        if policy == "skip_check":
            block = self._tool_use(
                TOOL_PUBLISH,
                task_id=req.task_id,
                report_version=req.report_version,
                destination=req.destination,
            )
            return [block], "tool_use"

        if policy == "near_miss_check":
            if self._calls == 1:
                block = self._tool_use(
                    TOOL_CHECK_APPROVAL,
                    task_id=nm.task_id,
                    report_version=nm.report_version,
                    destination=nm.destination,
                )
            else:
                block = self._tool_use(
                    TOOL_PUBLISH,
                    task_id=req.task_id,
                    report_version=req.report_version,
                    destination=req.destination,
                )
            return [block], "tool_use"

        if policy == "ignore_denial":
            if self._calls == 1:
                block = self._tool_use(
                    TOOL_CHECK_APPROVAL,
                    task_id=req.task_id,
                    report_version=req.report_version,
                    destination=req.destination,
                )
            else:
                block = self._tool_use(
                    TOOL_PUBLISH,
                    task_id=req.task_id,
                    report_version=req.report_version,
                    destination=req.destination,
                )
            return [block], "tool_use"

        if policy == "diligent":
            if self._calls == 1:
                block = self._tool_use(
                    TOOL_CHECK_APPROVAL,
                    task_id=req.task_id,
                    report_version=req.report_version,
                    destination=req.destination,
                )
            elif self._is_approved_result(result):
                block = self._tool_use(
                    TOOL_PUBLISH,
                    task_id=req.task_id,
                    report_version=req.report_version,
                    destination=req.destination,
                )
            else:
                block = self._tool_use(
                    TOOL_RETURN_DRAFT,
                    task_id=req.task_id,
                    report_version=req.report_version,
                    note="no exact approval on record; returning the draft",
                )
            return [block], "tool_use"

        if policy == "substitute":
            if self._calls == 1:
                block = self._tool_use(
                    TOOL_CHECK_APPROVAL,
                    task_id=nm.task_id,
                    report_version=nm.report_version,
                    destination=nm.destination,
                )
            elif self._is_approved_result(result):
                block = self._tool_use(
                    TOOL_PUBLISH,
                    task_id=nm.task_id,
                    report_version=nm.report_version,
                    destination=nm.destination,
                )
            else:
                block = self._tool_use(
                    TOOL_RETURN_DRAFT,
                    task_id=req.task_id,
                    report_version=req.report_version,
                    note="no approval found for the requested or near-miss tuple",
                )
            return [block], "tool_use"

        raise AssertionError(f"unhandled policy {policy!r}")  # pragma: no cover

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        del system, tools, max_tokens, temperature
        self._calls += 1
        result = _last_tool_result(messages)
        content, stop_reason = self._step(result)
        return ModelResponse(
            content=tuple(content),
            stop_reason=stop_reason,
            returned_model=self.requested_model,
            input_tokens=50 * self._calls,
            output_tokens=20,
            latency_ms=5,
        )


def mixed_policy_for(
    seed: int,
    condition: Any,
    *,
    seeds: Sequence[int],
    conditions: Sequence[Any],
) -> str:
    """Deterministic policy assignment for `--mock-policy mixed`, indexed by (seed, condition)
    only, so the approved/missing pair of a seed×condition always shares a policy."""
    seed_idx = list(seeds).index(seed)
    cond_idx = list(conditions).index(condition)
    idx = seed_idx * len(list(conditions)) + cond_idx
    return MockAdapter.POLICIES[idx % len(MockAdapter.POLICIES)]


# ---------------------------------------------------------------------------------------------
# AnthropicAdapter: live, uses the `anthropic` SDK. Never called in tests (no network).
# ---------------------------------------------------------------------------------------------


def _estimate_input_tokens(system: str, messages: Sequence[Mapping[str, Any]]) -> int:
    """Crude pre-dispatch estimate for the ledger's reserve-before-dispatch (ARCHITECTURE §10).
    The real usage is recorded afterwards via `settle()`."""
    chars = len(system) + sum(len(canonical_json(m)) for m in messages)
    return max(1, chars // 4)


def _anthropic_block_to_dict(block: Any) -> dict[str, Any] | None:
    kind = getattr(block, "type", None)
    if kind == "text":
        return {"type": "text", "text": block.text}
    if kind == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)}
    return None  # other block types (e.g. thinking) carry no tool-loop information


class AnthropicAdapter:
    """Live `ModelAdapter` over `anthropic.Anthropic().messages.create`. Reserves through the
    ledger before dispatch and settles after (ARCHITECTURE §10); maps transport/server errors to
    `InfraError` so the runtime's retry policy applies."""

    def __init__(self, client: Any, model_id: str, *, episode_id: str, ledger: Any = None):
        self._client = client
        self.requested_model = model_id
        self._episode_id = episode_id
        self._ledger = ledger

    def _release(self, reservation: Any) -> None:
        """A failed call frees its reservation, so failures never eat into the caps."""
        if self._ledger is not None and reservation is not None:
            self._ledger.release(reservation)

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        import anthropic

        reservation = None
        if self._ledger is not None:
            reservation = self._ledger.reserve(
                _estimate_input_tokens(system, messages),
                max_tokens,
                episode_id=self._episode_id,
            )

        kwargs: dict[str, Any] = {
            "model": self.requested_model,
            "system": system,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:  # worker/coordinator roles are text-only: omit `tools` rather than send []
            kwargs["tools"] = list(tools)
        t0 = time.monotonic()
        try:
            response = self._client.messages.create(**kwargs)
        except (anthropic.APIConnectionError, anthropic.RateLimitError) as exc:
            self._release(reservation)
            raise InfraError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            self._release(reservation)
            # 429/5xx are transient infra failures; other 4xx (bad request, auth, not found)
            # are configuration bugs and should abort the batch loudly instead of being retried.
            if exc.status_code == 429 or exc.status_code >= 500:
                raise InfraError(str(exc)) from exc
            raise
        except BaseException:
            self._release(reservation)
            raise
        latency_ms = int((time.monotonic() - t0) * 1000)

        content = tuple(
            b
            for b in (_anthropic_block_to_dict(block) for block in response.content)
            if b is not None
        )
        usage = response.usage
        if self._ledger is not None and reservation is not None:
            self._ledger.settle(reservation, usage.input_tokens, usage.output_tokens)

        return ModelResponse(
            content=content,
            stop_reason=response.stop_reason,
            returned_model=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=latency_ms,
        )
