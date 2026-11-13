import numpy as np
import pytest

from copguard.data import synthetic_trm
from copguard.models import (carry_analysis, fit_garch, garman_kohlhagen, run_analysis, simulate_paths,
                             stress_tests)


@pytest.fixture(scope="module")
def trm():
    return synthetic_trm(days=3000, seed=3)


def test_garch_recovers_parameters(trm):
    g = fit_garch(trm.log_returns)
    assert 0.03 < g.alpha < 0.15
    assert 0.80 < g.beta < 0.96
    assert g.persistence < 1


def test_simulation_is_centered(trm):
    g = fit_garch(trm.log_returns)
    term = simulate_paths(g, 4000.0, 30, 20000, seed=1)
    assert abs(np.median(term) / 4000 - 1) < 0.01


def test_var_and_cvar_ordering(trm):
    a = run_analysis(trm, 100000, 30, 0.95)
    assert a.risk.var > 0
    assert a.risk.cvar >= a.risk.var
    a99 = run_analysis(trm, 100000, 30, 0.99)
    assert a99.risk.var > a.risk.var


def test_scenarios_add_up_to_one(trm):
    a = run_analysis(trm, 100000, 30, 0.95)
    assert sum(p for _, _, p, _ in a.outlook.scenarios) == pytest.approx(1.0)
    lo, base, hi = (t for _, t, _, _ in a.outlook.scenarios)
    assert lo < base < hi


def test_hedge_reduces_var(trm):
    a = run_analysis(trm, 100000, 30, 0.95)
    assert a.hedge.var_hedged < a.hedge.var_unhedged


def test_payable_is_mirror(trm):
    r = run_analysis(trm, 100000, 30, 0.95, side="receivable")
    p = run_analysis(trm, 100000, 30, 0.95, side="payable")
    assert np.allclose(r.risk.pnl, -p.risk.pnl)


def test_forward_above_spot_when_colombian_rates_higher():
    c = carry_analysis(4000, 90, 9.0, 4.0, 1000)
    assert c.forward > 4000 and c.forward_points > 0


def test_put_call_parity():
    s, k, d, v, rd, rf = 4000, 4100, 90, 0.12, 9.0, 4.0
    c = garman_kohlhagen(s, k, d, v, rd, rf, call=True)
    p = garman_kohlhagen(s, k, d, v, rd, rf, call=False)
    t = d / 365
    assert c - p == pytest.approx(s * np.exp(-rf / 100 * t) - k * np.exp(-rd / 100 * t), rel=1e-9)


def test_stress_moves_are_against_position(trm):
    for row in stress_tests(trm.rates, 1000, 4000, "receivable"):
        assert row.move <= 0
