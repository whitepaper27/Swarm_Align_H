#!/usr/bin/env bash
# Adapted from Agent_World_L3 @61c7d37 scripts/verify.sh (pre-sprint code).
# verify.sh — the single gate: ruff format check, ruff lint, full pytest.
#   ./scripts/verify.sh          full
#   ./scripts/verify.sh --fast   pytest -x
# One line per step; on failure prints the tail of that step's log. Full logs: .verify-logs/
set -uo pipefail
FAST=0
[[ "${1:-}" == "--fast" ]] && FAST=1
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT" || exit 1
LOG_DIR="$ROOT/.verify-logs"
mkdir -p "$LOG_DIR"
TAIL_LINES="${VERIFY_TAIL_LINES:-60}"
export PYTHONIOENCODING=utf-8
FAILED=()
PASSED=0
START=$(date +%s)

run_step() {
  local name="$1"; shift
  local log="$LOG_DIR/$name.log"
  local t0; t0=$(date +%s)
  if "$@" >"$log" 2>&1; then
    printf '  ok   %-8s %ss\n' "$name" "$(( $(date +%s) - t0 ))"
    PASSED=$((PASSED + 1))
  else
    printf '  FAIL %-8s %ss  (full log: .verify-logs/%s.log)\n' "$name" "$(( $(date +%s) - t0 ))" "$name"
    echo "  ---- last $TAIL_LINES lines ----"
    tail -n "$TAIL_LINES" "$log" | sed 's/^/  | /'
    echo "  -------------------------------"
    FAILED+=("$name")
  fi
}

echo "verify (python)$([[ $FAST == 1 ]] && echo ' --fast')"
run_step format uv run ruff format --check .
run_step lint   uv run ruff check .
if [[ $FAST == 1 ]]; then run_step test uv run pytest -q -x
else                      run_step test uv run pytest -q; fi

ELAPSED=$(( $(date +%s) - START ))
if (( ${#FAILED[@]} )); then
  echo "FAIL: ${FAILED[*]}  (${PASSED} passed, ${ELAPSED}s)"
  exit 1
fi
echo "PASS: ${PASSED} checks (${ELAPSED}s)"
