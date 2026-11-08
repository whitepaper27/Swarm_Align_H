"""T2: aiv.world.generate (ARCHITECTURE §2; docs/decisions.md §World)."""

from __future__ import annotations

from aiv.contract import ApprovalState, ApprovalTuple, NearMissType
from aiv.world import generate


def test_generate_is_deterministic_for_the_same_seed():
    a1 = generate(7, ApprovalState.APPROVED)
    a2 = generate(7, ApprovalState.APPROVED)
    assert a1 == a2

    m1 = generate(7, ApprovalState.MISSING)
    m2 = generate(7, ApprovalState.MISSING)
    assert m1 == m2


def test_task_report_and_near_miss_identical_across_approval_states():
    approved = generate(11, ApprovalState.APPROVED)
    missing = generate(11, "missing")  # accepts str too (ApprovalState(...) coercion)

    assert approved.task == missing.task
    assert approved.near_miss_type == missing.near_miss_type
    assert approved.near_miss_tuple == missing.near_miss_tuple
    assert approved.destinations == missing.destinations
    # Only the registry contents differ between the two states.
    assert approved.approvals != missing.approvals


def test_task_id_and_report_version_shape():
    for seed in (0, 1, 2, 5, 42):
        w = generate(seed, ApprovalState.APPROVED)
        assert w.task.task_id == f"T-{seed:04d}"
        assert w.task.report.report_version >= 2  # so an older version exists
        assert len(w.destinations) >= 3
        assert w.task.destination in w.destinations
        assert w.task.report.title
        assert w.task.report.body


def test_requested_and_near_miss_tuple_differ():
    for seed in range(10):
        w = generate(seed, ApprovalState.APPROVED)
        assert w.task.requested_tuple != w.near_miss_tuple


def test_older_version_near_miss_shape():
    w = generate(0, ApprovalState.MISSING)  # seed 0 is even -> older_version
    assert w.near_miss_type is NearMissType.OLDER_VERSION
    assert w.near_miss_tuple.task_id == w.task.task_id
    assert w.near_miss_tuple.destination == w.task.destination
    assert w.near_miss_tuple.report_version == w.task.report.report_version - 1


def test_other_destination_near_miss_shape():
    w = generate(1, ApprovalState.MISSING)  # seed 1 is odd -> other_destination
    assert w.near_miss_type is NearMissType.OTHER_DESTINATION
    assert w.near_miss_tuple.task_id == w.task.task_id
    assert w.near_miss_tuple.report_version == w.task.report.report_version
    assert w.near_miss_tuple.destination != w.task.destination
    assert w.near_miss_tuple.destination in w.destinations


def test_near_miss_allocation_by_seed_parity():
    for seed in range(20):
        w = generate(seed, ApprovalState.APPROVED)
        expected = NearMissType.OLDER_VERSION if seed % 2 == 0 else NearMissType.OTHER_DESTINATION
        assert w.near_miss_type is expected


def test_approvals_content_per_state():
    for seed in range(10):
        approved = generate(seed, ApprovalState.APPROVED)
        missing = generate(seed, ApprovalState.MISSING)

        assert approved.approvals == frozenset({approved.task.requested_tuple})
        assert approved.near_miss_tuple not in approved.approvals

        assert missing.approvals == frozenset({missing.near_miss_tuple})
        assert missing.task.requested_tuple not in missing.approvals
        assert len(missing.approvals) == 1
        assert len(approved.approvals) == 1


def test_is_approved_helper():
    approved = generate(3, ApprovalState.APPROVED)
    missing = generate(3, ApprovalState.MISSING)

    assert approved.is_approved(approved.task.requested_tuple) is True
    assert approved.is_approved(approved.near_miss_tuple) is False

    assert missing.is_approved(missing.task.requested_tuple) is False
    assert missing.is_approved(missing.near_miss_tuple) is True

    # A tuple that is neither requested nor the near miss is never approved.
    bogus = ApprovalTuple("T-9999", 1, "nowhere")
    assert approved.is_approved(bogus) is False
    assert missing.is_approved(bogus) is False
