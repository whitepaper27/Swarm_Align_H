"""Invariant 7 (ARCHITECTURE §12): the ledger refuses calls when pricing is unknown or a cap
would be exceeded, including under concurrent reservations. Everything here uses `tmp_path`;
the real `.spend` directory is never touched.
"""

from __future__ import annotations

import json
import threading
from decimal import Decimal

import pytest

from aiv.spend import Ledger, SpendRefused


def make_ledger(tmp_path, partition="pilot", **overrides):
    kwargs = dict(
        price_in_per_mtok="1",
        price_out_per_mtok="1",
        total_cap="50",
        partition_cap="10",
        episode_cap="1",
    )
    kwargs.update(overrides)
    return Ledger(tmp_path / "spend", partition, **kwargs)


# ---------------------------------------------------------------------------- pricing refusals


def test_refuses_when_pricing_missing(tmp_path):
    ledger = make_ledger(tmp_path, price_in_per_mtok=None)
    with pytest.raises(SpendRefused):
        ledger.reserve(10, 10, episode_id="ep-1")


def test_refuses_when_pricing_empty_string(tmp_path):
    ledger = make_ledger(tmp_path, price_out_per_mtok="")
    with pytest.raises(SpendRefused):
        ledger.reserve(10, 10, episode_id="ep-1")


def test_refuses_when_pricing_non_positive(tmp_path):
    ledger = make_ledger(tmp_path, price_in_per_mtok="0")
    with pytest.raises(SpendRefused):
        ledger.reserve(10, 10, episode_id="ep-1")
    ledger2 = make_ledger(tmp_path, price_out_per_mtok="-1")
    with pytest.raises(SpendRefused):
        ledger2.reserve(10, 10, episode_id="ep-2")


def test_refusal_never_creates_the_spend_root(tmp_path):
    root = tmp_path / "spend"
    ledger = make_ledger(tmp_path, price_in_per_mtok=None)
    with pytest.raises(SpendRefused):
        ledger.reserve(10, 10, episode_id="ep-1")
    assert not root.exists()


def test_from_env_blank_pricing_refuses(tmp_path):
    # Exactly the shape .env.example ships: PRICE_*_PER_MTOK left blank.
    env = {
        "AIV_SPEND_ROOT": str(tmp_path / "spend"),
        "PRICE_INPUT_PER_MTOK": "",
        "PRICE_OUTPUT_PER_MTOK": "",
        "SPEND_CAP_TOTAL_USD": "50",
        "SPEND_CAP_PILOT_USD": "5",
        "SPEND_CAP_EPISODE_USD": "0.80",
    }
    ledger = Ledger.from_env("pilot", env=env)
    with pytest.raises(SpendRefused):
        ledger.reserve(10, 10, episode_id="ep-1")


def test_from_env_reads_configured_values(tmp_path):
    env = {
        "AIV_SPEND_ROOT": str(tmp_path / "spend"),
        "PRICE_INPUT_PER_MTOK": "3",
        "PRICE_OUTPUT_PER_MTOK": "5",
        "SPEND_CAP_TOTAL_USD": "50",
        "SPEND_CAP_PILOT_USD": "5",
        "SPEND_CAP_EPISODE_USD": "0.80",
    }
    ledger = Ledger.from_env("pilot", env=env)
    res = ledger.reserve(10, 10, episode_id="ep-1")
    # 10 tokens * $3/Mtok + 10 tokens * $5/Mtok = 80 micro-USD exactly.
    assert res.cost_micro == 80


# ---------------------------------------------------------------------------- mock partition


def test_mock_partition_is_always_refused(tmp_path):
    ledger = make_ledger(tmp_path, partition="mock", partition_cap="999")
    with pytest.raises(SpendRefused):
        ledger.reserve(1, 1, episode_id="ep-mock")


# ---------------------------------------------------------------------------- cap refusals


def test_refuses_when_total_cap_exceeded(tmp_path):
    ledger = make_ledger(tmp_path, total_cap="0.00001")  # 10 micro-USD
    with pytest.raises(SpendRefused):
        ledger.reserve(1000, 1000, episode_id="ep-1")  # 2000 micro-USD at $1/Mtok


def test_refuses_when_partition_cap_exceeded(tmp_path):
    ledger = make_ledger(tmp_path, partition_cap="0.00001", total_cap="50")
    with pytest.raises(SpendRefused):
        ledger.reserve(1000, 1000, episode_id="ep-1")


def test_refuses_when_episode_cap_exceeded(tmp_path):
    ledger = make_ledger(tmp_path, episode_cap="0.00001", partition_cap="50", total_cap="50")
    with pytest.raises(SpendRefused):
        ledger.reserve(1000, 1000, episode_id="ep-1")


def test_cap_refusal_leaves_no_trace(tmp_path):
    ledger = make_ledger(tmp_path, total_cap="0.00001")
    with pytest.raises(SpendRefused):
        ledger.reserve(1000, 1000, episode_id="ep-1")
    assert ledger.total_spent_usd == 0


# ---------------------------------------------------------------------------- rounding


def test_reserve_rounds_up(tmp_path):
    ledger = make_ledger(
        tmp_path, price_in_per_mtok="0.333333", price_out_per_mtok="1", episode_cap="1"
    )
    # 3 tokens * 0.333333 micro-USD/token = 0.999999 micro-USD, plus 0 output tokens.
    res = ledger.reserve(3, 0, episode_id="ep-1")
    assert res.cost_micro == 1  # rounded UP from 0.999999, never truncated to 0


# ---------------------------------------------------------------------------- settle / release


def test_settle_reduces_to_actual(tmp_path):
    ledger = make_ledger(tmp_path, episode_cap="1", partition_cap="1", total_cap="50")
    res = ledger.reserve(50, 100, episode_id="ep-1")  # reserves 150 micro-USD
    assert res.cost_micro == 150
    actual = ledger.settle(res, input_tokens=10, output_tokens=20)  # 30 micro-USD
    assert actual == 30
    assert ledger.total_spent_usd == Decimal(30) / Decimal(1_000_000)
    assert ledger.episode_spent_usd("ep-1") == Decimal(30) / Decimal(1_000_000)
    assert ledger.partition_spent_usd == Decimal(30) / Decimal(1_000_000)


def test_settle_unknown_reservation_refused(tmp_path):
    ledger = make_ledger(tmp_path)
    res = ledger.reserve(1, 1, episode_id="ep-1")
    ledger.settle(res, 1, 1)
    with pytest.raises(SpendRefused):
        ledger.settle(res, 1, 1)  # already closed


def test_release_on_failure_returns_to_zero(tmp_path):
    ledger = make_ledger(tmp_path)
    res = ledger.reserve(10, 10, episode_id="ep-1")
    assert ledger.total_spent_usd > 0
    ledger.release(res)
    assert ledger.total_spent_usd == 0
    assert ledger.episode_spent_usd("ep-1") == 0


def test_release_is_idempotent(tmp_path):
    ledger = make_ledger(tmp_path)
    res = ledger.reserve(10, 10, episode_id="ep-1")
    ledger.release(res)
    ledger.release(res)  # no-op, does not go negative
    assert ledger.total_spent_usd == 0


def test_settle_can_exceed_reservation(tmp_path):
    # Settlement charges the actual usage even if it is more than was reserved; the ledger
    # does not re-check caps at settle time (the call already happened).
    ledger = make_ledger(tmp_path, episode_cap="1")
    res = ledger.reserve(1, 1, episode_id="ep-1")  # 2 micro-USD
    actual = ledger.settle(res, input_tokens=1000, output_tokens=1000)  # 2000 micro-USD
    assert actual == 2000
    assert ledger.total_spent_usd == Decimal(2000) / Decimal(1_000_000)


# ---------------------------------------------------------------------------- journal


def test_journal_is_append_only_jsonl(tmp_path):
    ledger = make_ledger(tmp_path)
    res = ledger.reserve(1, 1, episode_id="ep-1")
    ledger.settle(res, 1, 1)
    journal = tmp_path / "spend" / "ledger.jsonl"
    lines = journal.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    ops = [json.loads(line)["op"] for line in lines]
    assert ops == ["reserve", "settle"]


# ---------------------------------------------------------------------------- concurrency


def test_concurrent_reservations_cannot_oversubscribe(tmp_path):
    """8 workers each reserve a $1 slot against a $3 total/partition cap: exactly 3 fit."""
    n_workers = 8
    cap_fits = 3
    root = tmp_path / "spend"

    results: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_workers)

    def worker(i: int) -> None:
        ledger = Ledger(
            root,
            "pilot",
            price_in_per_mtok="1",
            price_out_per_mtok="1000000",  # $1,000,000/Mtok == $1/token
            total_cap=str(cap_fits),
            partition_cap=str(cap_fits),
            episode_cap="1",
        )
        barrier.wait()
        try:
            ledger.reserve(0, 1, episode_id=f"ep-{i}")  # exactly $1 each
            outcome = "ok"
        except SpendRefused:
            outcome = "refused"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == cap_fits
    assert results.count("refused") == n_workers - cap_fits

    check = Ledger(
        root,
        "pilot",
        price_in_per_mtok="1",
        price_out_per_mtok="1000000",
        total_cap=str(cap_fits),
        partition_cap=str(cap_fits),
        episode_cap="1",
    )
    assert check.total_spent_usd == Decimal(cap_fits)
    assert check.total_spent_usd <= Decimal(cap_fits)

    journal = root / "ledger.jsonl"
    lines = journal.read_text(encoding="utf-8").strip().splitlines()
    reserve_lines = [line for line in lines if json.loads(line)["op"] == "reserve"]
    assert len(reserve_lines) == cap_fits
