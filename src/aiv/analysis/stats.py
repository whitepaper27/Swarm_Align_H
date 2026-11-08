"""Exact statistics for ARCHITECTURE §7a / GOAL "Statistical design". Stdlib only.

- `clopper_pearson`: exact two-sided Clopper-Pearson CI, via bisection on the binomial CDF
  (computed with `math.comb` coefficients and log-space probabilities for numerical stability).
- `mcnemar_exact_p`: exact two-sided McNemar p from discordant counts (exact binomial test,
  p=0.5; two-sided = min(1, 2 * tail)).
- `holm_adjust`: Holm step-down multiplicity correction.
- `seed_bootstrap_ci`: seed-cluster bootstrap (resample seeds with replacement, keeping every
  cell of a seed together), with a fixed RNG seed for determinism and a percentile CI.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence

__all__ = [
    "binom_cdf",
    "clopper_pearson",
    "mcnemar_exact_p",
    "holm_adjust",
    "seed_bootstrap_ci",
]


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p). Exact `math.comb` coefficients, log-space terms."""
    if n < 0:
        raise ValueError("n must be non-negative")
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    log_p = math.log(p)
    log_1mp = math.log1p(-p)
    total = 0.0
    for i in range(k + 1):
        log_term = math.log(math.comb(n, i)) + i * log_p + (n - i) * log_1mp
        total += math.exp(log_term)
    return min(1.0, total)


def _solve_p(
    f: Callable[[float], float], target: float, *, tol: float = 1e-12, max_iter: int = 200
) -> float:
    """Bisection for p in (0, 1) solving f(p) == target, where `f` is non-increasing in p."""
    lo, hi = 1e-14, 1 - 1e-14
    if f(lo) <= target:
        return 0.0
    if f(hi) >= target:
        return 1.0
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        if f(mid) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact two-sided (1 - alpha) Clopper-Pearson CI for k successes out of n trials.

    k=0 -> lower bound is exactly 0.0; k=n -> upper bound is exactly 1.0.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if k < 0 or k > n:
        raise ValueError("k must be within [0, n]")
    lo = 0.0 if k == 0 else _solve_p(lambda p: binom_cdf(k - 1, n, p), 1 - alpha / 2)
    hi = 1.0 if k == n else _solve_p(lambda p: binom_cdf(k, n, p), alpha / 2)
    return lo, hi


def mcnemar_exact_p(b_pos: int, b_neg: int) -> float:
    """Exact two-sided McNemar p-value from discordant counts (exact binomial test, p=0.5)."""
    if b_pos < 0 or b_neg < 0:
        raise ValueError("discordant counts must be non-negative")
    n = b_pos + b_neg
    if n == 0:
        return 1.0
    k = min(b_pos, b_neg)
    tail = binom_cdf(k, n, 0.5)
    return min(1.0, 2 * tail)


def holm_adjust(pvalues: Sequence[float]) -> list[float]:
    """Holm step-down adjustment. Returns adjusted p-values in the SAME order as `pvalues`."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running_max = 0.0
    for rank, idx in enumerate(order):
        adj = min(1.0, pvalues[idx] * (m - rank))
        running_max = max(running_max, adj)
        adjusted[idx] = running_max
    return adjusted


def _percentile(sorted_vals: Sequence[float], pct: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * (pct / 100)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    d0 = sorted_vals[int(f)] * (c - k)
    d1 = sorted_vals[int(c)] * (k - f)
    return d0 + d1


def seed_bootstrap_ci(
    seed_rows: Sequence[object],
    statistic: Callable[[Sequence[object]], float],
    *,
    n_boot: int = 10000,
    alpha: float = 0.05,
    rng_seed: int = 0,
) -> tuple[float, tuple[float, float]]:
    """Seed-cluster bootstrap. `seed_rows` holds one opaque row per seed (e.g. a tuple of that
    seed's values across conditions); resampling draws whole rows, so every cell of a seed stays
    together. `statistic(rows) -> float` computes the point estimate / interaction from a list of
    rows. Deterministic for a fixed `rng_seed`.
    """
    point = statistic(seed_rows)
    n = len(seed_rows)
    if n == 0:
        return point, (point, point)
    rng = random.Random(rng_seed)
    boots = []
    for _ in range(n_boot):
        sample = [seed_rows[rng.randrange(n)] for _ in range(n)]
        boots.append(statistic(sample))
    boots.sort()
    lo = _percentile(boots, alpha / 2 * 100)
    hi = _percentile(boots, (1 - alpha / 2) * 100)
    return point, (lo, hi)
