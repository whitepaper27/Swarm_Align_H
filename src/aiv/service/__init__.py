"""Registry lookup + publication service + append-only ordered event log (ARCHITECTURE §3, §4,
§5; docs/decisions.md §Service).

`EpisodeService` is the only writer of events for an episode. It assigns `seq`, builds the
`Event`, and writes it through an `EventSink`. Tool methods (`check_approval`, `publish`,
`return_draft`, `request_approval`) return the dict the publisher sees as the tool result; they
raise `RuntimeError` once the episode has terminated. `emit()` itself keeps working after
termination, since the runtime still records `episode.end` (and possibly trailing `model.call` /
`agent.message` events) through the same service.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from aiv.contract import (
    Actor,
    ApprovalTuple,
    Event,
    EventSink,
    EventType,
    RegistryStatus,
    World,
    validate_payload,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class ListSink:
    """`EventSink` that collects events into a list. For tests and other modules."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def write(self, event: Event) -> None:
        self.events.append(event)


class EpisodeService:
    """`ServiceAPI` implementation for one episode (one `World`, one run/episode id)."""

    def __init__(
        self,
        world: World,
        sink: EventSink,
        run_id: str,
        episode_id: str,
        *,
        enforcement: bool = False,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.world = world
        self.sink = sink
        self.run_id = run_id
        self.episode_id = episode_id
        self.enforcement = enforcement
        self._clock = clock or utc_now
        # Instance attributes only: two services for the same world must share no mutable
        # state (ARCHITECTURE §12 invariant 4).
        self.events: list[Event] = []
        self.terminated = False
        self._seq = 0
        self._attempt_n = 0

    # -- plumbing ---------------------------------------------------------------------------

    def emit(self, type_: EventType, actor: Actor, payload: Mapping[str, Any]) -> Event:
        payload = dict(payload)
        validate_payload(type_, payload)
        event = Event(
            run_id=self.run_id,
            episode_id=self.episode_id,
            seq=self._seq,
            ts=self._clock(),
            type=EventType(type_),
            actor=Actor(actor),
            payload=payload,
        )
        self.sink.write(event)  # if this raises, seq/events stay untouched (nothing half-written)
        self._seq += 1
        self.events.append(event)
        return event

    def _require_active(self) -> None:
        if self.terminated:
            raise RuntimeError(f"episode {self.episode_id} already terminated")

    def _registry_status(self, t: ApprovalTuple) -> RegistryStatus:
        return RegistryStatus.APPROVED if self.world.is_approved(t) else RegistryStatus.NOT_APPROVED

    def _next_attempt_id(self) -> str:
        attempt_id = f"p{self._attempt_n}"
        self._attempt_n += 1
        return attempt_id

    # -- publisher tools (§4) ----------------------------------------------------------------

    def check_approval(self, t: ApprovalTuple) -> dict[str, Any]:
        """Lookups never end the episode, but still require an active one."""
        self._require_active()
        status = self._registry_status(t)
        self.emit(
            EventType.REGISTRY_LOOKUP,
            Actor.SERVICE,
            {"tuple": t.to_dict(), "response": {"status": status.value}},
        )
        return {"status": status.value}

    def publish(self, t: ApprovalTuple) -> dict[str, Any]:
        self._require_active()
        status = self._registry_status(t)
        attempt_id = self._next_attempt_id()
        self.emit(
            EventType.PUBLICATION_ATTEMPT,
            Actor.SERVICE,
            {"tuple": t.to_dict(), "attempt_id": attempt_id, "registry_status": status.value},
        )
        if self.enforcement and status is not RegistryStatus.APPROVED:
            reason = "tuple not approved"
            self.emit(
                EventType.PUBLICATION_REJECTED,
                Actor.SERVICE,
                {
                    "tuple": t.to_dict(),
                    "attempt_id": attempt_id,
                    "registry_status": status.value,
                    "reason": reason,
                },
            )
            return {"status": "rejected", "reason": reason, "attempt_id": attempt_id}
        # Measurement mode always commits; enforcement mode commits approved tuples too.
        self.emit(
            EventType.PUBLICATION_COMMIT,
            Actor.SERVICE,
            {"tuple": t.to_dict(), "attempt_id": attempt_id, "registry_status": status.value},
        )
        self.terminated = True
        return {"status": "published", "attempt_id": attempt_id}

    def return_draft(self, task_id: str, report_version: int, note: str) -> dict[str, Any]:
        self._require_active()
        self.emit(
            EventType.PUBLISHER_RETURN_DRAFT,
            Actor.SERVICE,
            {"task_id": task_id, "report_version": report_version, "note": note},
        )
        self.terminated = True
        return {"status": "returned"}

    def request_approval(self, t: ApprovalTuple, note: str) -> dict[str, Any]:
        self._require_active()
        self.emit(
            EventType.PUBLISHER_REQUEST_APPROVAL,
            Actor.SERVICE,
            {"tuple": t.to_dict(), "note": note},
        )
        self.terminated = True
        return {"status": "approval_requested"}
