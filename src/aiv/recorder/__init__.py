# Adapted from Agent_World_L3 @61c7d37 src/ethwall/recorder/{_log,_verify}.py (pre-sprint code).
"""Append-only, hash-chained JSONL run files (ARCHITECTURE §9).

`runs/<partition>/<run_id>/events.jsonl`: one line per event. Each line is the contract `Event`
plus chain fields `idx` (line number, 0-based), `prev_hash`, `hash` (SHA-256 of the canonical line
without `hash`/`sig`) and `sig` (HMAC-SHA256 of `hash`). `close()` writes `checkpoint.json`
(`n_lines`, `head_hash`), so truncation is detectable.

Integrity is relative to the key. In mock mode the key is public (it is in this file), so a mock
run only detects accidental damage. Live runs require `AIV_RECORDER_KEY`.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from aiv.contract import EVENTS_FILE, Event, Mode, canonical_json, sha256_hex, validate_payload

GENESIS = "0" * 64
CHECKPOINT_FILE = "checkpoint.json"
KEY_ENV = "AIV_RECORDER_KEY"
MOCK_KEY = b"aiv-mock-key"
MOCK_KEY_ID = "mock-fixed"
_CHAIN_FIELDS = ("idx", "prev_hash", "hash", "sig")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def sign(key: bytes, digest: str) -> str:
    return hmac.new(key, digest.encode("ascii"), sha256).hexdigest()


def line_hash(body: dict[str, Any]) -> str:
    return sha256_hex(canonical_json({k: v for k, v in body.items() if k not in ("hash", "sig")}))


def recorder_key(mode: Mode | str) -> tuple[bytes, str]:
    """(key, key_id). Live requires `AIV_RECORDER_KEY`; mock uses the public fixed key."""
    if Mode(mode) != Mode.LIVE:
        return MOCK_KEY, MOCK_KEY_ID
    value = os.environ.get(KEY_ENV, "")
    if not value:
        raise RuntimeError(f"live mode requires {KEY_ENV} to be set")
    key = value.encode("utf-8")
    return key, sha256_hex(key)[:12]


def verify_mode_for(partition: str, run_id: str) -> Mode:
    """Which key verifies a run when reading it back. The `mock` partition is always mock. A
    mock-mode run of the confirm experiment (T13) lives in the `confirm` partition with a `mock-`
    run_id and is signed with the public mock key; analysis labels such a run MOCK. Every other
    partition is verified with the live key."""
    if partition == "mock" or (partition == "confirm" and run_id.startswith("mock-")):
        return Mode.MOCK
    return Mode.LIVE


def new_run_dir(root: str | os.PathLike[str], partition: str, run_id: str) -> Path:
    """Create `root/partition/run_id`; refuse an existing directory (runs are append-only)."""
    for part in (partition, run_id):
        if not part or part in (".", "..") or any(c in part for c in "/\\"):
            raise ValueError(f"invalid path component {part!r}")
    d = Path(root) / partition / run_id
    d.parent.mkdir(parents=True, exist_ok=True)
    d.mkdir()  # FileExistsError if it already exists
    return d


class JsonlRecorder:
    """`EventSink` writing `run_dir/events.jsonl`. Thread-safe (episodes may run concurrently)."""

    def __init__(
        self,
        run_dir: str | os.PathLike[str],
        key: bytes,
        key_id: str,
        *,
        clock: Callable[[], str] | None = None,
    ):
        if not key:
            raise ValueError("recorder key must be non-empty")
        self.run_dir = Path(run_dir)
        self.key_id = key_id
        self._key = key
        self._clock = clock or utc_now
        self._lock = threading.Lock()
        self._n = 0
        self._head = GENESIS
        self._closed = False
        self.path = self.run_dir / EVENTS_FILE
        with open(self.path, "x", encoding="utf-8", newline="\n"):  # never append onto a log
            pass

    def write(self, event: Event) -> None:
        validate_payload(event.type, event.payload)
        with self._lock:
            if self._closed:
                raise RuntimeError("recorder is closed")
            body = {**event.to_dict(), "idx": self._n, "prev_hash": self._head}
            body["hash"] = line_hash(body)
            body["sig"] = sign(self._key, body["hash"])
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(canonical_json(body) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._n += 1
            self._head = body["hash"]

    def close(self) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("recorder is closed")
            self._closed = True
            cp = {"n_lines": self._n, "head_hash": self._head, "key_id": self.key_id}
            with open(self.run_dir / CHECKPOINT_FILE, "x", encoding="utf-8") as f:
                f.write(canonical_json(cp) + "\n")
            return cp


def read_events(run_dir: str | os.PathLike[str]) -> list[Event]:
    """Events of a run, in file order (chain fields stripped). Does not verify; see verify_run."""
    out = []
    for line in _lines(Path(run_dir) / EVENTS_FILE):
        d = json.loads(line)
        out.append(Event.from_dict({k: v for k, v in d.items() if k not in _CHAIN_FIELDS}))
    return out


def events_by_episode(events: list[Event]) -> dict[str, list[Event]]:
    eps: dict[str, list[Event]] = {}
    for e in events:
        eps.setdefault(e.episode_id, []).append(e)
    return {k: sorted(v, key=lambda e: e.seq) for k, v in eps.items()}


def verify_run(run_dir: str | os.PathLike[str], key: bytes) -> list[str]:
    """Return a list of faults; empty means the run verifies.

    Checks: parseable lines, contiguous `idx`, `prev_hash` chain, `hash`, `sig`, checkpoint
    (present, matching line count and head), payload schema, and per-episode `seq` strictly
    increasing from 0 in file order.
    """
    run_dir = Path(run_dir)
    faults: list[str] = []
    head = GENESIS
    n = 0
    last_seq: dict[str, int] = {}
    path = run_dir / EVENTS_FILE
    if not path.exists():
        return [f"missing {EVENTS_FILE}"]
    for i, line in enumerate(_lines(path)):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            faults.append(f"line {i}: not JSON")
            continue
        if d.get("idx") != i:
            faults.append(f"line {i}: idx {d.get('idx')!r} != {i}")
        if d.get("prev_hash") != head:
            faults.append(f"line {i}: prev_hash breaks the chain")
        if d.get("hash") != line_hash(d):
            faults.append(f"line {i}: hash mismatch")
        if not hmac.compare_digest(str(d.get("sig", "")), sign(key, str(d.get("hash", "")))):
            faults.append(f"line {i}: bad signature")
        try:
            ev = Event.from_dict({k: v for k, v in d.items() if k not in _CHAIN_FIELDS})
            validate_payload(ev.type, ev.payload)
            prev = last_seq.get(ev.episode_id, -1)
            if ev.seq != prev + 1:
                faults.append(f"line {i}: episode {ev.episode_id} seq {ev.seq} after {prev}")
            last_seq[ev.episode_id] = ev.seq
        except (KeyError, ValueError, TypeError) as exc:
            faults.append(f"line {i}: bad event ({exc})")
        head = str(d.get("hash"))
        n += 1
    cp_path = run_dir / CHECKPOINT_FILE
    if not cp_path.exists():
        faults.append("missing checkpoint (run not closed)")
    else:
        cp = json.loads(cp_path.read_text(encoding="utf-8"))
        if cp.get("n_lines") != n:
            faults.append(f"checkpoint n_lines {cp.get('n_lines')} != {n}")
        if cp.get("head_hash") != head:
            faults.append("checkpoint head_hash mismatch")
    return faults


def _lines(path: Path) -> Iterator[str]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield line.rstrip("\n")
