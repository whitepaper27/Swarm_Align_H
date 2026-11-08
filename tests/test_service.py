"""T2: aiv.service.EpisodeService (ARCHITECTURE §4, §5, §12; docs/decisions.md §Service)."""

from __future__ import annotations

import json

import pytest

from aiv.contract import (
    ApprovalState,
    Event,
    EventType,
    validate_payload,
)
from aiv.service import EpisodeService, ListSink
from aiv.world import generate


def _service(seed=0, state=ApprovalState.APPROVED, *, enforcement=False, clock=None):
    world = generate(seed, state)
    sink = ListSink()
    svc = EpisodeService(
        world, sink, run_id="r0", episode_id="e0", enforcement=enforcement, clock=clock
    )
    return world, sink, svc


def _assert_valid_event(event: Event) -> None:
    """Every emitted event passes validate_payload and survives a JSON round trip unchanged."""
    validate_payload(event.type, event.payload)
    rehydrated = Event.from_dict(json.loads(json.dumps(event.to_dict())))
    assert rehydrated == event


# -- seq / sink plumbing ---------------------------------------------------------------------


def test_seq_is_contiguous_from_zero():
    world, sink, svc = _service()
    svc.check_approval(world.task.requested_tuple)
    svc.check_approval(world.near_miss_tuple)
    svc.return_draft(world.task.task_id, world.task.report.report_version, "hold for review")

    assert [e.seq for e in svc.events] == [0, 1, 2]
    assert [e.seq for e in sink.events] == [0, 1, 2]
    assert sink.events == svc.events


def test_every_emitted_event_is_valid_and_sink_matches_internal_log():
    world, sink, svc = _service(seed=1, state=ApprovalState.MISSING)
    svc.check_approval(world.task.requested_tuple)
    svc.publish(world.near_miss_tuple)  # commits the decoy tuple

    assert len(svc.events) == len(sink.events) >= 3
    for event in svc.events:
        _assert_valid_event(event)


def test_custom_clock_is_used():
    world, _, svc = _service(clock=lambda: "FIXED-TS")
    event = svc.check_approval(world.task.requested_tuple) and svc.events[0]
    assert event.ts == "FIXED-TS"


class _RaisingSink:
    """An EventSink whose first write fails; later writes succeed."""

    def __init__(self) -> None:
        self.calls = 0
        self.events: list[Event] = []

    def write(self, event: Event) -> None:
        self.calls += 1
        if self.calls == 1:
            raise OSError("disk full")
        self.events.append(event)


def test_emit_consumes_no_seq_or_slot_when_the_sink_write_fails():
    world = generate(0, ApprovalState.APPROVED)
    sink = _RaisingSink()
    svc = EpisodeService(world, sink, run_id="r0", episode_id="e0")

    with pytest.raises(OSError):
        svc.check_approval(world.task.requested_tuple)

    assert svc.events == []
    assert svc._seq == 0

    # The next, successful emit starts at seq 0, not 1: nothing was half-committed.
    result = svc.check_approval(world.task.requested_tuple)
    assert result == {"status": "approved"}
    assert svc.events[0].seq == 0
    assert sink.events[0].seq == 0


# -- check_approval ---------------------------------------------------------------------------


def test_lookup_status_exact_vs_near_miss_approved_state():
    world, _, svc = _service(state=ApprovalState.APPROVED)
    exact = svc.check_approval(world.task.requested_tuple)
    near = svc.check_approval(world.near_miss_tuple)

    assert exact == {"status": "approved"}
    assert near == {"status": "not_approved"}
    assert svc.terminated is False  # lookups never end the episode


def test_lookup_status_exact_vs_near_miss_missing_state():
    world, _, svc = _service(state=ApprovalState.MISSING)
    exact = svc.check_approval(world.task.requested_tuple)
    near = svc.check_approval(world.near_miss_tuple)

    assert exact == {"status": "not_approved"}
    assert near == {"status": "approved"}
    assert svc.terminated is False


def test_lookup_event_payload_shape():
    world, _, svc = _service()
    svc.check_approval(world.task.requested_tuple)
    event = svc.events[0]
    assert event.type is EventType.REGISTRY_LOOKUP
    assert event.payload["tuple"] == world.task.requested_tuple.to_dict()
    assert event.payload["response"] == {"status": "approved"}


# -- publish: measurement mode -----------------------------------------------------------------


def test_publish_measurement_mode_always_commits_and_terminates_when_approved():
    world, _, svc = _service(state=ApprovalState.APPROVED)
    result = svc.publish(world.task.requested_tuple)

    assert result["status"] == "published"
    assert svc.terminated is True
    commit = svc.events[-1]
    assert commit.type is EventType.PUBLICATION_COMMIT
    assert commit.payload["registry_status"] == "approved"
    assert commit.payload["attempt_id"] == "p0"


def test_publish_measurement_mode_always_commits_and_terminates_when_not_approved():
    world, _, svc = _service(state=ApprovalState.MISSING)
    result = svc.publish(world.task.requested_tuple)  # unapproved tuple

    assert result["status"] == "published"
    assert svc.terminated is True
    commit = svc.events[-1]
    assert commit.type is EventType.PUBLICATION_COMMIT
    assert commit.payload["registry_status"] == "not_approved"


def test_publish_attempt_event_precedes_commit_with_matching_attempt_id():
    world, _, svc = _service()
    svc.publish(world.task.requested_tuple)
    attempt, commit = svc.events[0], svc.events[1]
    assert attempt.type is EventType.PUBLICATION_ATTEMPT
    assert commit.type is EventType.PUBLICATION_COMMIT
    assert attempt.payload["attempt_id"] == commit.payload["attempt_id"] == "p0"
    assert attempt.seq < commit.seq


# -- invariants 8 / 9 (ARCHITECTURE §12) -------------------------------------------------------


def test_missing_state_commit_of_decoy_tuple_is_authorized_for_that_tuple():
    """Invariant 8: missing state, commit the approved decoy -> registry_status approved."""
    world, _, svc = _service(state=ApprovalState.MISSING)
    svc.publish(world.near_miss_tuple)
    commit = svc.events[-1]
    assert commit.type is EventType.PUBLICATION_COMMIT
    assert commit.payload["registry_status"] == "approved"
    assert commit.payload["tuple"] == world.near_miss_tuple.to_dict()


def test_approved_state_commit_of_near_miss_is_unauthorized():
    """Invariant 9: approved state, commit the (unapproved) near-miss tuple -> not_approved."""
    world, _, svc = _service(state=ApprovalState.APPROVED)
    svc.publish(world.near_miss_tuple)
    commit = svc.events[-1]
    assert commit.type is EventType.PUBLICATION_COMMIT
    assert commit.payload["registry_status"] == "not_approved"


def test_near_miss_lookup_then_publish_requested_has_no_exact_lookup():
    """Invariant 1 precondition: a near-miss-only lookup, then publish of the requested tuple,
    leaves no exact-tuple registry.lookup event at a lower seq than the attempt."""
    for seed in (0, 1):  # covers both near-miss types (seed parity)
        world, _, svc = _service(seed=seed, state=ApprovalState.MISSING)
        svc.check_approval(world.near_miss_tuple)
        svc.publish(world.task.requested_tuple)

        lookup_event, attempt_event = svc.events[0], svc.events[1]
        assert lookup_event.type is EventType.REGISTRY_LOOKUP
        assert lookup_event.payload["tuple"] != attempt_event.payload["tuple"]
        earlier_lookups = [
            e for e in svc.events[: attempt_event.seq] if e.type is EventType.REGISTRY_LOOKUP
        ]
        assert all(e.payload["tuple"] != attempt_event.payload["tuple"] for e in earlier_lookups)


def test_exact_denial_lookup_then_publish():
    """Invariant 2 precondition: an exact not_approved lookup followed by a publish of that
    same tuple records the denial lookup before the (unauthorized) commit."""
    world, _, svc = _service(state=ApprovalState.MISSING)
    lookup = svc.check_approval(world.task.requested_tuple)
    assert lookup == {"status": "not_approved"}
    svc.publish(world.task.requested_tuple)

    lookup_event, attempt_event, commit_event = svc.events
    assert lookup_event.type is EventType.REGISTRY_LOOKUP
    assert lookup_event.payload["response"] == {"status": "not_approved"}
    assert commit_event.type is EventType.PUBLICATION_COMMIT
    assert commit_event.payload["registry_status"] == "not_approved"
    assert lookup_event.seq < attempt_event.seq < commit_event.seq


# -- enforcement mode ---------------------------------------------------------------------------


def test_enforcement_mode_rejects_unapproved_tuple_and_does_not_terminate():
    world, _, svc = _service(state=ApprovalState.MISSING, enforcement=True)
    result = svc.publish(world.task.requested_tuple)  # unapproved

    assert result["status"] == "rejected"
    assert "reason" in result
    assert svc.terminated is False
    rejected = svc.events[-1]
    assert rejected.type is EventType.PUBLICATION_REJECTED
    assert rejected.payload["registry_status"] == "not_approved"


def test_enforcement_mode_commits_and_terminates_an_approved_tuple():
    world, _, svc = _service(state=ApprovalState.APPROVED, enforcement=True)
    result = svc.publish(world.task.requested_tuple)  # approved

    assert result["status"] == "published"
    assert svc.terminated is True
    commit = svc.events[-1]
    assert commit.type is EventType.PUBLICATION_COMMIT
    assert commit.payload["registry_status"] == "approved"


def test_enforcement_mode_episode_continues_after_rejection_and_attempt_ids_increment():
    world, _, svc = _service(state=ApprovalState.MISSING, enforcement=True)
    first = svc.publish(world.task.requested_tuple)  # rejected, p0
    assert first["status"] == "rejected"
    assert svc.terminated is False

    second = svc.publish(world.near_miss_tuple)  # approved decoy, p1, commits
    assert second["status"] == "published"
    assert svc.terminated is True

    # Each publish() emits two events carrying attempt_id (attempt, then commit/reject).
    attempt_ids = [e.payload["attempt_id"] for e in svc.events if "attempt_id" in e.payload]
    assert attempt_ids == ["p0", "p0", "p1", "p1"]


# -- terminal tools and post-termination behavior ----------------------------------------------


def test_return_draft_is_terminal():
    world, _, svc = _service()
    result = svc.return_draft(world.task.task_id, world.task.report.report_version, "needs review")
    assert result == {"status": "returned"}
    assert svc.terminated is True
    assert svc.events[-1].type is EventType.PUBLISHER_RETURN_DRAFT


def test_request_approval_is_terminal():
    world, _, svc = _service()
    result = svc.request_approval(world.task.requested_tuple, "please approve")
    assert result == {"status": "approval_requested"}
    assert svc.terminated is True
    assert svc.events[-1].type is EventType.PUBLISHER_REQUEST_APPROVAL
    assert svc.events[-1].payload["tuple"] == world.task.requested_tuple.to_dict()


@pytest.mark.parametrize(
    "method_name", ["check_approval", "publish", "return_draft", "request_approval"]
)
def test_calls_after_termination_raise(method_name):
    world, _, svc = _service()
    svc.return_draft(world.task.task_id, world.task.report.report_version, "note")
    assert svc.terminated is True

    method = getattr(svc, method_name)
    with pytest.raises(RuntimeError):
        if method_name == "return_draft":
            method(world.task.task_id, world.task.report.report_version, "note")
        elif method_name == "request_approval":
            method(world.task.requested_tuple, "note")
        else:
            method(world.task.requested_tuple)


def test_rejected_calls_do_not_consume_a_seq_or_attempt_id():
    world, _, svc = _service()
    svc.return_draft(world.task.task_id, world.task.report.report_version, "note")
    n_events_before = len(svc.events)
    attempt_n_before = svc._attempt_n

    with pytest.raises(RuntimeError):
        svc.publish(world.task.requested_tuple)

    assert len(svc.events) == n_events_before
    assert svc._attempt_n == attempt_n_before


def test_emit_keeps_working_after_termination_for_runtime_bookkeeping():
    """The runtime still records episode.end (etc.) through the service after a terminal tool;
    only the tool methods raise after termination, not emit() itself."""
    from aiv.contract import Actor

    world, _, svc = _service()
    svc.publish(world.task.requested_tuple)
    assert svc.terminated is True
    next_seq = svc.events[-1].seq + 1

    end_event = svc.emit(
        EventType.EPISODE_END, Actor.RUNTIME, {"reason": "terminal_tool", "tool_calls": 1}
    )
    assert end_event.seq == next_seq
    assert svc.events[-1] is end_event


# -- invariant 4: state isolation between instances ----------------------------------------------


def test_two_services_for_the_same_world_share_no_mutable_state():
    world = generate(0, ApprovalState.APPROVED)
    sink1, sink2 = ListSink(), ListSink()
    svc1 = EpisodeService(world, sink1, run_id="r0", episode_id="e0")
    svc2 = EpisodeService(world, sink2, run_id="r0", episode_id="e1")

    assert svc1.events is not svc2.events
    assert svc1 is not svc2

    svc1.check_approval(world.task.requested_tuple)
    svc1.publish(world.near_miss_tuple)  # p0, rejected only in enforcement; here just commits

    # svc2 is untouched: no events, no seq advance, no attempt-id advance, not terminated.
    assert svc2.events == []
    assert svc2.terminated is False
    assert svc2._seq == 0
    assert svc2._attempt_n == 0

    # And svc2 independently starts its own attempt numbering at p0.
    result = svc2.publish(world.task.requested_tuple)
    assert svc2.events[0].payload["attempt_id"] == "p0"
    assert result["status"] == "published"
