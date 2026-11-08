# Adapted from Agent_World_L3 @61c7d37 src/ethwall/spend/ledger.py (pre-sprint code).
"""Reservation-based spend ledger (ARCHITECTURE §10; docs/decisions.md "Spend + live gate").

- `Ledger.from_env(partition)` reads pricing (`PRICE_INPUT_PER_MTOK`, `PRICE_OUTPUT_PER_MTOK`),
  caps (`SPEND_CAP_TOTAL_USD`, `SPEND_CAP_<PARTITION>_USD`, `SPEND_CAP_EPISODE_USD`) and the
  shared root (`AIV_SPEND_ROOT`, default `<repo>/.spend`) from the environment.
- `reserve()` computes `input_tokens_est * price_in + max_tokens * price_out` (USD per Mtok is
  numerically micro-USD per token), rounded UP with `Decimal`, **before dispatch**, and raises
  `SpendRefused` if pricing is unknown/empty/non-positive or the reservation would exceed the
  total, partition or episode cap. The mock partition is refused outright: mock never charges
  the ledger.
- `settle()` replaces the reservation with the actual usage; `release()` cancels it.
- Per-model pricing (cross-provider extension, sol.md §16): `reserve(..., model=...)` prices the
  call by *that call's model*: `PRICE_<KEY>_INPUT_PER_MTOK` / `PRICE_<KEY>_OUTPUT_PER_MTOK`, where
  `<KEY>` is the model id uppercased with every non-alphanumeric replaced by `_` (e.g.
  `gemini-3.5-flash-lite` -> `PRICE_GEMINI_3_5_FLASH_LITE_INPUT_PER_MTOK`). The generic
  `PRICE_INPUT_PER_MTOK` / `PRICE_OUTPUT_PER_MTOK` apply only when `model` is `None` or equals
  `ACTOR_MODEL_ID` (the original Anthropic path). Any other unpriced model -> `SpendRefused`. The
  `Reservation` carries the model so `settle()` prices the actual usage the same way.
- All shared state lives on disk under one root-wide lock (`<root>/.spend.lock`, a byte lock:
  `msvcrt.locking` on Windows, `fcntl.flock` elsewhere) so concurrent reservations across
  processes cannot oversubscribe a cap: `ledger.json` holds totals per partition and per
  episode; `ledger.jsonl` is an append-only journal of every reserve/settle/release.

All amounts are integer micro-dollars internally (1 USD == 1_000_000 micro-dollars).
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import random
import re
import sys
import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

MICRO = 1_000_000
SCHEMA = 1
LOCK_NAME = ".spend.lock"
LEDGER_FILE = "ledger.json"
JOURNAL_FILE = "ledger.jsonl"
SPEND_ROOT_ENV = "AIV_SPEND_ROOT"
LOCK_TIMEOUT_S = 30.0
MOCK_PARTITION = "mock"


class SpendRefused(RuntimeError):
    """Pricing is unknown, a cap would be exceeded, or the partition may never be charged."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class Reservation:
    """A reserved-but-not-yet-settled charge. Pass it back to `settle()` or `release()`."""

    id: str
    episode_id: str
    partition: str
    cost_micro: int
    input_tokens_est: int
    max_tokens: int
    created_ts: str
    model: str | None = None  # the model this call was priced for (None = generic pricing)

    @property
    def cost_usd(self) -> Decimal:
        return Decimal(self.cost_micro) / MICRO


# ---------------------------------------------------------------------------- root lock

# errno values meaning "another handle holds the lock" (msvcrt: EACCES/EDEADLOCK; flock:
# EWOULDBLOCK/EAGAIN). Anything else is a real error and propagates.
_BUSY = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, getattr(errno, "EDEADLOCK", -1)}

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in _BUSY:
                return False
            raise
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in _BUSY:
                return False
            raise
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


REPLACE_ATTEMPTS = 20


def _replace_with_retry(src: Path, dst: Path, *, attempts: int = REPLACE_ATTEMPTS) -> None:
    """`os.replace` that rides out transient Windows sharing violations (antivirus, indexer).

    Our own writers are already serialized by the root lock, so a short bounded retry is safe.
    """
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(min(0.05 * (attempt + 1), 0.5))


@contextlib.contextmanager
def ledger_lock(root: str | Path, timeout: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Hold the root-wide spend lock `<root>/.spend.lock`.

    Exclusive across processes, and across separate acquisitions within one process (each call
    opens its own handle), so it is not re-entrant. Raises `TimeoutError` if the lock is still
    held by someone else after `timeout` seconds.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / LOCK_NAME
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"spend ledger lock {path} not acquired within {timeout:g}s")
            time.sleep(random.uniform(0.005, 0.02))
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------- pricing / caps


def _decimal_or_none(value: Any) -> Decimal | None:
    """`value` as a positive, finite `Decimal`, or `None` if unset/blank."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        d = Decimal(text)
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return d


def _require_price(value: Any, name: str) -> Decimal:
    d = _decimal_or_none(value)
    if d is None:
        raise SpendRefused(f"{name} is not set; refusing to reserve")
    if d <= 0:
        raise SpendRefused(f"{name} must be a positive finite number, got {value!r}")
    return d


def _require_cap_micro(value: Any, name: str) -> int:
    d = _decimal_or_none(value)
    if d is None:
        raise SpendRefused(f"{name} is not set; refusing to reserve")
    if d <= 0:
        raise SpendRefused(f"{name} must be a positive finite number, got {value!r}")
    return int((d * MICRO).to_integral_value(rounding=ROUND_CEILING))


def _cost_micro(tokens_in: int, tokens_out: int, price_in: Decimal, price_out: Decimal) -> int:
    """Exact cost in micro-dollars, rounded UP. USD per Mtok == micro-USD per token."""
    exact = (
        Decimal(max(0, int(tokens_in))) * price_in + Decimal(max(0, int(tokens_out))) * price_out
    )
    return int(exact.to_integral_value(rounding=ROUND_CEILING))


def price_key(model: str) -> str:
    """`gemini-3.5-flash-lite` -> `GEMINI_3_5_FLASH_LITE` (the `<KEY>` in `PRICE_<KEY>_*`)."""
    return re.sub(r"[^A-Z0-9]", "_", str(model).upper())


def price_env_names(model: str) -> tuple[str, str]:
    key = price_key(model)
    return f"PRICE_{key}_INPUT_PER_MTOK", f"PRICE_{key}_OUTPUT_PER_MTOK"


def _env_value(env: Mapping[str, str], key: str) -> str | None:
    raw = env.get(key, "")
    text = raw.strip() if raw is not None else ""
    return text or None


def default_spend_root() -> str:
    """`<repo>/.spend`: three parents up from this file is the repo root (src/aiv/spend/)."""
    return str(Path(__file__).resolve().parents[3] / ".spend")


# ---------------------------------------------------------------------------- ledger


class Ledger:
    """Per-partition view onto the shared, lock-guarded `ledger.json` / `ledger.jsonl`.

    Stateless besides configuration: every operation reloads the full ledger from disk under
    the root lock, so instances in different threads or processes never race.
    """

    lock_timeout_s: float = LOCK_TIMEOUT_S

    def __init__(
        self,
        root: str | Path,
        partition: str,
        *,
        price_in_per_mtok: Any,
        price_out_per_mtok: Any,
        total_cap: Any,
        partition_cap: Any,
        episode_cap: Any,
        price_env: Mapping[str, str] | None = None,
        actor_model_id: str | None = None,
    ) -> None:
        self.root = Path(root)
        self.partition = str(partition)
        self.price_in_per_mtok = price_in_per_mtok
        self.price_out_per_mtok = price_out_per_mtok
        self.total_cap = total_cap
        self.partition_cap = partition_cap
        self.episode_cap = episode_cap
        # Per-model `PRICE_<KEY>_*` values. A directly constructed ledger sees none unless given,
        # so a developer's shell `PRICE_*` variables never leak into tests.
        self._price_env_given = price_env is not None
        self.price_env: dict[str, str] = dict(price_env or {})
        self.actor_model_id = actor_model_id

    @classmethod
    def from_env(cls, partition: Any, *, env: Mapping[str, str] | None = None) -> Ledger:
        env = os.environ if env is None else env
        name = str(partition)
        root = _env_value(env, SPEND_ROOT_ENV) or default_spend_root()
        return cls(
            root,
            name,
            price_in_per_mtok=_env_value(env, "PRICE_INPUT_PER_MTOK"),
            price_out_per_mtok=_env_value(env, "PRICE_OUTPUT_PER_MTOK"),
            total_cap=_env_value(env, "SPEND_CAP_TOTAL_USD"),
            partition_cap=_env_value(env, f"SPEND_CAP_{name.upper()}_USD"),
            episode_cap=_env_value(env, "SPEND_CAP_EPISODE_USD"),
            price_env={
                k: v
                for k, v in env.items()
                if k.startswith("PRICE_") and k.endswith("_PER_MTOK") and v and v.strip()
            },
            actor_model_id=_env_value(env, "ACTOR_MODEL_ID"),
        )

    # ------------------------------------------------------------------ pricing

    def prices_for(self, model: str | None) -> tuple[Decimal, Decimal]:
        """(USD/Mtok in, USD/Mtok out) for one call's model. Raises `SpendRefused` (before any
        disk access) if the model has no price.

        Per-model `PRICE_<KEY>_INPUT/OUTPUT_PER_MTOK` (both set) wins. The generic
        `PRICE_INPUT/OUTPUT_PER_MTOK` is the fallback only for `model is None` or
        `model == ACTOR_MODEL_ID`. A ledger built without any pricing context (no `price_env`
        and no `actor_model_id`: the single-model constructor the pre-extension code and tests
        use) keeps the original behavior: one generic price for every call. `from_env`, the
        only production constructor, always passes a `price_env`, so it is always strict.
        """
        if model is not None:
            name_in, name_out = price_env_names(model)
            val_in = _env_value(self.price_env, name_in)
            val_out = _env_value(self.price_env, name_out)
            if val_in is not None and val_out is not None:
                return _require_price(val_in, name_in), _require_price(val_out, name_out)
            single_model = self._price_env_given is False and self.actor_model_id is None
            if model != self.actor_model_id and not single_model:
                missing = name_in if val_in is None else name_out
                raise SpendRefused(
                    f"{missing} is not set for model {model!r}; refusing to reserve (the generic "
                    "PRICE_INPUT/OUTPUT_PER_MTOK apply only to ACTOR_MODEL_ID)"
                )
        return (
            _require_price(self.price_in_per_mtok, "PRICE_INPUT_PER_MTOK"),
            _require_price(self.price_out_per_mtok, "PRICE_OUTPUT_PER_MTOK"),
        )

    # ------------------------------------------------------------------ calls

    def reserve(
        self,
        input_tokens_est: int,
        max_tokens: int,
        *,
        episode_id: str,
        model: str | None = None,
    ) -> Reservation:
        """Reserve `input_tokens_est * price_in + max_tokens * price_out` (rounded up), priced
        for `model` (see `prices_for`).

        Raises `SpendRefused` without touching disk if pricing is unknown, the partition is
        `mock` (never charged), or the reservation would exceed any cap.
        """
        if self.partition == MOCK_PARTITION:
            raise SpendRefused("the mock partition is never charged; it never uses the ledger")
        price_in, price_out = self.prices_for(model)
        total_cap_micro = _require_cap_micro(self.total_cap, "SPEND_CAP_TOTAL_USD")
        partition_cap_micro = _require_cap_micro(
            self.partition_cap, f"SPEND_CAP_{self.partition.upper()}_USD"
        )
        episode_cap_micro = _require_cap_micro(self.episode_cap, "SPEND_CAP_EPISODE_USD")
        cost_micro = _cost_micro(input_tokens_est, max_tokens, price_in, price_out)

        res_id = uuid.uuid4().hex
        created = utc_now()
        with self._locked() as state:
            totals = state["totals"]
            part = totals["partitions"].setdefault(self.partition, {"spent_micro": 0})
            episodes = state["episodes"]
            ep = episodes.setdefault(episode_id, {"spent_micro": 0, "partition": self.partition})
            if ep["partition"] != self.partition:
                raise SpendRefused(
                    f"episode {episode_id!r} already belongs to partition {ep['partition']!r}"
                )
            new_ep = ep["spent_micro"] + cost_micro
            new_part = part["spent_micro"] + cost_micro
            new_total = totals["spent_micro"] + cost_micro
            if new_ep > episode_cap_micro:
                raise SpendRefused(
                    f"episode cap exceeded for {episode_id!r}: "
                    f"{new_ep / MICRO} > {episode_cap_micro / MICRO} USD"
                )
            if new_part > partition_cap_micro:
                raise SpendRefused(
                    f"partition cap exceeded for {self.partition!r}: "
                    f"{new_part / MICRO} > {partition_cap_micro / MICRO} USD"
                )
            if new_total > total_cap_micro:
                raise SpendRefused(
                    f"total cap exceeded: {new_total / MICRO} > {total_cap_micro / MICRO} USD"
                )
            ep["spent_micro"] = new_ep
            part["spent_micro"] = new_part
            totals["spent_micro"] = new_total
            state["reservations"][res_id] = {
                "episode_id": episode_id,
                "partition": self.partition,
                "cost_micro": cost_micro,
                "created_ts": created,
            }
            self._write(state)
            self._journal(
                state,
                {
                    "op": "reserve",
                    "ts": created,
                    "reservation_id": res_id,
                    "episode_id": episode_id,
                    "partition": self.partition,
                    "cost_micro": cost_micro,
                    "input_tokens_est": input_tokens_est,
                    "max_tokens": max_tokens,
                    "model": model,
                },
            )
        return Reservation(
            id=res_id,
            episode_id=episode_id,
            partition=self.partition,
            cost_micro=cost_micro,
            input_tokens_est=input_tokens_est,
            max_tokens=max_tokens,
            created_ts=created,
            model=model,
        )

    def settle(self, res: Reservation, input_tokens: int, output_tokens: int) -> int:
        """Replace the reservation with the actual cost, priced for the reservation's model.
        Returns the actual cost in micro-USD."""
        price_in, price_out = self.prices_for(res.model)
        actual_micro = _cost_micro(input_tokens, output_tokens, price_in, price_out)
        delta = actual_micro - res.cost_micro
        with self._locked() as state:
            if res.id not in state["reservations"]:
                raise SpendRefused(f"unknown or already-closed reservation {res.id}")
            self._apply_delta(state, res, delta)
            del state["reservations"][res.id]
            self._write(state)
            self._journal(
                state,
                {
                    "op": "settle",
                    "ts": utc_now(),
                    "reservation_id": res.id,
                    "episode_id": res.episode_id,
                    "partition": res.partition,
                    "cost_micro": actual_micro,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            )
        return actual_micro

    def release(self, res: Reservation) -> None:
        """Release an open reservation for a call that was never made (idempotent)."""
        with self._locked() as state:
            if res.id not in state["reservations"]:
                return
            self._apply_delta(state, res, -res.cost_micro)
            del state["reservations"][res.id]
            self._write(state)
            self._journal(
                state,
                {
                    "op": "release",
                    "ts": utc_now(),
                    "reservation_id": res.id,
                    "episode_id": res.episode_id,
                    "partition": res.partition,
                },
            )

    @staticmethod
    def _apply_delta(state: dict[str, Any], res: Reservation, delta: int) -> None:
        totals = state["totals"]
        part = totals["partitions"].setdefault(res.partition, {"spent_micro": 0})
        ep = state["episodes"].setdefault(
            res.episode_id, {"spent_micro": 0, "partition": res.partition}
        )
        part["spent_micro"] += delta
        ep["spent_micro"] += delta
        totals["spent_micro"] += delta

    # ------------------------------------------------------------------ views

    @property
    def total_spent_usd(self) -> Decimal:
        with self._locked() as state:
            return Decimal(state["totals"]["spent_micro"]) / MICRO

    @property
    def partition_spent_usd(self) -> Decimal:
        with self._locked() as state:
            part = state["totals"]["partitions"].get(self.partition, {"spent_micro": 0})
            return Decimal(part["spent_micro"]) / MICRO

    def episode_spent_usd(self, episode_id: str) -> Decimal:
        with self._locked() as state:
            ep = state["episodes"].get(episode_id, {"spent_micro": 0})
            return Decimal(ep["spent_micro"]) / MICRO

    # ------------------------------------------------------------------ internals

    @contextlib.contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        """Hold the root lock and reload the full ledger state from disk."""
        with ledger_lock(self.root, self.lock_timeout_s):
            yield self._read()

    def _ledger_path(self) -> Path:
        return self.root / LEDGER_FILE

    def _read(self) -> dict[str, Any]:
        path = self._ledger_path()
        if not path.exists():
            return {
                "schema": SCHEMA,
                "totals": {"spent_micro": 0, "partitions": {}},
                "episodes": {},
                "reservations": {},
            }
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("totals", {"spent_micro": 0, "partitions": {}})
        data["totals"].setdefault("spent_micro", 0)
        data["totals"].setdefault("partitions", {})
        data.setdefault("episodes", {})
        data.setdefault("reservations", {})
        return data

    def _write(self, state: dict[str, Any]) -> None:
        """Atomic replace of `ledger.json`. Call only while holding the root lock."""
        state["schema"] = SCHEMA
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._ledger_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True, indent=1), encoding="utf-8")
        _replace_with_retry(tmp, path)

    def _journal(self, state: dict[str, Any], record: dict[str, Any]) -> None:
        """Append one line to `ledger.jsonl`. Call only while holding the root lock."""
        del state  # unused; kept for a consistent "call under lock" signature
        path = self.root / JOURNAL_FILE
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
