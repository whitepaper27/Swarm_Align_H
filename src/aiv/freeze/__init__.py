# Adapted from Agent_World_L3 @61c7d37 src/ethwall/freeze/__init__.py (pre-sprint code).
"""Protocol freeze: copy `protocol/draft/**` into `protocol/frozen/<freeze_id>/` with a
`freeze.json` manifest (ARCHITECTURE §11; docs/decisions.md "Spend + live gate").

`create(repo_root, config, *, note="") -> freeze_id` hashes every file under `protocol/draft/`,
copies the same bytes into the frozen directory, and writes `freeze.json` recording those
hashes, the config dict (N/seeds, conditions, wording variants, model ids, generation settings,
retry policy, contrasts, stopping rule — ARCHITECTURE §11, GOAL.md "Statistical design"), the
git commit and the creation timestamp. It refuses to overwrite an existing freeze directory.

`validate(repo_root, freeze_id) -> list[str]` recomputes every hash and reports one fault per
mismatch, missing file or extra file; an empty list means the freeze is intact.

`main(argv)` is the `aiv freeze create|validate` CLI entry point (wired in by `aiv.cli`, which
this module does not import).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiv.contract import canonical_json, sha256_hex

FROZEN_DIR = "protocol/frozen"
DRAFT_DIR = "protocol/draft"
FREEZE_FILE = "freeze.json"

# Default contrasts / stopping rule (GOAL.md "Statistical design", fixed before any data).
DEFAULT_CONFIG: dict[str, Any] = {
    "n_seeds": 20,
    "seeds": list(range(20)),
    "conditions": ["A", "B", "C", "D"],
    "approval_states": ["approved", "missing"],
    "wording_variants": [0, 1, 2],
    "model_ids": {"actor": None},
    "generation_settings": {"max_tokens": 1024, "temperature": 1.0},
    "retry_policy": {
        "retried": ["infra_error"],
        "retry_max": 2,
        "note": "Only infra_error is retried, with state reset. Refusals and invalid "
        "decisions are never retried.",
    },
    "primary_contrasts": [
        "B-A: unauthorized committed publication, missing state",
        "D-B: unauthorized committed publication, missing state",
    ],
    "secondary_contrasts": [
        "C-A: unauthorized committed publication, missing state",
        "(D-C)-(B-A): interaction, missing state, task-level bootstrap over seeds",
    ],
    "multiplicity": "Holm correction across the two primary contrasts.",
    "stopping_rule": (
        "Never stop a frozen batch early because results look favorable. Pilot and discovery "
        "are never pooled into confirmatory estimates. At the cutoff, report coverage by "
        "condition."
    ),
}


class FreezeError(RuntimeError):
    """A freeze operation refused: existing directory, bad id, or an unreadable manifest."""


def _root(repo_root: str | Path) -> Path:
    return Path(repo_root)


def _draft_dir(root: Path) -> Path:
    return root / DRAFT_DIR


def _frozen_dir(root: Path, freeze_id: str) -> Path:
    if not freeze_id or freeze_id in (".", "..") or any(c in freeze_id for c in "/\\"):
        raise ValueError(f"invalid freeze id {freeze_id!r}")
    return root / FROZEN_DIR / freeze_id


def _draft_files(draft: Path) -> dict[str, bytes]:
    """`{posix relative path: content bytes}` for every file under `draft`, read once."""
    if not draft.exists():
        return {}
    out: dict[str, bytes] = {}
    for p in sorted(draft.rglob("*")):
        if p.is_file():
            out[p.relative_to(draft).as_posix()] = p.read_bytes()
    return out


def _git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    commit = result.stdout.strip()
    return commit or None


def _content_hash(file_hashes: Mapping[str, str], config: dict[str, Any]) -> str:
    return sha256_hex(canonical_json({"files": file_hashes, "config": config}))


def create(
    repo_root: str | Path,
    config: dict[str, Any],
    *,
    note: str = "",
    now: datetime | None = None,
) -> str:
    """Freeze `protocol/draft/**` plus `config` into a new `protocol/frozen/<freeze_id>/`.

    Returns the new `freeze_id`. Raises `FileExistsError` if that exact id already exists
    (same draft contents and config, frozen in the same minute) — a freeze directory is never
    overwritten; a real change (even just the config) needs a new freeze.
    """
    root = _root(repo_root)
    draft = _draft_dir(root)
    files = _draft_files(draft)
    file_hashes = {rel: sha256_hex(data) for rel, data in files.items()}
    config = json.loads(canonical_json(config))  # normalize to plain jsonable dict
    content_hash = _content_hash(file_hashes, config)
    ts = now if now is not None else datetime.now(timezone.utc)
    freeze_id = f"F{ts.strftime('%Y%m%d-%H%M')}-{content_hash[:8]}"

    frozen_root = root / FROZEN_DIR
    frozen_root.mkdir(parents=True, exist_ok=True)
    frozen_dir = frozen_root / freeze_id
    frozen_dir.mkdir(exist_ok=False)  # atomic refuse-overwrite: FileExistsError if present

    for rel, data in files.items():
        dest = frozen_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    record = {
        "freeze_id": freeze_id,
        "created_utc": ts.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(root),
        "content_hash": content_hash,
        "note": note,
        "config": config,
        "files": file_hashes,
    }
    freeze_path = frozen_dir / FREEZE_FILE
    with open(freeze_path, "x", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return freeze_id


def validate(repo_root: str | Path, freeze_id: str) -> list[str]:
    """Recompute every hashed file and the content hash; return one message per fault.

    An empty list means every hashed file is present and matches, nothing extra is in the
    directory, and the recorded id and content hash are self-consistent.
    """
    root = _root(repo_root)
    frozen_dir = _frozen_dir(root, freeze_id)
    freeze_path = frozen_dir / FREEZE_FILE
    try:
        frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"freeze_missing: {freeze_path} does not exist"]
    except (OSError, ValueError) as exc:
        return [f"freeze_unreadable: {freeze_path}: {exc}"]
    if not isinstance(frozen, dict):
        return [f"freeze_unreadable: {freeze_path}: not a JSON object"]

    problems: list[str] = []
    if frozen.get("freeze_id") != freeze_id:
        problems.append(f"freeze_id: file says {frozen.get('freeze_id')!r}, dir is {freeze_id!r}")

    recorded_files = frozen.get("files") or {}
    if not isinstance(recorded_files, dict):
        problems.append("files: not an object")
        recorded_files = {}
    config = frozen.get("config") or {}
    recomputed_content_hash = _content_hash(recorded_files, config)
    if frozen.get("content_hash") != recomputed_content_hash:
        problems.append(
            f"content_hash: recorded {frozen.get('content_hash')!r} != "
            f"recomputed {recomputed_content_hash!r} (hashes or config were edited)"
        )
    suffix = freeze_id.rsplit("-", 1)[-1]
    if recomputed_content_hash[:8] != suffix:
        problems.append(
            f"freeze_id suffix {suffix!r} does not match the content hash "
            f"{recomputed_content_hash[:8]!r}"
        )

    for rel, expected in recorded_files.items():
        path = frozen_dir / rel
        if not path.is_file():
            problems.append(f"missing_file: {rel}")
            continue
        actual = sha256_hex(path.read_bytes())
        if actual != expected:
            problems.append(f"hash_mismatch: {rel}: frozen {expected!r} != current {actual!r}")

    present = {
        p.relative_to(frozen_dir).as_posix()
        for p in frozen_dir.rglob("*")
        if p.is_file() and p.name != FREEZE_FILE
    }
    extra = present - set(recorded_files)
    for rel in sorted(extra):
        problems.append(f"extra_file: {rel}")

    return problems


def _default_repo_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        top = result.stdout.strip()
        if top:
            return Path(top)
    except (OSError, subprocess.CalledProcessError):
        pass
    return Path.cwd()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aiv freeze")
    parser.add_argument(
        "--repo-root", default=None, help="Repo root (default: git toplevel, else cwd)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="Freeze protocol/draft/** + a config.")
    p_create.add_argument(
        "--config", default=None, help="JSON file with the freeze config (default: built-in)."
    )
    p_create.add_argument("--note", default="")

    p_validate = sub.add_parser("validate", help="Validate an existing freeze.")
    p_validate.add_argument("freeze_id")

    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root) if args.repo_root else _default_repo_root()

    if args.cmd == "create":
        if args.config:
            config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        else:
            config = dict(DEFAULT_CONFIG)
        try:
            freeze_id = create(repo_root, config, note=args.note)
        except FileExistsError as exc:
            print(f"refused: {exc}")
            return 1
        print(freeze_id)
        return 0

    if args.cmd == "validate":
        faults = validate(repo_root, args.freeze_id)
        if faults:
            for f in faults:
                print(f)
            return 1
        print("OK")
        return 0

    return 2  # unreachable: argparse enforces a known subcommand


__all__ = ["FreezeError", "DEFAULT_CONFIG", "create", "validate", "main"]
