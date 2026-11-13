"""Market data for COPGuard.

The official TRM (Tasa Representativa del Mercado) history comes from Colombia's open-data portal, published by the
Superintendencia Financiera. It needs no API key. The series is cached locally so the app also works offline.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

TRM_URL = "https://www.datos.gov.co/resource/32sa-8pi3.json"
CACHE_DIR = Path(os.environ.get("COPGUARD_HOME", Path.home() / ".copguard"))
CACHE_FILE = CACHE_DIR / "trm_history.csv"
HEADERS = {"User-Agent": "COPGuard/1.0 (desktop risk app)"}


@dataclass
class TRMSeries:
    rates: pd.Series          # COP per USD, indexed by date, one value per business day
    source: str               # where the data came from
    updated: datetime         # when it was fetched

    @property
    def last(self) -> float:
        return float(self.rates.iloc[-1])

    @property
    def last_date(self) -> date:
        return self.rates.index[-1].date()

    @property
    def log_returns(self) -> np.ndarray:
        return np.diff(np.log(self.rates.to_numpy()))


def _download(since: str = "2006-01-01") -> pd.Series:
    rows, offset, page = [], 0, 50000
    while True:
        params = {
            "$select": "valor,vigenciadesde,vigenciahasta",
            "$where": f"vigenciadesde >= '{since}T00:00:00'",
            "$order": "vigenciadesde ASC",
            "$limit": page,
            "$offset": offset,
        }
        r = requests.get(TRM_URL, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        batch = r.json()
        rows += batch
        if len(batch) < page:
            break
        offset += page
    if not rows:
        raise RuntimeError("The TRM service returned no data")
    df = pd.DataFrame(rows)
    # Each TRM is in force from vigenciadesde to vigenciahasta (a Saturday rate covers Saturday to Monday).
    # Expand every record to the days it covers, so each business day gets the rate actually in force.
    start = pd.to_datetime(df["vigenciadesde"]).dt.normalize()
    end = pd.to_datetime(df["vigenciahasta"]).dt.normalize().fillna(start)
    days, vals = [], []
    for a, b, v in zip(start, end, df["valor"].astype(float)):
        for d in pd.date_range(a, max(a, b), freq="D"):
            days.append(d); vals.append(v)
    return _clean(pd.Series(vals, index=pd.DatetimeIndex(days)))


def _clean(s: pd.Series) -> pd.Series:
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s = s[s > 0]
    # A TRM published on a Saturday stays in force over the weekend and the holiday. Keep one value per
    # business day so returns are daily moves, not repeated zeros.
    s = s[s.index.dayofweek < 5]
    s = s[s.diff().fillna(1) != 0]   # drop weekday holidays, which repeat the previous rate
    s.name = "TRM"
    return s


def load_trm(refresh: bool = True) -> TRMSeries:
    """Returns the TRM history. Downloads it if possible, otherwise falls back to the local cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if refresh:
        try:
            s = _download()
            s.to_csv(CACHE_FILE, header=True)
            return TRMSeries(s, "datos.gov.co (Superintendencia Financiera)", datetime.now())
        except Exception:
            if not CACHE_FILE.exists():
                raise
    if CACHE_FILE.exists():
        s = pd.read_csv(CACHE_FILE, index_col=0, parse_dates=True).iloc[:, 0]
        updated = datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
        return TRMSeries(_clean(s), "local cache (offline)", updated)
    raise RuntimeError("No TRM data: no internet connection and no local cache yet")


def synthetic_trm(days: int = 4000, seed: int = 7) -> TRMSeries:
    """A GARCH-like synthetic series, used by the tests and the demo mode when there is no data."""
    rng = np.random.default_rng(seed)
    omega, alpha, beta = 2e-6, 0.08, 0.90
    var = omega / (1 - alpha - beta)
    r = np.empty(days)
    for t in range(days):
        r[t] = np.sqrt(var) * rng.standard_normal()
        var = omega + alpha * r[t] ** 2 + beta * var
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days + 1)
    rates = pd.Series(3000 * np.exp(np.concatenate([[0], np.cumsum(r)])), index=idx, name="TRM")
    return TRMSeries(rates, "synthetic demo data", datetime.now())


SETTINGS_FILE = CACHE_DIR / "settings.json"
DEFAULT_SETTINGS = {"col_rate": 9.25, "us_rate": 3.75}


def load_settings() -> dict:
    try:
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text())}
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(values: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(values, indent=2))
