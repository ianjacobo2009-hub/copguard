"""Run the desktop app, or `python -m copguard --cli --usd 100000 --days 30` for a report in the terminal."""
import argparse


def main():
    p = argparse.ArgumentParser(prog="copguard", description="COP/USD exchange-rate risk analysis")
    p.add_argument("--cli", action="store_true", help="print a report instead of opening the app")
    p.add_argument("--usd", type=float, default=100000, help="USD exposure (default 100000)")
    p.add_argument("--days", type=int, default=30, choices=[1, 10, 30, 90])
    p.add_argument("--confidence", type=float, default=0.95, choices=[0.90, 0.95, 0.99])
    p.add_argument("--side", default="receivable", choices=["receivable", "payable"])
    p.add_argument("--offline", action="store_true", help="use the cached TRM history only")
    a = p.parse_args()
    if a.cli:
        from .data import load_settings, load_trm
        from .models import run_analysis
        from .report import markdown
        s = load_settings()
        trm = load_trm(refresh=not a.offline)
        print(markdown(run_analysis(trm, a.usd, a.days, a.confidence, a.side,
                                    col_rate=s["col_rate"], us_rate=s["us_rate"])))
    else:
        from .app import main as gui
        gui()


if __name__ == "__main__":
    main()
