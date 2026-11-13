"""COPGuard's quantitative models. Every number shown in the app is computed here from the TRM history.

Conventions: the exposure is a USD amount held by someone who thinks in COP (a receivable, an investment, or with
side="payable" a debt). A long-USD position loses when the TRM falls (the peso appreciates).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

TRADING_DAYS = 252


# ---------------------------------------------------------------- GARCH(1,1)
@dataclass
class Garch:
    mu: float
    omega: float
    alpha: float
    beta: float
    last_var: float          # conditional variance for tomorrow
    loglik: float
    resid_std: np.ndarray    # standardized residuals (used for filtered historical simulation)

    @property
    def persistence(self) -> float:
        return self.alpha + self.beta

    @property
    def long_run_var(self) -> float:
        return self.omega / (1 - self.persistence)

    @property
    def long_run_vol_annual(self) -> float:
        return float(np.sqrt(self.long_run_var * TRADING_DAYS))

    @property
    def current_vol_annual(self) -> float:
        return float(np.sqrt(self.last_var * TRADING_DAYS))

    @property
    def half_life_days(self) -> float:
        return float(np.log(0.5) / np.log(self.persistence))

    def forecast_var(self, days: int) -> np.ndarray:
        """Expected daily variance for each of the next `days` days (mean reverting to the long-run level)."""
        k = np.arange(days)
        return self.long_run_var + self.persistence ** k * (self.last_var - self.long_run_var)


def _garch_filter(r, mu, omega, alpha, beta):
    e = r - mu
    var = np.empty(len(r) + 1)
    var[0] = e.var()
    for t in range(len(r)):
        var[t + 1] = omega + alpha * e[t] ** 2 + beta * var[t]
    return e, var


def fit_garch(returns: np.ndarray) -> Garch:
    """Fits GARCH(1,1) with normal quasi-maximum likelihood."""
    r = np.asarray(returns, dtype=float)
    scale = 100.0                       # optimise in percent returns for numerical stability
    x = r * scale
    v0 = x.var()

    def nll(p):
        mu, omega, alpha, beta = p
        if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 0.9995:
            return 1e10
        e, var = _garch_filter(x, mu, omega, alpha, beta)
        v = var[:-1]
        return 0.5 * np.sum(np.log(2 * np.pi * v) + e ** 2 / v)

    best = None
    for a0, b0 in ((0.05, 0.90), (0.10, 0.85), (0.15, 0.80)):
        start = [x.mean(), v0 * (1 - a0 - b0), a0, b0]
        res = minimize(nll, start, method="Nelder-Mead", options={"maxiter": 4000, "xatol": 1e-8, "fatol": 1e-8})
        if best is None or res.fun < best.fun:
            best = res
    mu, omega, alpha, beta = best.x
    e, var = _garch_filter(x, mu, omega, alpha, beta)
    return Garch(
        mu=mu / scale, omega=omega / scale ** 2, alpha=alpha, beta=beta,
        last_var=var[-1] / scale ** 2, loglik=-best.fun, resid_std=e / np.sqrt(var[:-1]),
    )


# ---------------------------------------------------------------- Monte Carlo
def simulate_paths(g: Garch, spot: float, days: int, n_paths: int = 10000, seed: int | None = 42,
                   drift: float = 0.0) -> np.ndarray:
    """GARCH filtered historical simulation: shocks are resampled from the model's own standardized residuals,
    so fat tails and asymmetry in the real data carry into the paths. Returns the TRM at the horizon."""
    rng = np.random.default_rng(seed)
    z_pool = g.resid_std[np.isfinite(g.resid_std)]
    z_pool = (z_pool - z_pool.mean()) / z_pool.std()
    var = np.full(n_paths, g.last_var)
    logp = np.zeros(n_paths)
    for _ in range(days):
        z = rng.choice(z_pool, size=n_paths)
        r = drift + np.sqrt(var) * z
        logp += r
        var = g.omega + g.alpha * (np.sqrt(var) * z) ** 2 + g.beta * var
    return spot * np.exp(logp)


# ---------------------------------------------------------------- Risk measures
@dataclass
class RiskResult:
    pnl: np.ndarray                    # P&L in COP for each simulated path
    var: float                         # Value at Risk (positive number = loss) in COP
    cvar: float                        # Expected Shortfall in COP
    parametric_var: float              # normal VaR using the GARCH horizon volatility
    horizon_vol: float                 # volatility of the log return over the horizon
    percentiles: dict = field(default_factory=dict)
    p_down: float = 0.0                # probability the TRM ends below today's
    p_up: float = 0.0


def risk_measures(exposure_usd: float, spot: float, terminal: np.ndarray, g: Garch, days: int,
                  confidence: float, side: str = "receivable") -> RiskResult:
    sign = 1 if side == "receivable" else -1
    pnl = sign * exposure_usd * (terminal - spot)
    q = np.quantile(pnl, 1 - confidence)
    var = max(0.0, -q)
    tail = pnl[pnl <= q]
    cvar = max(0.0, -tail.mean()) if len(tail) else var
    hv = float(np.sqrt(g.forecast_var(days).sum()))
    parametric = exposure_usd * spot * norm.ppf(confidence) * hv
    pct = {p: float(np.percentile(terminal, p)) for p in (1, 5, 10, 25, 50, 75, 90, 95, 99)}
    return RiskResult(pnl, var, cvar, parametric, hv, pct,
                      float((terminal < spot).mean()), float((terminal > spot).mean()))


# ---------------------------------------------------------------- Regime
@dataclass
class Regime:
    name: str
    score: int                  # 0 calm ... 100 crisis
    vol_90d: float
    vol_long: float
    hedge_multiplier: float
    note: str


def detect_regime(returns: np.ndarray, g: Garch) -> Regime:
    vol_90 = float(np.std(returns[-90:]) * np.sqrt(TRADING_DAYS))
    hist = np.array([np.std(returns[i - 90:i]) for i in range(90, len(returns), 5)]) * np.sqrt(TRADING_DAYS)
    score = int(round(100 * (hist < vol_90).mean())) if len(hist) else 50
    if score >= 90:
        return Regime("CRISIS", score, vol_90, g.long_run_vol_annual, 1.3,
                      "Volatility is in the top 10% of its history. Hedge more and shorten the horizon.")
    if score >= 75:
        return Regime("ELEVATED", score, vol_90, g.long_run_vol_annual, 1.15,
                      "Volatility is above normal. Consider hedging a larger share of the exposure.")
    if score <= 20:
        return Regime("CALM", score, vol_90, g.long_run_vol_annual, 0.9,
                      "Volatility is unusually low. Calm periods tend to end suddenly, so keep core hedges.")
    return Regime("NORMAL", score, vol_90, g.long_run_vol_annual, 1.0,
                  "Volatility is within its normal historical range. Standard hedging applies.")


# ---------------------------------------------------------------- Carry, forward and hedging
@dataclass
class Carry:
    col_rate: float
    us_rate: float
    differential: float
    forward: float              # covered interest parity forward for the horizon
    forward_points: float       # forward minus spot, in COP
    forward_premium: float      # annualised, %
    carry_cop: float            # interest pick-up of holding COP instead of USD over the horizon, in COP


def carry_analysis(spot: float, days: int, col_rate: float, us_rate: float, exposure_usd: float) -> Carry:
    t = days / 365
    fwd = spot * (1 + col_rate / 100 * t) / (1 + us_rate / 100 * t)
    prem = (fwd / spot - 1) / t * 100
    return Carry(col_rate, us_rate, col_rate - us_rate, fwd, fwd - spot, prem,
                 exposure_usd * spot * (col_rate - us_rate) / 100 * t)


@dataclass
class Hedge:
    ratio: float                # recommended share of the exposure to hedge
    forward_pickup_cop: float   # gain (+) or cost (-) of locking the forward instead of today's spot, hedged amount
    var_unhedged: float
    var_hedged: float
    put_premium_cop: float      # Garman-Kohlhagen at-the-money option cost for the hedged amount
    explanation: str


def garman_kohlhagen(spot, strike, days, vol_annual, r_dom, r_for, call: bool):
    t = days / 365
    if t <= 0 or vol_annual <= 0:
        return max(0.0, (spot - strike) if call else (strike - spot))
    rd, rf = r_dom / 100, r_for / 100
    d1 = (np.log(spot / strike) + (rd - rf + vol_annual ** 2 / 2) * t) / (vol_annual * np.sqrt(t))
    d2 = d1 - vol_annual * np.sqrt(t)
    if call:
        return spot * np.exp(-rf * t) * norm.cdf(d1) - strike * np.exp(-rd * t) * norm.cdf(d2)
    return strike * np.exp(-rd * t) * norm.cdf(-d2) - spot * np.exp(-rf * t) * norm.cdf(-d1)


def hedge_plan(exposure_usd, spot, risk: RiskResult, regime: Regime, carry: Carry, g: Garch, days: int,
               confidence: float, side: str) -> Hedge:
    base = {0.99: 0.8, 0.95: 0.65, 0.90: 0.5}.get(round(confidence, 2), 0.65)
    ratio = float(np.clip(base * regime.hedge_multiplier, 0.25, 1.0))
    sign = 1 if side == "receivable" else -1
    hedged_pnl = risk.pnl * (1 - ratio)   # a forward fixes the hedged part
    q = np.quantile(hedged_pnl, 1 - confidence)
    var_h = max(0.0, -q)
    # Locking a forward gives the hedged amount at F instead of S: a receivable earns the forward points,
    # a payable pays them.
    fwd_pickup = sign * exposure_usd * ratio * carry.forward_points
    vol = float(np.sqrt(g.forecast_var(days).mean() * TRADING_DAYS))
    put = garman_kohlhagen(spot, spot, days, vol, carry.col_rate, carry.us_rate, call=(side == "payable"))
    put_cost = put * exposure_usd * ratio
    who = "receive" if side == "receivable" else "pay"
    exp = (f"Hedge about {ratio:.0%} of the exposure. You {who} dollars, so a "
           f"{'falling' if side == 'receivable' else 'rising'} TRM is the risk. A forward locks "
           f"{carry.forward:,.2f} for the hedged part; an at-the-money "
           f"{'USD put' if side == 'receivable' else 'USD call'} keeps the upside for an upfront premium. "
           f"Regime: {regime.name.lower()} (×{regime.hedge_multiplier}).")
    return Hedge(ratio, fwd_pickup, risk.var, var_h, put_cost, exp)


# ---------------------------------------------------------------- Historical stress tests
STRESS_WINDOWS = [
    ("Lehman / global financial crisis", "2008-08-01", "2009-03-31"),
    ("Oil price collapse", "2014-07-01", "2016-02-29"),
    ("COVID-19 crash", "2020-02-15", "2020-04-30"),
    ("Fed tightening 2022", "2022-01-01", "2022-11-30"),
    ("Peso rally 2023", "2023-01-01", "2023-12-31"),
]


@dataclass
class StressRow:
    name: str
    start: str
    end: str
    move: float               # worst move against the position inside the window, as a fraction
    days: int                 # trading days from start to the worst point
    loss_cop: float
    implied_trm: float


def stress_tests(rates, exposure_usd: float, spot: float, side: str) -> list[StressRow]:
    """For each crisis window, finds the largest real TRM move against the position (peak-to-trough for a
    receivable, trough-to-peak for a payable) and applies it to today's exposure."""
    rows = []
    sign = 1 if side == "receivable" else -1
    for name, a, b in STRESS_WINDOWS:
        w = rates.loc[a:b]
        if len(w) < 5:
            continue
        v = w.to_numpy()
        if sign > 0:
            run_max = np.maximum.accumulate(v)
            dd = v / run_max - 1
            i = int(dd.argmin()); j = int(v[:i + 1].argmax()); move = dd[i]
        else:
            run_min = np.minimum.accumulate(v)
            du = v / run_min - 1
            i = int(du.argmax()); j = int(v[:i + 1].argmin()); move = -du[i]
        implied = spot * (1 + move) if sign > 0 else spot * (1 - move)
        loss = exposure_usd * spot * abs(move) if move < 0 else 0.0
        rows.append(StressRow(name, str(w.index[j].date()), str(w.index[i].date()), move, i - j, loss, implied))
    r = np.diff(np.log(rates.to_numpy()))
    k = int(r.argmin() if sign > 0 else r.argmax())
    worst = float(np.expm1(r[k])) if sign > 0 else -float(np.expm1(r[k]))
    rows.append(StressRow("Worst single day on record", str(rates.index[k + 1].date()), str(rates.index[k + 1].date()),
                          worst, 1, exposure_usd * spot * abs(worst), spot * (1 + worst) if sign > 0 else spot * (1 - worst)))
    return rows


# ---------------------------------------------------------------- Directional view
@dataclass
class Signal:
    name: str
    score: int        # -2 (TRM down / peso up) ... +2 (TRM up / peso down)
    detail: str


@dataclass
class Outlook:
    signals: list
    direction: str          # UP / DOWN / NEUTRAL (direction of the TRM)
    confidence: float       # 0.5 ... 0.7, deliberately modest
    expected_move: float    # fraction over the horizon (MC median incl. the tilt)
    scenarios: list         # (name, trm, probability, change); probabilities add up to 1


def outlook(rates, g: Garch, carry: Carry, days: int, n_paths: int = 10000, seed: int = 7) -> Outlook:
    v = rates.to_numpy()
    r = np.diff(np.log(v))
    spot = v[-1]
    sig = []

    mom = v[-1] / v[-21] - 1 if len(v) > 21 else 0
    mom3 = v[-1] / v[-63] - 1 if len(v) > 63 else 0
    s = int(np.clip(np.sign(mom) + np.sign(mom3), -2, 2))
    sig.append(Signal("Momentum", s, f"1-month {mom:+.2%}, 3-month {mom3:+.2%}. FX trends tend to persist for weeks."))

    ma = v[-200:].mean()
    z = (spot - ma) / (np.std(v[-200:]) or 1)
    s = int(-np.clip(np.round(z), -2, 2))
    sig.append(Signal("Mean reversion", s, f"TRM is {z:+.2f}σ from its 200-day average ({ma:,.0f}). Stretched moves tend to fade."))

    s = -2 if carry.differential > 4 else -1 if carry.differential > 2 else 0
    sig.append(Signal("Carry", s, f"Colombia {carry.col_rate:.2f}% vs US {carry.us_rate:.2f}% "
                                  f"= {carry.differential:+.2f} pts. A wide gap attracts flows into pesos."))

    ratio = np.sqrt(g.last_var) / np.sqrt(g.long_run_var)
    s = 1 if ratio > 1.2 else -1 if ratio < 0.85 else 0
    sig.append(Signal("Volatility regime", s, f"GARCH vol is {ratio:.2f}× its long-run level. "
                                              "Stress usually comes with a weaker peso."))

    yr = v[-1] / v[-252] - 1 if len(v) > 252 else 0
    s = 1 if yr < -0.10 else -1 if yr > 0.10 else 0
    sig.append(Signal("1-year valuation", s, f"TRM {yr:+.1%} over a year. Big moves tend to partly reverse."))

    total = sum(x.score for x in sig)
    max_total = 2 * len(sig)
    tilt = total / max_total                                  # -1 ... +1
    confidence = 0.5 + 0.2 * abs(tilt)                        # never above 70%: FX direction is hard
    direction = "UP" if tilt > 0.1 else "DOWN" if tilt < -0.1 else "NEUTRAL"
    # Express the view as a small drift (at most a quarter of a horizon standard deviation) and simulate.
    hv = np.sqrt(g.forecast_var(days).sum())
    drift = 0.25 * tilt * hv / days
    term = simulate_paths(g, spot, days, n_paths, seed=seed, drift=drift)
    lo, hi = np.percentile(term, [20, 80])
    p_bear = float((term < lo).mean()); p_bull = float((term > hi).mean())
    scen = [
        ("Peso strengthens (TRM low)", float(np.median(term[term < lo])), p_bear, float(np.median(term[term < lo]) / spot - 1)),
        ("Base case", float(np.median(term[(term >= lo) & (term <= hi)])), 1 - p_bear - p_bull,
         float(np.median(term[(term >= lo) & (term <= hi)]) / spot - 1)),
        ("Peso weakens (TRM high)", float(np.median(term[term > hi])), p_bull, float(np.median(term[term > hi]) / spot - 1)),
    ]
    return Outlook(sig, direction, confidence, float(np.median(term) / spot - 1), scen)


# ---------------------------------------------------------------- Everything together
@dataclass
class Analysis:
    spot: float
    exposure_usd: float
    days: int
    confidence: float
    side: str
    garch: Garch
    terminal: np.ndarray
    risk: RiskResult
    regime: Regime
    carry: Carry
    hedge: Hedge
    stress: list
    outlook: Outlook
    vol_bands: list           # (day, daily sigma, low95, high95, low99, high99)
    data_source: str
    data_last_date: str
    n_obs: int


def run_analysis(trm, exposure_usd: float, days: int, confidence: float, side: str = "receivable",
                 spot: float | None = None, col_rate: float = 9.25, us_rate: float = 3.75,
                 n_paths: int = 10000, seed: int = 42) -> Analysis:
    rates = trm.rates
    r = np.diff(np.log(rates.to_numpy()))
    spot = float(spot or rates.iloc[-1])
    g = fit_garch(r[-2520:])                     # last ~10 years
    term = simulate_paths(g, spot, days, n_paths, seed)
    risk = risk_measures(exposure_usd, spot, term, g, days, confidence, side)
    reg = detect_regime(r, g)
    car = carry_analysis(spot, days, col_rate, us_rate, exposure_usd)
    hed = hedge_plan(exposure_usd, spot, risk, reg, car, g, days, confidence, side)
    st = stress_tests(rates, exposure_usd, spot, side)
    out = outlook(rates, g, car, days)
    fv = g.forecast_var(min(days, 10))
    bands = []
    cum = 0.0
    for d, v in enumerate(fv, 1):
        cum += v
        s = np.sqrt(cum)
        bands.append((d, float(np.sqrt(v)), spot * np.exp(-1.96 * s), spot * np.exp(1.96 * s),
                      spot * np.exp(-2.576 * s), spot * np.exp(2.576 * s)))
    return Analysis(spot, exposure_usd, days, confidence, side, g, term, risk, reg, car, hed, st, out, bands,
                    trm.source, str(trm.last_date), len(rates))
