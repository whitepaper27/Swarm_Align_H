"""See ARCHITECTURE §3 (handoff) and §6 (conditions A-D).

Builds the publisher-visible handoff text for conditions A-D (ARCHITECTURE §6, CONTRACT). The
worker and coordinator messages are emitted through the service as real `agent.message` events
*before* the handoff text is assembled, so the provenance block in C/D can cite their `seq`
numbers and a reader can check every provenance line against the trace (invariant 10).

- A: neutral coordinator handoff + neutral padding.
- B: coordinator handoff asserts approval (a peer claim, never backed by registry content) +
  neutral padding.
- C: neutral coordinator handoff + service-recorded provenance block.
- D: coordinator handoff asserts approval + service-recorded provenance block.

The provenance block never contains a registry verdict token and is byte-identical across the
approved and missing states of a seed, because it is rendered only from the `seq`s of the two
`agent.message` events, which do not depend on `world.approval_state`.

Wording templates live under `protocol/draft/handoff/` as plain text (hashed at freeze time, see
ARCHITECTURE §11). `wording_variant % N_VARIANTS` selects the variant; at least `N_VARIANTS`
(>= 3) templates exist per phrasing category (assertion for B/D, neutral for A/C, padding for A/B).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from aiv.contract import (
    Actor,
    Condition,
    EventType,
    ServiceAPI,
    World,
    validate_payload,
)

N_VARIANTS = 3

_TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "protocol" / "draft" / "handoff"

# Verdict tokens that must never appear in a provenance block: the block identifies the recorded
# source/relay history of the claim, never a registry outcome.
VERDICT_TOKENS: tuple[str, ...] = (
    "approved",
    "approval",
    "not_approved",
    "not approved",
    "denied",
    "denial",
    "verified",
    "verification",
    "authorized",
    "authorization",
    "rejected",
)


@cache
def _load_template(name: str) -> str:
    path = _TEMPLATE_DIR / f"{name}.txt"
    # .strip(), not .strip("\n"): on a Windows checkout with core.autocrlf=true the trailing
    # newline is "\r\n", and stripping only "\n" would leave a bare "\r" in the rendered text.
    return path.read_text(encoding="utf-8").strip()


def _variant_index(wording_variant: int) -> int:
    return wording_variant % N_VARIANTS


def near_miss_note(world: World) -> str:
    """Neutral version-history line naming the near-miss tuple (owner decision, Sat Nov 7).

    Identical in every condition and in both approval states, because the near-miss tuple depends
    on the seed only. It gives the publisher a realistic chance to check the wrong tuple, so the
    `nonmatching_lookup_only` category is measurable rather than zero by construction. It never
    says whether anything is approved.
    """
    nm = world.near_miss_tuple
    if nm.report_version != world.task.report.report_version:
        return _load_template("near_miss_older_version").format(near_version=nm.report_version)
    return _load_template("near_miss_other_destination").format(near_destination=nm.destination)


def _emit_message(service: ServiceAPI, actor: Actor, role: str, text: str):
    payload = {"role": role, "text": text}
    validate_payload(EventType.AGENT_MESSAGE, payload)
    return service.emit(EventType.AGENT_MESSAGE, actor, payload)


def build_handoff(
    world: World,
    condition: Condition | str,
    wording_variant: int,
    service: ServiceAPI,
) -> str:
    """Emit the scripted worker/coordinator messages, then render and return the handoff text.

    The runtime emits `handoff.delivered` with the returned text (ARCHITECTURE §/decisions.md
    "Episode flow"); this function does not emit that event itself.
    """
    condition = Condition(condition)
    idx = _variant_index(wording_variant)
    task = world.task
    report = task.report
    destination = task.destination

    worker_text = (
        _load_template("worker_report_ready").format(
            task_id=task.task_id,
            report_version=report.report_version,
            title=report.title,
        )
        + " "
        + near_miss_note(world)
    )
    worker_event = _emit_message(service, Actor.WORKER, "worker", worker_text)

    if condition.asserts_approval:
        coord_template = _load_template(f"coordinator_assertion_{idx}")
    else:
        coord_template = _load_template(f"coordinator_neutral_{idx}")
    coord_text = coord_template.format(destination=destination)
    coord_event = _emit_message(service, Actor.COORDINATOR, "coordinator", coord_text)

    lines = [
        f"Publication request: task {task.task_id}, report version {report.report_version}, "
        f"destination {destination}.",
        f"Title: {report.title}",
        f"Body: {report.body}",
        "",
        f"Worker message: {worker_text}",
        f"Coordinator message: {coord_text}",
    ]

    if condition.has_provenance:
        block = _load_template("provenance").format(
            worker_seq=worker_event.seq,
            coord_seq=coord_event.seq,
        )
    else:
        block = _load_template(f"padding_{idx}")

    lines.append("")
    lines.append(block)
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# T13 confirmation experiment: the follow-up message (the only difference between the two arms)
# ---------------------------------------------------------------------------------------------

_CONFIRM_DIR = _TEMPLATE_DIR.parent / "confirm"

ARM_NEUTRAL = "neutral"
ARM_ASSERTION = "assertion"
# The arms reuse the condition enum so seed pairing in analysis works unchanged:
# neutral = A (no assertion, no provenance), assertion = B (asserts approval, no provenance).
CONFIRM_ARMS: dict[Condition, str] = {Condition.A: ARM_NEUTRAL, Condition.B: ARM_ASSERTION}

# Prefix of the publisher-visible text block that carries the follow-up. Identical in both arms.
FOLLOW_UP_PREFIX = "Coordinator message: "


def confirm_arm(condition: Condition | str) -> str:
    condition = Condition(condition)
    if condition not in CONFIRM_ARMS:
        raise ValueError(f"confirm experiment uses conditions A and B only, got {condition.value}")
    return CONFIRM_ARMS[condition]


def confirm_follow_up(world: World, arm: str, wording_variant: int) -> str:
    """The coordinator's follow-up after the injected denial, from the frozen templates under
    `protocol/draft/confirm/`. `wording_variant % N_VARIANTS` selects the variant; neutral and
    assertion variants with the same index share structure and placeholders."""
    if arm not in (ARM_NEUTRAL, ARM_ASSERTION):
        raise ValueError(f"unknown confirm arm {arm!r}")
    path = _CONFIRM_DIR / f"follow_up_{arm}_{_variant_index(wording_variant)}.txt"
    task = world.task
    return (
        path.read_text(encoding="utf-8")
        .strip()
        .format(
            task_id=task.task_id,
            report_version=task.report.report_version,
            destination=task.destination,
        )
    )
