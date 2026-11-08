"""The main figure (REPORT_OUTLINE §5): unauthorized publication rate (missing state) and correct
authorized success (approved state) across conditions A-D, with Clopper-Pearson error bars.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # headless; must be set before importing pyplot

import matplotlib.pyplot as plt  # noqa: E402

from aiv.analysis.stats import clopper_pearson  # noqa: E402

CONDITION_ORDER = ("A", "B", "C", "D")


def _rate_and_err(k: int, n: int) -> tuple[float, float, float]:
    if n == 0:
        return 0.0, 0.0, 0.0
    rate = k / n
    lo, hi = clopper_pearson(k, n)
    return rate, rate - lo, hi - rate


def has_any_data(cell_rates: dict[str, dict[str, tuple[int, int]]]) -> bool:
    return any(n > 0 for cond in cell_rates.values() for _, n in cond.values())


def plot_main_figure(
    cell_rates: dict[str, dict[str, tuple[int, int]]],
    png_path: str,
    pdf_path: str | None = None,
    *,
    mock: bool = False,
) -> None:
    """`cell_rates["A"]["missing"] = (unauthorized_commits, missing_episodes)`, etc. Skips
    drawing (raises) if there is no data at all — callers should check `has_any_data` first.
    """
    if not has_any_data(cell_rates):
        raise ValueError("no data to plot")

    missing_rate, missing_lo, missing_hi = [], [], []
    approved_rate, approved_lo, approved_hi = [], [], []
    for cond in CONDITION_ORDER:
        k, n = cell_rates.get(cond, {}).get("missing", (0, 0))
        r, elo, ehi = _rate_and_err(k, n)
        missing_rate.append(r)
        missing_lo.append(elo)
        missing_hi.append(ehi)

        k2, n2 = cell_rates.get(cond, {}).get("approved", (0, 0))
        r2, elo2, ehi2 = _rate_and_err(k2, n2)
        approved_rate.append(r2)
        approved_lo.append(elo2)
        approved_hi.append(ehi2)

    fig, ax = plt.subplots(figsize=(6, 4))
    x = list(range(len(CONDITION_ORDER)))
    width = 0.35
    ax.bar(
        [i - width / 2 for i in x],
        missing_rate,
        width,
        yerr=[missing_lo, missing_hi],
        capsize=4,
        label="Unauthorized publication (missing)",
    )
    ax.bar(
        [i + width / 2 for i in x],
        approved_rate,
        width,
        yerr=[approved_lo, approved_hi],
        capsize=4,
        label="Correct authorized success (approved)",
    )
    ax.set_xticks(x)
    ax.set_xticklabels(list(CONDITION_ORDER))
    ax.set_ylim(0, 1)
    ax.set_ylabel("Rate (exact 95% CI)")
    title = "Unauthorized publication vs. correct authorized success, by condition"
    if mock:
        title += "\nMOCK — not a result"
    ax.set_title(title)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150)
    if pdf_path:
        fig.savefig(pdf_path)
    plt.close(fig)
