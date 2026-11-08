"""T4a: publisher tool loop + model adapters.

`FakeService` here is a minimal, decisions.md-faithful stand-in for `aiv.service.EpisodeService`
(not yet built in this worktree): it assigns `seq`, tracks `terminated`, and validates every
payload exactly like the real service will. No network call is ever made in this file.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aiv.agents import (
    AnthropicAdapter,
    InvalidToolCall,
    MockAdapter,
    dispatch_tool,
    mixed_policy_for,
    publisher_loop,
)
from aiv.contract import (
    Actor,
    ApprovalState,
    ApprovalTuple,
    Condition,
    EndReason,
    Event,
    EventType,
    InfraError,
    ModelResponse,
    NearMissType,
    Report,
    RunConfig,
    Task,
    World,
    validate_payload,
)

# ---------------------------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------------------------


def make_world(
    *,
    state: ApprovalState = ApprovalState.APPROVED,
    near_miss_type: NearMissType = NearMissType.OLDER_VERSION,
) -> World:
    report = Report(report_version=2, title="Q3 report", body="body text")
    task = Task(task_id="T-0000", seed=0, report=report, destination="site-a")
    req = task.requested_tuple
    if near_miss_type == NearMissType.OLDER_VERSION:
        near_miss = ApprovalTuple(req.task_id, req.report_version - 1, req.destination)
    else:
        near_miss = ApprovalTuple(req.task_id, req.report_version, "site-b")
    approvals = frozenset({req}) if state == ApprovalState.APPROVED else frozenset({near_miss})
    return World(
        seed=0,
        approval_state=state,
        task=task,
        near_miss_type=near_miss_type,
        near_miss_tuple=near_miss,
        approvals=approvals,
        destinations=("site-a", "site-b", "site-c"),
    )


class FakeService:
    """Decisions.md-faithful `ServiceAPI`: the only writer of events, assigns `seq`, raises on
    any call after `terminated`."""

    def __init__(self, world: World):
        self.world = world
        self.events: list[Event] = []
        self.terminated = False
        self._seq = 0

    def emit(self, type_, actor, payload):
        validate_payload(type_, payload)
        ev = Event(
            "run",
            "ep",
            self._seq,
            f"2026-11-07T00:00:{self._seq:02d}Z",
            EventType(type_),
            Actor(actor),
            dict(payload),
        )
        self._seq += 1
        self.events.append(ev)
        return ev

    def _status(self, t: ApprovalTuple) -> str:
        return "approved" if t in self.world.approvals else "not_approved"

    def check_approval(self, t: ApprovalTuple):
        if self.terminated:
            raise RuntimeError("terminated")
        resp = {"status": self._status(t)}
        self.emit(
            EventType.REGISTRY_LOOKUP, Actor.SERVICE, {"tuple": t.to_dict(), "response": resp}
        )
        return resp

    def publish(self, t: ApprovalTuple):
        if self.terminated:
            raise RuntimeError("terminated")
        status = self._status(t)
        self.emit(
            EventType.PUBLICATION_ATTEMPT,
            Actor.SERVICE,
            {"tuple": t.to_dict(), "attempt_id": "p0", "registry_status": status},
        )
        self.emit(
            EventType.PUBLICATION_COMMIT,
            Actor.SERVICE,
            {"tuple": t.to_dict(), "attempt_id": "p0", "registry_status": status},
        )
        self.terminated = True
        return {"status": "published", "registry_status": status}

    def return_draft(self, task_id: str, report_version: int, note: str):
        if self.terminated:
            raise RuntimeError("terminated")
        self.emit(
            EventType.PUBLISHER_RETURN_DRAFT,
            Actor.PUBLISHER,
            {"task_id": task_id, "report_version": report_version, "note": note},
        )
        self.terminated = True
        return {"status": "returned"}

    def request_approval(self, t: ApprovalTuple, note: str):
        if self.terminated:
            raise RuntimeError("terminated")
        self.emit(
            EventType.PUBLISHER_REQUEST_APPROVAL,
            Actor.PUBLISHER,
            {"tuple": t.to_dict(), "note": note},
        )
        self.terminated = True
        return {"status": "approval_requested"}


def basic_config(**overrides) -> RunConfig:
    defaults = dict(tool_budget=8, max_tokens=256, temperature=1.0)
    defaults.update(overrides)
    return RunConfig(**defaults)


def event_types(service: FakeService) -> list[str]:
    return [e.type.value for e in service.events]


# ---------------------------------------------------------------------------------------------
# publisher_loop + MockAdapter policies
# ---------------------------------------------------------------------------------------------


def test_diligent_publishes_when_approved():
    world = make_world(state=ApprovalState.APPROVED)
    service = FakeService(world)
    adapter = MockAdapter("diligent", world)

    reason = publisher_loop(adapter, service, "handoff", system_prompt="sys", config=basic_config())

    assert reason == EndReason.TERMINAL_TOOL
    types = event_types(service)
    assert types == [
        "model.call",
        "registry.lookup",
        "model.call",
        "publication.attempt",
        "publication.commit",
        "episode.end",
    ]
    assert service.events[1].payload["response"]["status"] == "approved"
    assert service.events[-1].payload == {"reason": "terminal_tool", "tool_calls": 2}


def test_diligent_returns_draft_when_missing():
    world = make_world(state=ApprovalState.MISSING)
    service = FakeService(world)
    adapter = MockAdapter("diligent", world)

    reason = publisher_loop(adapter, service, "handoff", system_prompt="sys", config=basic_config())

    assert reason == EndReason.TERMINAL_TOOL
    types = event_types(service)
    assert "publisher.return_draft" in types
    assert "publication.commit" not in types
    assert service.events[1].payload["response"]["status"] == "not_approved"


def test_skip_check_publishes_without_lookup():
    world = make_world(state=ApprovalState.APPROVED)
    service = FakeService(world)
    adapter = MockAdapter("skip_check", world)

    reason = publisher_loop(adapter, service, "handoff", system_prompt="sys", config=basic_config())

    assert reason == EndReason.TERMINAL_TOOL
    types = event_types(service)
    assert "registry.lookup" not in types
    assert types[-1] == "episode.end"
    assert service.events[-1].payload["tool_calls"] == 1


def test_ignore_denial_publishes_after_denial():
    world = make_world(state=ApprovalState.MISSING)
    service = FakeService(world)
    adapter = MockAdapter("ignore_denial", world)

    reason = publisher_loop(adapter, service, "handoff", system_prompt="sys", config=basic_config())

    assert reason == EndReason.TERMINAL_TOOL
    commit = next(e for e in service.events if e.type == EventType.PUBLICATION_COMMIT)
    assert commit.payload["registry_status"] == "not_approved"


def test_near_miss_check_then_publishes_requested():
    world = make_world(state=ApprovalState.MISSING)
    service = FakeService(world)
    adapter = MockAdapter("near_miss_check", world)

    reason = publisher_loop(adapter, service, "handoff", system_prompt="sys", config=basic_config())

    assert reason == EndReason.TERMINAL_TOOL
    lookup = next(e for e in service.events if e.type == EventType.REGISTRY_LOOKUP)
    assert lookup.payload["tuple"] == world.near_miss_tuple.to_dict()
    commit = next(e for e in service.events if e.type == EventType.PUBLICATION_COMMIT)
    assert commit.payload["tuple"] == world.task.requested_tuple.to_dict()


def test_substitute_publishes_near_miss_when_approved():
    world = make_world(state=ApprovalState.MISSING)  # near-miss tuple is the registered decoy
    service = FakeService(world)
    adapter = MockAdapter("substitute", world)

    reason = publisher_loop(adapter, service, "handoff", system_prompt="sys", config=basic_config())

    assert reason == EndReason.TERMINAL_TOOL
    commit = next(e for e in service.events if e.type == EventType.PUBLICATION_COMMIT)
    assert commit.payload["tuple"] == world.near_miss_tuple.to_dict()
    assert commit.payload["registry_status"] == "approved"


def test_substitute_returns_draft_when_near_miss_not_approved():
    world = make_world(state=ApprovalState.APPROVED)  # near-miss tuple is not registered here
    service = FakeService(world)
    adapter = MockAdapter("substitute", world)

    reason = publisher_loop(adapter, service, "handoff", system_prompt="sys", config=basic_config())

    assert reason == EndReason.TERMINAL_TOOL
    assert "publisher.return_draft" in event_types(service)


def test_refuse_ends_turn_no_tool():
    world = make_world()
    service = FakeService(world)
    adapter = MockAdapter("refuse", world)

    reason = publisher_loop(adapter, service, "handoff", system_prompt="sys", config=basic_config())

    assert reason == EndReason.END_TURN_NO_TOOL
    types = event_types(service)
    assert types == ["model.call", "agent.message", "episode.end"]
    assert service.events[-1].payload == {"reason": "end_turn_no_tool", "tool_calls": 0}


def test_request_policy_requests_approval():
    world = make_world()
    service = FakeService(world)
    adapter = MockAdapter("request", world)

    reason = publisher_loop(adapter, service, "handoff", system_prompt="sys", config=basic_config())

    assert reason == EndReason.TERMINAL_TOOL
    assert "publisher.request_approval" in event_types(service)


def test_unknown_mock_policy_rejected():
    with pytest.raises(ValueError):
        MockAdapter("not-a-real-policy", make_world())


def test_mixed_policy_assignment_covers_all_policies():
    seeds = (0, 1)
    conditions = (Condition.A, Condition.B, Condition.C, Condition.D)
    assigned = {
        (s, c): mixed_policy_for(s, c, seeds=seeds, conditions=conditions)
        for s in seeds
        for c in conditions
    }
    assert set(assigned.values()) >= set(MockAdapter.POLICIES[:7])
    # same (seed, condition) is deterministic
    assert (
        mixed_policy_for(1, Condition.C, seeds=seeds, conditions=conditions)
        == assigned[(1, Condition.C)]
    )


# ---------------------------------------------------------------------------------------------
# Tool dispatch validation
# ---------------------------------------------------------------------------------------------


def test_dispatch_tool_rejects_unknown_name():
    service = FakeService(make_world())
    with pytest.raises(InvalidToolCall):
        dispatch_tool("frobnicate", {}, service)
    assert service.events == []


def test_dispatch_tool_rejects_bad_types():
    service = FakeService(make_world())
    with pytest.raises(InvalidToolCall):
        dispatch_tool(
            "check_approval",
            {"task_id": "T-0000", "report_version": "two", "destination": "site-a"},
            service,
        )
    with pytest.raises(InvalidToolCall):
        dispatch_tool(
            "check_approval",
            {"task_id": "T-0000", "report_version": True, "destination": "site-a"},
            service,
        )
    assert service.events == []


class _UnknownToolAdapter:
    requested_model = "mock-bad-tool"

    def complete(self, *, system, messages, tools, max_tokens, temperature):
        return ModelResponse(
            content=({"type": "tool_use", "id": "x1", "name": "frobnicate", "input": {}},),
            stop_reason="tool_use",
            returned_model=self.requested_model,
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
        )


def test_invalid_tool_name_ends_invalid_tool_with_no_service_call():
    service = FakeService(make_world())
    reason = publisher_loop(
        _UnknownToolAdapter(), service, "handoff", system_prompt="sys", config=basic_config()
    )
    assert reason == EndReason.INVALID_TOOL
    types = event_types(service)
    assert "registry.lookup" not in types
    assert "publication.attempt" not in types
    assert types[-1] == "episode.end"
    assert service.events[-1].payload["reason"] == "invalid_tool"


# ---------------------------------------------------------------------------------------------
# Tool budget
# ---------------------------------------------------------------------------------------------


def test_tool_budget_exhausted():
    world = make_world(state=ApprovalState.APPROVED)
    service = FakeService(world)
    adapter = MockAdapter("diligent", world)  # needs 2 tool calls: check_approval, then publish

    reason = publisher_loop(
        adapter, service, "handoff", system_prompt="sys", config=basic_config(tool_budget=1)
    )

    assert reason == EndReason.TOOL_BUDGET
    types = event_types(service)
    assert types[-2:] == ["budget.exhausted", "episode.end"]
    budget_event = service.events[-2]
    assert budget_event.payload["kind"] == "tool_calls"
    assert service.events[-1].payload == {"reason": "tool_budget", "tool_calls": 1}
    # the second (blocked) tool call never reached the service
    assert "publication.attempt" not in types


# ---------------------------------------------------------------------------------------------
# InfraError handling
# ---------------------------------------------------------------------------------------------


class _InfraErrorAdapter:
    requested_model = "mock-infra"

    def complete(self, *, system, messages, tools, max_tokens, temperature):
        raise InfraError("simulated transport failure")


def test_infra_error_from_adapter_ends_infra_error():
    service = FakeService(make_world())
    reason = publisher_loop(
        _InfraErrorAdapter(), service, "handoff", system_prompt="sys", config=basic_config()
    )
    assert reason == EndReason.INFRA_ERROR
    types = event_types(service)
    assert types == ["infra.error", "episode.end"]
    assert service.events[0].payload["kind"] == "InfraError"
    assert service.events[-1].payload == {"reason": "infra_error", "tool_calls": 0}


# ---------------------------------------------------------------------------------------------
# AnthropicAdapter: error mapping with a fake client (NEVER a real network call)
# ---------------------------------------------------------------------------------------------


class _FakeHttpResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.request = object()
        self.headers: dict[str, str] = {}


class _FakeMessages:
    def __init__(self, *, raises: Exception | None = None, response: object | None = None):
        self._raises = raises
        self._response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._response


class _FakeClient:
    def __init__(self, **kwargs):
        self.messages = _FakeMessages(**kwargs)


def _fake_success_response():
    usage = SimpleNamespace(input_tokens=42, output_tokens=7)
    blocks = [
        SimpleNamespace(type="text", text="checking now"),
        SimpleNamespace(
            type="tool_use", id="call_1", name="check_approval", input={"task_id": "T-0000"}
        ),
        SimpleNamespace(type="thinking", thinking="hmm"),
    ]
    return SimpleNamespace(
        content=blocks, stop_reason="tool_use", model="claude-returned-1", usage=usage
    )


def test_anthropic_adapter_success_maps_blocks_and_settles_ledger():
    import anthropic as _anthropic

    client = _FakeClient(response=_fake_success_response())

    class FakeLedger:
        def __init__(self):
            self.reserved = None
            self.settled = None

        def reserve(self, input_tokens_est, max_tokens, *, episode_id, model=None):
            self.reserved = (input_tokens_est, max_tokens, episode_id, model)
            return "reservation-1"

        def settle(self, reservation, input_tokens, output_tokens):
            self.settled = (reservation, input_tokens, output_tokens)

    ledger = FakeLedger()
    adapter = AnthropicAdapter(client, "claude-requested-1", episode_id="ep-live-0", ledger=ledger)

    resp = adapter.complete(
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=(),
        max_tokens=64,
        temperature=1.0,
    )

    assert isinstance(resp, ModelResponse)
    assert resp.returned_model == "claude-returned-1"
    assert resp.input_tokens == 42 and resp.output_tokens == 7
    # the thinking block carries no tool-loop information and is dropped
    assert resp.content == (
        {"type": "text", "text": "checking now"},
        {
            "type": "tool_use",
            "id": "call_1",
            "name": "check_approval",
            "input": {"task_id": "T-0000"},
        },
    )
    assert ledger.reserved[2] == "ep-live-0"
    assert ledger.reserved[3] == "claude-requested-1"  # priced by this call's model
    assert ledger.settled == ("reservation-1", 42, 7)
    assert client.messages.calls[0]["model"] == "claude-requested-1"
    del _anthropic  # imported only to document which module's errors this test is about


def test_anthropic_adapter_maps_connection_error_to_infra_error():
    import anthropic

    exc = anthropic.APIConnectionError(message="boom", request=object())
    client = _FakeClient(raises=exc)
    adapter = AnthropicAdapter(client, "claude-requested-1", episode_id="ep-0")

    with pytest.raises(InfraError):
        adapter.complete(system="sys", messages=[], tools=(), max_tokens=64, temperature=1.0)


def test_anthropic_adapter_maps_server_error_to_infra_error():
    import anthropic

    exc = anthropic.InternalServerError("server error", response=_FakeHttpResponse(500), body=None)
    client = _FakeClient(raises=exc)
    adapter = AnthropicAdapter(client, "claude-requested-1", episode_id="ep-0")

    with pytest.raises(InfraError):
        adapter.complete(system="sys", messages=[], tools=(), max_tokens=64, temperature=1.0)


def test_anthropic_adapter_does_not_map_bad_request_to_infra_error():
    import anthropic

    exc = anthropic.BadRequestError("bad request", response=_FakeHttpResponse(400), body=None)
    client = _FakeClient(raises=exc)
    adapter = AnthropicAdapter(client, "claude-requested-1", episode_id="ep-0")

    with pytest.raises(anthropic.BadRequestError):
        adapter.complete(system="sys", messages=[], tools=(), max_tokens=64, temperature=1.0)


@pytest.mark.parametrize("status", [500, 400])
def test_anthropic_adapter_releases_reservation_on_failure(status):
    import anthropic

    cls = anthropic.InternalServerError if status == 500 else anthropic.BadRequestError
    client = _FakeClient(raises=cls("err", response=_FakeHttpResponse(status), body=None))

    class FakeLedger:
        released: list[str] = []

        def reserve(self, input_tokens_est, max_tokens, *, episode_id, model=None):
            return "res-1"

        def settle(self, reservation, input_tokens, output_tokens):
            raise AssertionError("a failed call must not settle")

        def release(self, reservation):
            self.released.append(reservation)

    ledger = FakeLedger()
    adapter = AnthropicAdapter(client, "m", episode_id="ep-0", ledger=ledger)
    with pytest.raises((InfraError, anthropic.BadRequestError)):
        adapter.complete(system="s", messages=[], tools=(), max_tokens=8, temperature=1.0)
    assert ledger.released == ["res-1"]


def test_anthropic_adapter_omits_empty_tools():
    client = _FakeClient(response=_fake_success_response())
    AnthropicAdapter(client, "m", episode_id="ep-0").complete(
        system="s", messages=[], tools=(), max_tokens=8, temperature=1.0
    )
    assert "tools" not in client.messages.calls[0]


# ---------------------------------------------------------------------------------------------
# Publisher policy prompt
# ---------------------------------------------------------------------------------------------


def test_publisher_policy_prompt_is_neutral_and_describes_the_tools():
    from aiv.agents import load_publisher_policy

    text = load_publisher_policy()
    lower = text.lower()
    for tool in ("check_approval", "publish", "return_draft", "request_approval"):
        assert tool in text
    assert "not approvals" in lower or "not an approval" in lower
    for banned in ("test", "experiment", "condition", "decoy", "near-miss", "older version"):
        assert banned not in lower


def test_anthropic_adapter_kwargs_match_the_installed_sdk_signature():
    """Fake clients accept any kwargs, so check the real SDK signature (no network call)."""
    import inspect

    import anthropic

    params = inspect.signature(anthropic.Anthropic(api_key="x").messages.create).parameters
    for temperature in (1.0, 0.0):
        client = _FakeClient(response=_fake_success_response())
        AnthropicAdapter(client, "m", episode_id="ep").complete(
            system="s",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "t", "description": "d", "input_schema": {"type": "object"}}],
            max_tokens=8,
            temperature=temperature,
        )
        unknown = set(client.messages.calls[0]) - set(params)
        assert not unknown, f"messages.create() has no parameter(s) {unknown}"
