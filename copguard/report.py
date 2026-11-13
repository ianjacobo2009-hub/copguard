"""Plain-text and Markdown versions of an analysis (for copy, export and the command line)."""
from __future__ import annotations

from datetime import datetime

from .models import Analysis


def cop(x: float) -> str:
    return ("-" if x < 0 else "") + f"${abs(x):,.0f}".replace(",", ".")


def trm(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def markdown(a: Analysis) -> str:
    g, r, o = a.garch, a.risk, a.outlook
    side = "receivable / investment (long USD)" if a.side == "receivable" else "payable (short USD)"
    L = [
        f"# COPGuard risk report",
        f"{datetime.now():%Y-%m-%d %H:%M} · data: {a.data_source}, {a.n_obs:,} days to {a.data_last_date}",
        "",
        f"**Exposure:** USD {a.exposure_usd:,.0f} {side} · **horizon:** {a.days} days · "
        f"**confidence:** {a.confidence:.0%} · **TRM:** {trm(a.spot)}",
        f"**COP equivalent:** {cop(a.exposure_usd * a.spot)}",
        "",
        "## Value at Risk",
        f"- VaR {a.confidence:.0%}: **{cop(r.var)}** (USD {r.var / a.spot:,.0f})",
        f"- Expected shortfall (CVaR): {cop(r.cvar)}",
        f"- Parametric (normal) VaR: {cop(r.parametric_var)}",
        f"- Horizon volatility: {r.horizon_vol:.2%}",
        "",
        "## Volatility (GARCH(1,1))",
        f"- Current {g.current_vol_annual:.2%} a year · long-run {g.long_run_vol_annual:.2%} · "
        f"persistence {g.persistence:.3f} · half-life {g.half_life_days:.0f} days",
        f"- Regime: **{a.regime.name}** ({a.regime.score}/100). {a.regime.note}",
        "",
        "## Monte Carlo (10,000 paths, filtered historical simulation)",
        "| Percentile | TRM | Change |", "|---|---|---|",
        *[f"| P{p} | {trm(v)} | {v / a.spot - 1:+.2%} |" for p, v in r.percentiles.items()],
        f"P(TRM lower) {r.p_down:.1%} · P(TRM higher) {r.p_up:.1%}",
        "",
        "## Hedging",
        f"- Recommended hedge: **{a.hedge.ratio:.0%}**. {a.hedge.explanation}",
        f"- VaR unhedged {cop(a.hedge.var_unhedged)} → hedged {cop(a.hedge.var_hedged)}",
        f"- Forward {trm(a.carry.forward)} ({a.carry.forward_points:+.2f} COP, {a.carry.forward_premium:+.2f}% a year); "
        f"forward points {'earned' if a.hedge.forward_pickup_cop >= 0 else 'paid'} on the hedged amount: {cop(abs(a.hedge.forward_pickup_cop))}",
        f"- At-the-money option premium for the hedged amount: {cop(a.hedge.put_premium_cop)}",
        "",
        "## Historical stress tests (real TRM moves)",
        "| Scenario | From | To | Move against you | Loss | Implied TRM |", "|---|---|---|---|---|---|",
        *[f"| {s.name} | {s.start} | {s.end} | {s.move:+.1%} | {cop(s.loss_cop)} | {trm(s.implied_trm)} |" for s in a.stress],
        "",
        f"## Outlook: TRM {o.direction} (confidence {o.confidence:.0%})",
        *[f"- {s.name} ({s.score:+d}): {s.detail}" for s in o.signals],
        "",
        "| Scenario | TRM | Probability | Change |", "|---|---|---|---|",
        *[f"| {n} | {trm(t)} | {p:.0%} | {c:+.2%} |" for n, t, p, c in o.scenarios],
        "",
        "_Statistical estimates from historical data. Not financial advice._",
    ]
    return "\n".join(L)
