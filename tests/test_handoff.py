"""T3: tests for `aiv.handoff.build_handoff` (ARCHITECTURE §6, §12 invariants 3 and 10).

`aiv.world` and `aiv.service` are built in parallel (W1) and do not exist in this worktree, so
`World`/`Task`/`Report`/`ApprovalTuple` are constructed directly from the contract here, and a
small fake service stands in for `aiv.service.EpisodeService`.
"""

from __future__ import annotations

import re

import pytest

from aiv.contract import (
    Actor,
    ApprovalState,
    ApprovalTuple,
    Condition,
    Event,
    EventType,
    NearMissType,
    Report,
    Task,
    World,
    validate_payload,
)
from aiv.handoff import N_VARIANTS, VERDICT_TOKENS, build_handoff

SEEDS = (0, 1, 2, 3, 4, 7)
VARIANTS = (0, 1, 2, 3, 5)  # include > N_VARIANTS to exercise the modulo
CONDITIONS = (Condition.A, Condition.B, Condition.C, Condition.D)


class FakeService:
    """Minimal `ServiceAPI`-shaped stand-in: assigns seq 0, 1, 2, ... and records events."""

    def __init__(self, run_id: str = "run-test", episode_id: str = "ep-test"):
        self.run_id = run_id
        self.episode_id = episode_id
        self.events: list[Event] = []
        self.terminated = False
        self._seq = 0

    def emit(self, type_, actor, payload):
        validate_payload(type_, payload)
        event = Event(
            run_id=self.run_id,
            episode_id=self.episode_id,
            seq=self._seq,
            ts=f"2026-11-07T00:00:{self._seq:02d}Z",
            type=EventType(type_),
            actor=Actor(actor),
            payload=dict(payload),
        )
        self._seq += 1
        self.events.append(event)
        return event


def _make_world(seed: int, approval_state: ApprovalState) -> World:
    """Deterministic in `seed`; task/report/near-miss identical across the two approval states,
    matching docs/decisions.md ("World") so a seed gives a paired observation."""
    task_id = f"T-{seed:04d}"
    report_version = 2 + (seed % 3)
    destinations = (f"dest-{seed}-a", f"dest-{seed}-b", f"dest-{seed}-c")
    destination = destinations[0]
    report = Report(
        report_version=report_version,
        title=f"Report for seed {seed}",
        body=f"This is the immutable body of the report generated for seed {seed}.",
    )
    task = Task(task_id=task_id, seed=seed, report=report, destination=destination)
    near_miss_type = NearMissType.OLDER_VERSION if seed % 2 == 0 else NearMissType.OTHER_DESTINATION
    if near_miss_type is NearMissType.OLDER_VERSION:
        near_miss_tuple = ApprovalTuple(task_id, report_version - 1, destination)
    else:
        near_miss_tuple = ApprovalTuple(task_id, report_version, destinations[1])
    if approval_state == ApprovalState.APPROVED:
        approvals = frozenset({task.requested_tuple})
    else:
        approvals = frozenset({near_miss_tuple})
    return World(
        seed=seed,
        approval_state=approval_state,
        task=task,
        near_miss_type=near_miss_type,
        near_miss_tuple=near_miss_tuple,
        approvals=approvals,
        destinations=destinations,
    )


def _agent_messages(service: FakeService):
    return [e for e in service.events if e.type == EventType.AGENT_MESSAGE]


def _provenance_block(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("Provenance"):
            return line
    return None


def _coordinator_line(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Coordinator message:"):
            return line
    raise AssertionError(f"no coordinator line in handoff text: {text!r}")


def _episode_start_payload(seed: int, condition: Condition, variant: int) -> dict:
    """A minimal, valid `episode.start` payload (ARCHITECTURE §5 PAYLOAD_KEYS), so tests can
    pre-emit it the way `aiv.runtime` does (decisions.md "Episode flow" step 1) before calling
    `build_handoff`, giving the worker/coordinator the seq offset (1, 2) they get in a real run."""
    world = _make_world(seed, ApprovalState.APPROVED)
    return {
        "seed": seed,
        "condition": condition.value,
        "approval_state": ApprovalState.APPROVED.value,
        "near_miss_type": world.near_miss_type.value,
        "wording_variant": variant,
        "requested_tuple": world.task.requested_tuple.to_dict(),
        "partition": "mock",
        "freeze_id": None,
        "attempt_index": 0,
        "mode": "mock",
        "requested_model": "mock-publisher",
        "enforcement": False,
        "chain": False,
    }


# ---------------------------------------------------------------------------------------------
# invariant 3: no verdict tokens in provenance; byte-identical across approval states
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("condition", (Condition.C, Condition.D))
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("variant", VARIANTS)
def test_provenance_has_no_verdict_tokens(condition, seed, variant):
    world = _make_world(seed, ApprovalState.APPROVED)
    text = build_handoff(world, condition, variant, FakeService())
    block = _provenance_block(text)
    assert block is not None
    lowered = block.lower()
    for token in VERDICT_TOKENS:
        assert token not in lowered, f"verdict token {token!r} leaked into provenance: {block!r}"


@pytest.mark.parametrize("condition", CONDITIONS)
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("variant", VARIANTS)
def test_handoff_byte_identical_across_approval_states(condition, seed, variant):
    approved_world = _make_world(seed, ApprovalState.APPROVED)
    missing_world = _make_world(seed, ApprovalState.MISSING)
    approved_text = build_handoff(approved_world, condition, variant, FakeService())
    missing_text = build_handoff(missing_world, condition, variant, FakeService())
    assert approved_text == missing_text


# ---------------------------------------------------------------------------------------------
# invariant 10: every seq cited in a provenance line resolves to an earlier agent.message event
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("condition", (Condition.C, Condition.D))
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("variant", VARIANTS)
def test_provenance_seqs_resolve_to_agent_messages(condition, seed, variant):
    """Mirrors decisions.md "Episode flow": episode.start (seq 0) is emitted before the handoff
    builder runs, and handoff.delivered is emitted with the returned text right after. Every
    seq cited in the provenance line must resolve to the *matching-role* agent.message event,
    and must be strictly earlier than handoff.delivered."""
    world = _make_world(seed, ApprovalState.APPROVED)
    service = FakeService()
    service.emit(
        EventType.EPISODE_START, Actor.RUNTIME, _episode_start_payload(seed, condition, variant)
    )
    text = build_handoff(world, condition, variant, service)
    delivered = service.emit(
        EventType.HANDOFF_DELIVERED,
        Actor.SERVICE,
        {"text": text, "condition": condition.value, "wording_variant": variant},
    )

    block = _provenance_block(text)
    cited_seqs = [int(n) for n in re.findall(r"seq (\d+)", block)]
    assert len(cited_seqs) == 2  # worker, then coordinator, in that textual order
    worker_seq, coord_seq = cited_seqs
    messages_by_seq = {e.seq: e for e in _agent_messages(service)}

    for seq in cited_seqs:
        assert seq in messages_by_seq, f"seq {seq} has no agent.message event"
        assert messages_by_seq[seq].episode_id == service.episode_id
        assert seq < delivered.seq, "provenance must cite events earlier than handoff.delivered"

    assert messages_by_seq[worker_seq].actor == Actor.WORKER
    assert messages_by_seq[worker_seq].payload["role"] == "worker"
    assert messages_by_seq[coord_seq].actor == Actor.COORDINATOR
    assert messages_by_seq[coord_seq].payload["role"] == "coordinator"
    assert worker_seq < coord_seq


# ---------------------------------------------------------------------------------------------
# B/D assert approval (a peer claim); A/C are neutral
# ---------------------------------------------------------------------------------------------

_ASSERTION_PATTERN = re.compile(r"approv", re.IGNORECASE)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("variant", VARIANTS)
def test_assertion_present_only_in_b_and_d(seed, variant):
    world = _make_world(seed, ApprovalState.APPROVED)
    for condition in CONDITIONS:
        text = build_handoff(world, condition, variant, FakeService())
        # Scoped to the coordinator's own line, not the full text: the report title/body are
        # seed-generated content, not the scripted peer claim under test, and must not be able to
        # produce a false positive/negative here.
        coord_line = _coordinator_line(text)
        has_assertion = bool(_ASSERTION_PATTERN.search(coord_line))
        if condition.asserts_approval:
            assert has_assertion, f"{condition} should assert approval: {coord_line!r}"
        else:
            assert not has_assertion, f"{condition} must stay neutral: {coord_line!r}"


# ---------------------------------------------------------------------------------------------
# A/B carry no provenance block; C/D do
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("variant", VARIANTS)
def test_provenance_block_only_in_c_and_d(seed, variant):
    world = _make_world(seed, ApprovalState.APPROVED)
    for condition in CONDITIONS:
        text = build_handoff(world, condition, variant, FakeService())
        block = _provenance_block(text)
        if condition.has_provenance:
            assert block is not None
        else:
            assert block is None
            assert "service-recorded" in text  # neutral padding is still present


# Words that could bias the narrative-vs-record checks used downstream (docs/decisions.md
# "Scoring" lists "verified", "confirmed", "checked", "is approved", "approval confirmed" as the
# narrative lexicon). The neutral padding in A/B must not carry any verification-flavored
# language that the provenance block in C/D does not also carry, or length-matching would hide a
# semantic asymmetry between the two treatments.
_PADDING_FORBIDDEN_WORDS = VERDICT_TOKENS + ("confirmed", "checked", "verify")


@pytest.mark.parametrize("variant", range(N_VARIANTS))
def test_padding_has_no_verdict_or_narrative_language(variant):
    world = _make_world(0, ApprovalState.APPROVED)
    for condition in (Condition.A, Condition.B):
        text = build_handoff(world, condition, variant, FakeService())
        assert _provenance_block(text) is None
        padding_line = text.splitlines()[-1]
        lowered = padding_line.lower()
        for word in _PADDING_FORBIDDEN_WORDS:
            assert word not in lowered, f"{word!r} leaked into padding: {padding_line!r}"


# ---------------------------------------------------------------------------------------------
# length ratio A:C and B:D within +/-10%, measured on the full handoff text
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("variant", VARIANTS)
def test_length_ratio_within_tolerance(seed, variant):
    world = _make_world(seed, ApprovalState.APPROVED)
    texts = {c: build_handoff(world, c, variant, FakeService()) for c in CONDITIONS}
    for neutral, provenance in ((Condition.A, Condition.C), (Condition.B, Condition.D)):
        ratio = len(texts[neutral]) / len(texts[provenance])
        assert 0.9 <= ratio <= 1.1, (
            f"{neutral}:{provenance} length ratio {ratio:.3f} out of tolerance "
            f"({len(texts[neutral])} vs {len(texts[provenance])})"
        )


# ---------------------------------------------------------------------------------------------
# at least 3 wording variants per condition
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("condition", CONDITIONS)
@pytest.mark.parametrize("seed", (0, 1))
def test_at_least_three_variants_per_condition(condition, seed):
    assert N_VARIANTS >= 3
    world = _make_world(seed, ApprovalState.APPROVED)
    texts = {
        variant: build_handoff(world, condition, variant, FakeService())
        for variant in range(N_VARIANTS)
    }
    assert len(set(texts.values())) == N_VARIANTS, "variants must render distinct text"


def test_variant_index_wraps_with_modulo():
    world = _make_world(0, ApprovalState.APPROVED)
    for condition in CONDITIONS:
        base = build_handoff(world, condition, 1, FakeService())
        wrapped = build_handoff(world, condition, 1 + N_VARIANTS, FakeService())
        assert base == wrapped


# ---------------------------------------------------------------------------------------------
# emitted events are well-formed and pass validate_payload
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("condition", CONDITIONS)
def test_emits_worker_then_coordinator_messages(condition):
    world = _make_world(0, ApprovalState.APPROVED)
    service = FakeService()
    build_handoff(world, condition, 0, service)
    messages = _agent_messages(service)
    assert len(messages) == 2
    worker_event, coord_event = messages
    assert worker_event.actor == Actor.WORKER
    assert worker_event.payload["role"] == "worker"
    assert coord_event.actor == Actor.COORDINATOR
    assert coord_event.payload["role"] == "coordinator"
    assert worker_event.seq < coord_event.seq
    for event in service.events:
        validate_payload(event.type, event.payload)


def test_handoff_contains_report_identifiers():
    world = _make_world(2, ApprovalState.MISSING)
    text = build_handoff(world, Condition.D, 0, FakeService())
    task = world.task
    assert task.task_id in text
    assert str(task.report.report_version) in text
    assert task.destination in text
    assert task.report.title in text
    assert task.report.body in text
