"""Episode + batch runner (ARCHITECTURE §3 `runtime`, §8 data flow, §9 run files, §11 retries).

Built with dependency injection (`Deps`): the four callables that reach into modules owned by
other worktrees (`world`, `service`, `handoff`, `scoring`) default to small wrappers that import
the real module lazily, inside the function body, so this package imports and its tests run
before those modules exist. Tests inject fakes that follow `contract.py` + `docs/decisions.md`.
"""

from __future__ import annotations

import json
import secrets
import subprocess
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiv.agents import load_publisher_policy, publisher_loop, publisher_policy_path
from aiv.contract import (
    MANIFEST_FILE,
    SCORED_FILE,
    TOOL_CHECK_APPROVAL,
    Actor,
    ApprovalState,
    Condition,
    EndReason,
    Event,
    EventSink,
    EventType,
    InfraError,
    RunConfig,
    ScoredEpisode,
    ServiceAPI,
    World,
    canonical_json,
    make_episode_id,
    sha256_hex,
)
from aiv.recorder import JsonlRecorder, new_run_dir, recorder_key
from aiv.spend import SpendRefused

# ---------------------------------------------------------------------------------------------
# Lazy default wiring (the real modules may not exist yet in this worktree/CI run)
# ---------------------------------------------------------------------------------------------


def _default_generate(seed: int, approval_state: ApprovalState) -> World:
    from aiv.world import generate

    return generate(seed, approval_state)


def _default_make_service(
    world: World,
    sink: EventSink,
    run_id: str,
    episode_id: str,
    *,
    enforcement: bool = False,
) -> ServiceAPI:
    from aiv.service import EpisodeService

    return EpisodeService(world, sink, run_id, episode_id, enforcement=enforcement)


def _default_build_handoff(
    world: World, condition: Condition, wording_variant: int, service: ServiceAPI
) -> str:
    from aiv.handoff import build_handoff

    return build_handoff(world, condition, wording_variant, service)


def _default_score(events: Sequence[Event], *, infra_retries: int = 0) -> ScoredEpisode:
    from aiv.scoring import score_episode

    return score_episode(events, infra_retries=infra_retries)


@dataclass
class Deps:
    """Callables the runtime needs from modules owned by other worktrees. Defaults import the
    real module lazily inside the function body (never at import time)."""

    generate: Callable[[int, ApprovalState], World] = _default_generate
    make_service: Callable[..., ServiceAPI] = _default_make_service
    build_handoff: Callable[[World, Condition, int, ServiceAPI], str] = _default_build_handoff
    score: Callable[..., ScoredEpisode] = _default_score


# ---------------------------------------------------------------------------------------------
# Episode spec / adapter factory
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeSpec:
    """Everything a `ModelAdapter` factory needs to build an adapter for one attempt."""

    seed: int
    condition: Condition
    approval_state: ApprovalState
    wording_variant: int
    attempt: int
    episode_id: str
    chain: bool
    run_id: str = ""

    @property
    def spend_key(self) -> str:
        """Ledger key: unique per run, so a rerun never inherits an earlier run's episode spend."""
        return f"{self.run_id}/{self.episode_id}"


# `AdapterFactory(spec, world) -> ModelAdapter` — called once per attempt (fresh adapter, no
# shared state or message history with any other attempt; invariant 4).
AdapterFactory = Callable[[EpisodeSpec, World], Any]


@dataclass(frozen=True)
class AttemptResult:
    episode_id: str
    events: list[Event]
    end_reason: EndReason


@dataclass(frozen=True)
class EpisodeResult:
    scored: ScoredEpisode
    attempts: list[AttemptResult]


# ---------------------------------------------------------------------------------------------
# T13 confirmation experiment: injected exact-denial history (sol.md §6, §14)
# ---------------------------------------------------------------------------------------------

CONFIRM_EXPERIMENT = "confirm"
MODEL_EXTRA_KEYS = ("publisher_provider", "publisher_model", "worker_provider", "worker_model")
# Constant across arms (and seeds), so the two arms' histories differ only in the follow-up text.
INJECTED_TOOL_USE_ID = "toolu_confirm_injected_lookup"


def confirm_arm(condition: Condition) -> str:
    from aiv.handoff import confirm_arm as _arm

    return _arm(condition)


def build_confirm_history(
    world: World,
    condition: Condition,
    wording_variant: int,
    service: ServiceAPI,
    *,
    build_handoff: Callable[[World, Condition, int, ServiceAPI], str],
) -> list[dict[str, Any]]:
    """Emit the confirm episode's pre-publisher events and return the publisher's history.

    1. The neutral condition-A handoff (identical in both arms) and `handoff.delivered`.
    2. A real `service.check_approval` of the requested tuple: the service records a genuine
       `registry.lookup`, so scoring sees the denial. No `model.call` is emitted (the publisher
       model never produced this turn).
    3. The coordinator follow-up as an `agent.message`, emitted before the publisher sees it.

    History: user(handoff) -> assistant(one check_approval tool_use, no text) -> user(tool_result
    with the service's returned dict + the follow-up text block). Only the follow-up text differs
    between the arms.
    """
    from aiv.handoff import FOLLOW_UP_PREFIX, confirm_follow_up

    arm = confirm_arm(condition)
    handoff_text = build_handoff(world, Condition.A, wording_variant, service)
    service.emit(
        EventType.HANDOFF_DELIVERED,
        Actor.SERVICE,
        {
            # The delivered handoff is the condition-A text in both arms; the arm is recorded
            # separately (episode.start carries the episode's condition A|B).
            "text": handoff_text,
            "condition": Condition.A.value,
            "wording_variant": wording_variant,
            "arm": arm,
        },
    )
    requested = world.task.requested_tuple
    lookup_result = service.check_approval(requested)
    follow_up = confirm_follow_up(world, arm, wording_variant)
    service.emit(
        EventType.AGENT_MESSAGE,
        Actor.COORDINATOR,
        {"role": "coordinator", "text": follow_up, "follow_up": True, "arm": arm},
    )
    return [
        {"role": "user", "content": handoff_text},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": INJECTED_TOOL_USE_ID,
                    "name": TOOL_CHECK_APPROVAL,
                    "input": requested.to_dict(),
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": INJECTED_TOOL_USE_ID,
                    "content": canonical_json(lookup_result),
                },
                {"type": "text", "text": FOLLOW_UP_PREFIX + follow_up},
            ],
        },
    ]


# ---------------------------------------------------------------------------------------------
# Single episode (with infra-only retries, state reset per attempt)
# ---------------------------------------------------------------------------------------------


def run_episode(
    *,
    seed: int,
    condition: Condition,
    approval_state: ApprovalState,
    wording_variant: int,
    config: RunConfig,
    deps: Deps,
    adapter_factory: AdapterFactory,
    sink: EventSink,
    run_id: str,
    ledger: Any = None,
) -> EpisodeResult:
    """Run one (seed, condition, approval_state) episode, retrying only on `InfraError`, up to
    `config.retry_max` times. Each attempt gets a fresh `World`, service, adapter and message
    history (invariant 4): nothing is shared between attempts except the `sink`/`run_id`."""
    attempts: list[AttemptResult] = []

    for attempt in range(config.retry_max + 1):
        episode_id = make_episode_id(seed, condition, approval_state, attempt, config.chain)
        world = deps.generate(seed, approval_state)
        service = deps.make_service(world, sink, run_id, episode_id, enforcement=config.enforcement)
        spec = EpisodeSpec(
            seed=seed,
            condition=condition,
            approval_state=approval_state,
            wording_variant=wording_variant,
            attempt=attempt,
            episode_id=episode_id,
            chain=config.chain,
            run_id=run_id,
        )
        # Adapter construction does no I/O (the live adapter only stores a client reference);
        # a crash here is a real bug, not an infra failure, so it is deliberately outside the
        # InfraError-catching try block below.
        adapter = adapter_factory(spec, world)
        requested_model = getattr(adapter, "requested_model", None) or config.model_id

        confirm = config.extra.get("experiment") == CONFIRM_EXPERIMENT
        try:
            start_payload: dict[str, Any] = {
                "seed": seed,
                "condition": condition.value,
                "approval_state": approval_state.value,
                "near_miss_type": world.near_miss_type.value,
                "wording_variant": wording_variant,
                "requested_tuple": world.task.requested_tuple.to_dict(),
                "partition": config.partition.value,
                "freeze_id": config.freeze_id,
                "attempt_index": attempt,
                "mode": config.mode.value,
                "requested_model": requested_model,
                "enforcement": config.enforcement,
                "chain": config.chain,
                "chain_variant": config.extra.get("chain_variant"),
            }
            # Cross-provider extension (sol.md §16): live runs record which provider/model played
            # each role. Copied only when present, so mock and earlier runs are byte-identical.
            for key in MODEL_EXTRA_KEYS:
                if key in config.extra:
                    start_payload[key] = config.extra[key]
            if confirm:
                start_payload["experiment"] = CONFIRM_EXPERIMENT
                start_payload["arm"] = confirm_arm(condition)
            service.emit(EventType.EPISODE_START, Actor.RUNTIME, start_payload)
            system_prompt = load_publisher_policy()
            if confirm:
                history = build_confirm_history(
                    world, condition, wording_variant, service, build_handoff=deps.build_handoff
                )
                end_reason = publisher_loop(
                    adapter,
                    service,
                    history[0]["content"],
                    system_prompt=system_prompt,
                    config=config,
                    ledger=ledger,
                    history=history,
                )
            else:
                handoff_text = deps.build_handoff(world, condition, wording_variant, service)
                service.emit(
                    EventType.HANDOFF_DELIVERED,
                    Actor.SERVICE,
                    {
                        "text": handoff_text,
                        "condition": condition.value,
                        "wording_variant": wording_variant,
                    },
                )
                end_reason = publisher_loop(
                    adapter,
                    service,
                    handoff_text,
                    system_prompt=system_prompt,
                    config=config,
                    ledger=ledger,
                )
        except SpendRefused as exc:
            # A role call inside build_handoff (live chain) hit a cap; the publisher loop handles
            # its own refusals. Recorded as budget exhaustion and never retried (decisions.md).
            service.emit(
                EventType.BUDGET_EXHAUSTED, Actor.RUNTIME, {"kind": "spend", "detail": str(exc)}
            )
            service.emit(
                EventType.EPISODE_END,
                Actor.RUNTIME,
                {"reason": EndReason.SPEND_BUDGET.value, "tool_calls": 0},
            )
            end_reason = EndReason.SPEND_BUDGET
        except InfraError as exc:
            service.emit(
                EventType.INFRA_ERROR,
                Actor.RUNTIME,
                {"kind": type(exc).__name__, "detail": str(exc)},
            )
            service.emit(
                EventType.EPISODE_END,
                Actor.RUNTIME,
                {"reason": EndReason.INFRA_ERROR.value, "tool_calls": 0},
            )
            end_reason = EndReason.INFRA_ERROR

        attempts.append(AttemptResult(episode_id, list(service.events), end_reason))
        if end_reason != EndReason.INFRA_ERROR:
            break

    infra_retries = sum(1 for a in attempts[:-1] if a.end_reason == EndReason.INFRA_ERROR)
    final = attempts[-1]
    scored = deps.score(final.events, infra_retries=infra_retries)
    return EpisodeResult(scored=scored, attempts=attempts)


# ---------------------------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------------------------


def _new_run_id(config: RunConfig) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{config.mode.value}-{config.partition.value}-{ts}-{secrets.token_hex(3)}"


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[3],
            check=True,
            timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def _build_manifest(config: RunConfig, run_id: str) -> dict[str, Any]:
    draft = publisher_policy_path().parent  # protocol/draft/: every prompt and template
    prompt_hashes = {
        q.relative_to(draft).as_posix(): sha256_hex(q.read_bytes())
        for q in sorted(draft.rglob("*"))
        if q.is_file() and q.name != ".gitkeep"
    }
    return {
        "run_id": run_id,
        "freeze_id": config.freeze_id,
        "git_commit": _git_commit(),
        "config": config.to_dict(),
        "prompt_sha256": prompt_hashes,
        "model_ids": {"requested": config.model_id},
        "seeds": list(config.seeds),
        "retry_policy": {"retry_max": config.retry_max, "retried_on": "infra_error"},
    }


def run_batch(
    config: RunConfig,
    deps: Deps,
    adapter_factory: AdapterFactory,
    *,
    run_id: str | None = None,
    ledger: Any = None,
    workers: int = 1,
) -> dict[str, Any]:
    """Iterate seeds × conditions × approval_states, writing
    `runs/<partition>/<run_id>/events.jsonl` + `manifest.json`, then score each episode to
    `scored.jsonl`, then close the recorder."""
    run_id = run_id or _new_run_id(config)
    key, key_id = recorder_key(config.mode)
    run_dir = new_run_dir(config.runs_root, config.partition.value, run_id)
    recorder = JsonlRecorder(run_dir, key, key_id)

    # The manifest is written before the first paid call, so an aborted batch still records it.
    with open(run_dir / MANIFEST_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(_build_manifest(config, run_id), indent=2, sort_keys=True) + "\n")

    results: list[EpisodeResult] = []
    error: BaseException | None = None
    try:
        combos = [
            (seed, condition, approval_state)
            for seed in config.seeds
            for condition in config.conditions
            for approval_state in config.approval_states
        ]

        def _run_one(combo: tuple[int, Condition, ApprovalState]) -> EpisodeResult:
            seed, condition, approval_state = combo
            wording_variant = config.wording_variants[seed % len(config.wording_variants)]
            return run_episode(
                seed=seed,
                condition=condition,
                approval_state=approval_state,
                wording_variant=wording_variant,
                config=config,
                deps=deps,
                adapter_factory=adapter_factory,
                sink=recorder,
                run_id=run_id,
                ledger=ledger,
            )

        if workers and workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_run_one, combo) for combo in combos]
                for fut in as_completed(futures):
                    try:
                        results.append(fut.result())
                    except BaseException as exc:  # abort: stop queued episodes, keep finished
                        error = error or exc
                        for other in futures:
                            other.cancel()
        else:
            for combo in combos:
                results.append(_run_one(combo))
    finally:
        # Completed episodes are always scored, even when the batch aborts part way (a config
        # error, a non-retryable API error): paid work is never lost. Coverage shows the gap.
        scored_sorted = sorted(results, key=lambda r: r.scored.episode_id)
        with open(run_dir / SCORED_FILE, "w", encoding="utf-8", newline="\n") as f:
            for r in scored_sorted:
                f.write(canonical_json(r.scored.to_dict()) + "\n")
        recorder.close()
    if error is not None:
        raise error

    return {"run_id": run_id, "run_dir": str(run_dir), "n_episodes": len(combos)}
