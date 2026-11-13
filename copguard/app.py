"""COPGuard desktop app (PySide6 / Qt)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PySide6.QtCharts import (QAreaSeries, QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView,
                              QDateTimeAxis, QLineSeries, QValueAxis)
from PySide6.QtCore import QDateTime, QObject, QPointF, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QIcon, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from . import __version__
from .data import load_settings, load_trm, save_settings
from .models import Analysis, run_analysis
from .report import cop, markdown, trm

ASSETS = (Path(getattr(sys, "_MEIPASS", "")) / "assets") if getattr(sys, "frozen", False) \
    else Path(__file__).resolve().parent.parent / "assets"
BG, PANEL, CARD, LINE, TEXT, MUTED = "#0b1220", "#111a2e", "#16213a", "#24314f", "#e6edf7", "#8a9bb8"
BLUE, GREEN, RED, AMBER = "#3b82f6", "#22c55e", "#ef4444", "#f59e0b"

QSS = f"""
* {{ font-family: 'Helvetica Neue', 'Segoe UI', Arial, sans-serif; color: {TEXT}; }}
QMainWindow, QWidget#root {{ background: {BG}; }}
QFrame#panel {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame#card {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 12px; }}
QLabel#muted {{ color: {MUTED}; font-size: 12px; }}
QLabel#kpi {{ font-size: 22px; font-weight: 700; }}
QLabel#h1 {{ font-size: 18px; font-weight: 700; }}
QLabel#h2 {{ font-size: 14px; font-weight: 600; }}
QLabel#pill {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; padding: 6px 12px;
              font-family: Menlo, Consolas, monospace; }}
QPushButton {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 9px; padding: 8px 12px; }}
QPushButton:hover {{ border-color: {BLUE}; }}
QPushButton:checked {{ background: {BLUE}; border-color: {BLUE}; color: white; }}
QPushButton#primary {{ background: {BLUE}; border: none; color: white; font-weight: 600; padding: 11px; }}
QPushButton#primary:disabled {{ background: #2a3b5e; color: {MUTED}; }}
QDoubleSpinBox {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 9px; padding: 7px; font-size: 14px; }}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 9px 16px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {BLUE}; }}
QTableWidget {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; gridline-color: {LINE};
               font-family: Menlo, Consolas, monospace; font-size: 12px; }}
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none; padding: 6px; font-weight: 600; }}
"""


# ------------------------------------------------------------------ background work
class Worker(QObject):
    data_ready = Signal(object)
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, job, **kw):
        super().__init__()
        self.job, self.kw = job, kw

    def run(self):
        try:
            if self.job == "load":
                self.data_ready.emit(load_trm(refresh=True))
            else:
                self.result.emit(run_analysis(**self.kw))
        except Exception as e:  # shown in the UI instead of crashing
            self.failed.emit(str(e))


def card(title: str, value: str = "—", sub: str = "") -> tuple[QFrame, QLabel, QLabel]:
    f = QFrame(objectName="card")
    v = QVBoxLayout(f); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(2)
    t = QLabel(title.upper(), objectName="muted")
    val = QLabel(value, objectName="kpi")
    s = QLabel(sub, objectName="muted"); s.setWordWrap(True)
    v.addWidget(t); v.addWidget(val); v.addWidget(s)
    return f, val, s


def table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionMode(QTableWidget.NoSelection)
    t.setShowGrid(False)
    return t


def fill(t: QTableWidget, rows: list[list[str]], colors: dict | None = None):
    t.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            it = QTableWidgetItem(val)
            numeric = j > 0 and len(val) < 24 and any(ch.isdigit() for ch in val)
            it.setTextAlignment(Qt.AlignVCenter | (Qt.AlignRight if numeric else Qt.AlignLeft))
            if colors and (i, j) in colors:
                it.setForeground(QColor(colors[(i, j)]))
            t.setItem(i, j, it)
    t.resizeRowsToContents()
    h = t.horizontalHeader().height() + sum(t.rowHeight(i) for i in range(t.rowCount())) + 6
    t.setFixedHeight(min(h, 460))


def chart_view(chart: QChart) -> QChartView:
    chart.setBackgroundBrush(QColor(CARD)); chart.setBackgroundRoundness(10)
    chart.legend().setLabelColor(QColor(MUTED))
    chart.setTitleBrush(QColor(TEXT))
    v = QChartView(chart); v.setRenderHint(QPainter.Antialiasing); v.setMinimumHeight(280)
    v.setStyleSheet("background: transparent;")
    return v


def style_axis(ax):
    ax.setLabelsColor(QColor(MUTED)); ax.setGridLineColor(QColor(LINE)); ax.setLinePenColor(QColor(LINE))


# ------------------------------------------------------------------ settings dialog
class SettingsDialog(QDialog):
    def __init__(self, parent, values):
        super().__init__(parent)
        self.setWindowTitle("Interest rates")
        f = QFormLayout(self)
        self.col = QDoubleSpinBox(decimals=2, maximum=50, suffix=" %"); self.col.setValue(values["col_rate"])
        self.us = QDoubleSpinBox(decimals=2, maximum=50, suffix=" %"); self.us.setValue(values["us_rate"])
        f.addRow("Banco de la República policy rate", self.col)
        f.addRow("US Federal Reserve rate", self.us)
        note = QLabel("Used for the forward rate, carry and option prices. Update them when the central banks move.",
                      objectName="muted"); note.setWordWrap(True)
        f.addRow(note)
        b = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        b.accepted.connect(self.accept); b.rejected.connect(self.reject); f.addRow(b)

    def values(self):
        return {"col_rate": self.col.value(), "us_rate": self.us.value()}


# ------------------------------------------------------------------ main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("COPGuard · COP/USD Risk Intelligence")
        self.resize(1320, 860)
        self.trm_data = None
        self.analysis: Analysis | None = None
        self.settings = load_settings()
        self._threads = []

        root = QWidget(objectName="root"); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(18, 14, 18, 14); outer.setSpacing(12)

        # header
        h = QHBoxLayout()
        logo = QLabel()
        if (ASSETS / "icon.png").exists():
            from PySide6.QtGui import QPixmap
            logo.setPixmap(QPixmap(str(ASSETS / "icon.png")).scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        title = QVBoxLayout(); title.setSpacing(0)
        title.addWidget(QLabel("COPGuard", objectName="h1"))
        title.addWidget(QLabel("COP/USD risk intelligence · Blue Peak AI & Finance Solutions", objectName="muted"))
        self.pill = QLabel("Loading official TRM…", objectName="pill")
        refresh = QPushButton("↻ Refresh"); refresh.clicked.connect(self.load_data)
        sett = QPushButton("⚙ Rates"); sett.clicked.connect(self.open_settings)
        h.addWidget(logo); h.addLayout(title); h.addStretch(); h.addWidget(self.pill); h.addWidget(refresh); h.addWidget(sett)
        outer.addLayout(h)

        body = QHBoxLayout(); body.setSpacing(14); outer.addLayout(body, 1)
        body.addWidget(self._inputs_panel())
        body.addWidget(self._results_panel(), 1)

        self._menu()
        self.load_data()

    # ---------- inputs
    def _inputs_panel(self):
        p = QFrame(objectName="panel"); p.setFixedWidth(300)
        v = QVBoxLayout(p); v.setContentsMargins(16, 16, 16, 16); v.setSpacing(10)
        v.addWidget(QLabel("Your exposure", objectName="h2"))

        v.addWidget(QLabel("USD amount", objectName="muted"))
        self.amount = QDoubleSpinBox(decimals=0, maximum=1e10, singleStep=10000, prefix="US$ ")
        self.amount.setGroupSeparatorShown(True); self.amount.setValue(100000)
        v.addWidget(self.amount)

        v.addWidget(QLabel("Position", objectName="muted"))
        self.side = self._choice(v, [("I receive / hold USD", "receivable"), ("I owe / must pay USD", "payable")], 0, cols=1)

        v.addWidget(QLabel("Horizon", objectName="muted"))
        self.horizon = self._choice(v, [("1 d", 1), ("10 d", 10), ("30 d", 30), ("90 d", 90)], 2, cols=4)

        v.addWidget(QLabel("Confidence", objectName="muted"))
        self.conf = self._choice(v, [("90%", 0.90), ("95%", 0.95), ("99%", 0.99)], 1, cols=3)

        v.addWidget(QLabel("TRM (COP per USD), official by default", objectName="muted"))
        self.spot = QDoubleSpinBox(decimals=2, minimum=0, maximum=100000, singleStep=10)
        v.addWidget(self.spot)

        self.run_btn = QPushButton("Run risk analysis", objectName="primary"); self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self.run)
        v.addSpacing(6); v.addWidget(self.run_btn)
        self.status = QLabel("", objectName="muted"); self.status.setWordWrap(True); v.addWidget(self.status)
        v.addStretch()
        foot = QLabel("Data: official TRM history (Superintendencia Financiera via datos.gov.co). "
                      "Statistical estimates, not financial advice.", objectName="muted")
        foot.setWordWrap(True); v.addWidget(foot)
        return p

    def _choice(self, layout, options, default, cols):
        g = QButtonGroup(self); g.setExclusive(True)
        grid = QGridLayout(); grid.setSpacing(6)
        for i, (label, val) in enumerate(options):
            b = QPushButton(label); b.setCheckable(True); b.setProperty("val", val)
            if i == default: b.setChecked(True)
            g.addButton(b); grid.addWidget(b, i // cols, i % cols)
        layout.addLayout(grid)
        return g

    @staticmethod
    def _val(group):
        return group.checkedButton().property("val")

    # ---------- results
    def _results_panel(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)
        kpis = QHBoxLayout(); kpis.setSpacing(10)
        self.k_cop = card("COP equivalent"); self.k_var = card("Value at Risk"); self.k_cvar = card("Expected shortfall")
        self.k_reg = card("Market regime"); self.k_hedge = card("Suggested hedge")
        for c in (self.k_cop, self.k_var, self.k_cvar, self.k_reg, self.k_hedge):
            kpis.addWidget(c[0])
        v.addLayout(kpis)

        self.tabs = QTabWidget()
        self.tab_outlook = QWidget(); self.tab_mc = QWidget(); self.tab_vol = QWidget()
        self.tab_hedge = QWidget(); self.tab_stress = QWidget()
        for t, name in ((self.tab_outlook, "Outlook"), (self.tab_mc, "Monte Carlo"), (self.tab_vol, "Volatility"),
                        (self.tab_hedge, "Hedging && carry"), (self.tab_stress, "Stress tests")):
            QVBoxLayout(t).setContentsMargins(0, 10, 0, 0)
            self.tabs.addTab(t, name)
        v.addWidget(self.tabs, 1)

        bar = QHBoxLayout()
        self.copy_btn = QPushButton("Copy summary"); self.copy_btn.clicked.connect(self.copy_report)
        self.export_btn = QPushButton("Export report…"); self.export_btn.clicked.connect(self.export_report)
        for b in (self.copy_btn, self.export_btn): b.setEnabled(False)
        bar.addStretch(); bar.addWidget(self.copy_btn); bar.addWidget(self.export_btn)
        v.addLayout(bar)

        self._placeholder()
        return w

    def _placeholder(self):
        lay = self.tab_outlook.layout()
        msg = QLabel("Set your exposure on the left and run the analysis.\n\n"
                     "COPGuard fits a GARCH(1,1) model to 20 years of official TRM data, simulates 10,000 paths, "
                     "and turns the result into VaR, a hedge plan and historical stress tests.", objectName="muted")
        msg.setAlignment(Qt.AlignCenter); msg.setWordWrap(True)
        lay.addWidget(msg)

    def _menu(self):
        m = self.menuBar().addMenu("File")
        a = QAction("Export report…", self); a.setShortcut("Ctrl+E"); a.triggered.connect(self.export_report); m.addAction(a)
        a = QAction("Refresh data", self); a.setShortcut("Ctrl+R"); a.triggered.connect(self.load_data); m.addAction(a)
        h = self.menuBar().addMenu("Help")
        a = QAction("About COPGuard", self); a.triggered.connect(self.about); h.addAction(a)

    # ---------- actions
    def _start(self, worker):
        th = QThread(self); worker.moveToThread(th)
        th.started.connect(worker.run)
        for sig in (worker.data_ready, worker.result, worker.failed):
            sig.connect(th.quit)
        # Keep references until the thread has fully stopped; dropping them early crashes Qt.
        self._threads = [(t, w) for t, w in self._threads if not t.isFinished()]
        self._threads.append((th, worker)); th.start()

    def load_data(self):
        self.pill.setText("Loading official TRM…"); self.run_btn.setEnabled(False)
        w = Worker("load"); w.data_ready.connect(self.on_data); w.failed.connect(self.on_fail)
        self._start(w)

    def on_data(self, t):
        self.trm_data = t
        dot = "●" if "offline" not in t.source else "○"
        self.pill.setText(f"{dot} TRM {trm(t.last)}  ·  {t.last_date}  ·  {len(t.rates):,} days")
        self.pill.setToolTip(t.source)
        self.spot.setValue(t.last)
        self.run_btn.setEnabled(True)
        self.status.setText(f"Source: {t.source}")
        import os
        if os.environ.get("COPGUARD_AUTORUN") and self.analysis is None:
            self.run()

    def on_fail(self, msg):
        self.run_btn.setEnabled(self.trm_data is not None); self.run_btn.setText("Run risk analysis")
        self.pill.setText("TRM unavailable")
        QMessageBox.warning(self, "COPGuard", msg)

    def run(self):
        if not self.trm_data:
            return
        self.run_btn.setEnabled(False); self.run_btn.setText("Fitting GARCH and simulating 10,000 paths…")
        w = Worker("run", trm=self.trm_data, exposure_usd=self.amount.value(), days=self._val(self.horizon),
                   confidence=self._val(self.conf), side=self._val(self.side), spot=self.spot.value(),
                   col_rate=self.settings["col_rate"], us_rate=self.settings["us_rate"])
        w.result.connect(self.show_result); w.failed.connect(self.on_fail)
        self._start(w)

    def open_settings(self):
        d = SettingsDialog(self, self.settings)
        if d.exec():
            self.settings.update(d.values()); save_settings(self.settings)
            if self.analysis: self.run()

    def copy_report(self):
        if self.analysis:
            QGuiApplication.clipboard().setText(markdown(self.analysis))
            self.status.setText("Summary copied to the clipboard.")

    def export_report(self):
        if not self.analysis:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export report", str(Path.home() / "COPGuard report.md"),
                                              "Markdown (*.md);;Text (*.txt)")
        if path:
            Path(path).write_text(markdown(self.analysis), encoding="utf-8")
            self.status.setText(f"Report saved: {path}")

    def about(self):
        QMessageBox.about(self, "About COPGuard",
                          f"<b>COPGuard {__version__}</b><br>COP/USD risk intelligence by Blue Peak AI &amp; Finance "
                          "Solutions.<br><br>GARCH(1,1) · filtered historical simulation · VaR/CVaR · "
                          "covered interest parity · Garman-Kohlhagen options · historical stress tests.<br><br>"
                          "Statistical estimates, not financial advice.")

    # ---------- rendering
    @staticmethod
    def _clear(w):
        lay = w.layout()
        while lay.count():
            it = lay.takeAt(0)
            if it.widget(): it.widget().deleteLater()
            elif it.layout():
                MainWindow._clear_layout(it.layout())

    @staticmethod
    def _clear_layout(lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget(): it.widget().deleteLater()

    def show_result(self, a: Analysis):
        self.analysis = a
        self.run_btn.setEnabled(True); self.run_btn.setText("Run risk analysis")
        for b in (self.copy_btn, self.export_btn): b.setEnabled(True)
        r = a.risk
        self.k_cop[1].setText(cop(a.exposure_usd * a.spot)); self.k_cop[2].setText(f"at TRM {trm(a.spot)}")
        self.k_var[1].setText(cop(r.var)); self.k_var[2].setText(f"{a.confidence:.0%}, {a.days} days · US$ {r.var / a.spot:,.0f}")
        self.k_cvar[1].setText(cop(r.cvar)); self.k_cvar[2].setText("average loss beyond VaR")
        col = {"CALM": GREEN, "NORMAL": BLUE, "ELEVATED": AMBER, "CRISIS": RED}[a.regime.name]
        self.k_reg[1].setText(a.regime.name); self.k_reg[1].setStyleSheet(f"color: {col};")
        self.k_reg[2].setText(f"90-day vol {a.regime.vol_90d:.1%} vs long-run {a.regime.vol_long:.1%}")
        self.k_hedge[1].setText(f"{a.hedge.ratio:.0%}")
        self.k_hedge[2].setText(f"VaR {cop(a.hedge.var_unhedged)} → {cop(a.hedge.var_hedged)}")
        self._render_outlook(a); self._render_mc(a); self._render_vol(a); self._render_hedge(a); self._render_stress(a)
        self.status.setText(f"Done · {a.n_obs:,} days of data · GARCH α={a.garch.alpha:.3f} β={a.garch.beta:.3f}")
        import os
        snap = os.environ.get("COPGUARD_SNAPSHOT")      # used to make the README screenshots
        if snap:
            from PySide6.QtCore import QTimer
            def shoot():
                for i in range(self.tabs.count()):
                    self.tabs.setCurrentIndex(i); QApplication.processEvents()
                    self.grab().save(f"{snap}-{i}.png")
                QApplication.quit()
            QTimer.singleShot(800, shoot)

    def _render_outlook(self, a):
        w = self.tab_outlook; self._clear(w); lay = w.layout()
        o = a.outlook
        arrow = {"UP": "▲", "DOWN": "▼", "NEUTRAL": "■"}[o.direction]
        meaning = {"UP": "the peso weakens (more COP per dollar)", "DOWN": "the peso strengthens (fewer COP per dollar)",
                   "NEUTRAL": "no clear direction"}[o.direction]
        top = QFrame(objectName="card"); tv = QVBoxLayout(top)
        head = QLabel(f"{arrow}  TRM outlook: {o.direction}  ·  confidence {o.confidence:.0%}", objectName="h1")
        head.setStyleSheet(f"color: {GREEN if o.direction == 'DOWN' else RED if o.direction == 'UP' else MUTED};")
        tv.addWidget(head)
        sub = QLabel(f"Over {a.days} days the signals lean towards: {meaning}. Expected median move {o.expected_move:+.2%}. "
                     "Confidence is capped at 70%: nobody predicts FX direction reliably.", objectName="muted")
        sub.setWordWrap(True); tv.addWidget(sub)
        lay.addWidget(top)
        t = table(["Scenario", "TRM", "Probability", "Change"])
        fill(t, [[n, trm(x), f"{p:.0%}", f"{c:+.2%}"] for n, x, p, c in o.scenarios])
        lay.addWidget(t)
        s = table(["Signal", "Score", "Why"]); s.setWordWrap(True)
        s.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        s.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        s.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        colors = {(i, 1): (RED if x.score > 0 else GREEN if x.score < 0 else MUTED) for i, x in enumerate(o.signals)}
        fill(s, [[x.name, f"{x.score:+d}", x.detail] for x in o.signals], colors)
        lay.addWidget(s); lay.addStretch()

    def _render_mc(self, a):
        w = self.tab_mc; self._clear(w); lay = w.layout()
        term = a.terminal
        counts, edges = np.histogram(term, bins=60)
        ch = QChart(); ch.setTitle(f"Where the TRM could be in {a.days} days · 10,000 simulated paths")
        top, base = QLineSeries(), QLineSeries()
        for i, c in enumerate(counts):          # stepped outline of the histogram
            for x in (edges[i], edges[i + 1]):
                top.append(float(x), float(c)); base.append(float(x), 0.0)
        self._mc_lines = (top, base)
        area = QAreaSeries(top, base); area.setColor(QColor(59, 130, 246, 150)); area.setBorderColor(QColor(BLUE))
        ch.addSeries(area)
        markers = []
        for x, name, colr in ((a.spot, "Today", TEXT), (a.risk.percentiles[5], "P5", RED), (a.risk.percentiles[95], "P95", GREEN)):
            m = QLineSeries(); m.setName(f"{name} {trm(x)}"); m.setPen(QPen(QColor(colr), 2, Qt.DashLine))
            m.append(float(x), 0.0); m.append(float(x), float(counts.max()) * 1.05); ch.addSeries(m); markers.append(m)
        ax = QValueAxis(); ax.setLabelFormat("%.0f"); ax.setRange(float(edges[0]), float(edges[-1])); style_axis(ax)
        ay = QValueAxis(); ay.setLabelFormat("%d"); ay.setRange(0, float(counts.max()) * 1.1); style_axis(ay)
        ay.setTitleText("paths"); ay.setTitleBrush(QColor(MUTED))
        ch.addAxis(ax, Qt.AlignBottom); ch.addAxis(ay, Qt.AlignLeft)
        for sr in [area] + markers:
            sr.attachAxis(ax); sr.attachAxis(ay)
        ch.legend().markers(area)[0].setVisible(False)
        lay.addWidget(chart_view(ch))
        sign = 1 if a.side == "receivable" else -1
        rows = [[f"P{p}", trm(v), f"{v / a.spot - 1:+.2%}", cop(sign * a.exposure_usd * (v - a.spot))]
                for p, v in a.risk.percentiles.items()]
        colors = {(i, 3): (GREEN if sign * (v - a.spot) >= 0 else RED) for i, (_, v) in enumerate(a.risk.percentiles.items())}
        t = table(["Percentile", "TRM", "Change", "Your P&L (COP)"]); fill(t, rows, colors); lay.addWidget(t)
        lay.addWidget(QLabel(f"P(TRM lower) {a.risk.p_down:.1%} · P(TRM higher) {a.risk.p_up:.1%} · "
                             f"parametric VaR {cop(a.risk.parametric_var)} · horizon volatility {a.risk.horizon_vol:.2%}",
                             objectName="muted"))
        lay.addStretch()

    def _render_vol(self, a):
        w = self.tab_vol; self._clear(w); lay = w.layout()
        rates = self.trm_data.rates.iloc[-260:]
        ch = QChart(); ch.setTitle(f"Official TRM, last 12 months, with the GARCH 95% band for the next {a.days} days")
        line = QLineSeries(); line.setName("TRM"); line.setPen(QPen(QColor(BLUE), 2))
        for d, v in rates.items():
            line.append(QDateTime(d.to_pydatetime()).toMSecsSinceEpoch(), float(v))
        last = rates.index[-1]
        up, lo = QLineSeries(), QLineSeries()
        import pandas as pd
        fut = pd.bdate_range(last, periods=a.days + 1)[1:]
        up.append(QDateTime(last.to_pydatetime()).toMSecsSinceEpoch(), a.spot)
        lo.append(QDateTime(last.to_pydatetime()).toMSecsSinceEpoch(), a.spot)
        cum = np.cumsum(a.garch.forecast_var(a.days))
        for d, c in zip(fut, cum):
            ms = QDateTime(d.to_pydatetime()).toMSecsSinceEpoch()
            up.append(ms, a.spot * np.exp(1.96 * np.sqrt(c))); lo.append(ms, a.spot * np.exp(-1.96 * np.sqrt(c)))
        self._band_lines = (up, lo)   # QAreaSeries doesn't own its boundary lines; keep them alive
        band = QAreaSeries(up, lo); band.setName("95% band"); band.setColor(QColor(59, 130, 246, 70)); band.setBorderColor(QColor(BLUE))
        ch.addSeries(line); ch.addSeries(band)
        ax = QDateTimeAxis(); ax.setFormat("MMM yy"); style_axis(ax); ch.addAxis(ax, Qt.AlignBottom)
        ay = QValueAxis(); ay.setLabelFormat("%.0f"); style_axis(ay); ch.addAxis(ay, Qt.AlignLeft)
        for s in (line, band): s.attachAxis(ax); s.attachAxis(ay)
        end = fut[-1] if len(fut) else last
        ax.setRange(QDateTime(rates.index[0].to_pydatetime()), QDateTime(end.to_pydatetime()))
        hi = max(float(rates.max()), a.spot * float(np.exp(1.96 * np.sqrt(cum[-1]))))
        lo_ = min(float(rates.min()), a.spot * float(np.exp(-1.96 * np.sqrt(cum[-1]))))
        ay.setRange(lo_ * 0.98, hi * 1.02)
        lay.addWidget(chart_view(ch))
        g = a.garch
        lay.addWidget(QLabel(f"GARCH(1,1): ω={g.omega:.2e}  α={g.alpha:.3f}  β={g.beta:.3f}  ·  persistence {g.persistence:.3f}  ·  "
                             f"half-life {g.half_life_days:.0f} days  ·  current vol {g.current_vol_annual:.1%}  ·  "
                             f"long-run {g.long_run_vol_annual:.1%}", objectName="muted"))
        t = table(["Day", "Daily σ", "95% low", "95% high", "99% low", "99% high"])
        fill(t, [[str(d), f"{s:.3%}", trm(l95), trm(h95), trm(l99), trm(h99)] for d, s, l95, h95, l99, h99 in a.vol_bands])
        lay.addWidget(t); lay.addStretch()

    def _render_hedge(self, a):
        w = self.tab_hedge; self._clear(w); lay = w.layout()
        hb = QFrame(objectName="card"); hv = QVBoxLayout(hb)
        hv.addWidget(QLabel(f"Hedge {a.hedge.ratio:.0%} of the exposure", objectName="h1"))
        e = QLabel(a.hedge.explanation, objectName="muted"); e.setWordWrap(True); hv.addWidget(e)
        lay.addWidget(hb)
        c = a.carry
        pick = a.hedge.forward_pickup_cop
        t = table(["Item", "Value"])
        fill(t, [
            ["Forward rate (covered interest parity)", trm(c.forward)],
            ["Forward points", f"{c.forward_points:+.2f} COP ({c.forward_premium:+.2f}% a year)"],
            [f"Forward points {'earned' if pick >= 0 else 'paid'} on hedged amount", cop(abs(pick))],
            [f"At-the-money {'USD put' if a.side == 'receivable' else 'USD call'} premium (hedged amount)",
             cop(a.hedge.put_premium_cop)],
            ["VaR without hedge", cop(a.hedge.var_unhedged)],
            ["VaR with forward hedge", cop(a.hedge.var_hedged)],
            ["Colombia policy rate", f"{c.col_rate:.2f}%"],
            ["US Fed rate", f"{c.us_rate:.2f}%"],
            ["Rate differential", f"{c.differential:+.2f} pts"],
            [f"Carry on the COP equivalent over {a.days} days", cop(c.carry_cop)],
        ])
        lay.addWidget(t)
        n = QLabel("Rates are editable under ⚙ Rates. The forward price follows from the rate gap: when Colombian "
                   "rates are higher, the forward TRM is above spot.", objectName="muted"); n.setWordWrap(True)
        lay.addWidget(n); lay.addStretch()

    def _render_stress(self, a):
        w = self.tab_stress; self._clear(w); lay = w.layout()
        lay.addWidget(QLabel("The worst real TRM move against your position inside each crisis, applied to today's exposure.",
                             objectName="muted"))
        t = table(["Scenario", "From", "To", "Move against you", "Loss (COP)", "Implied TRM"])
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        fill(t, [[s.name, s.start, s.end, f"{s.move:+.1%}", cop(s.loss_cop), trm(s.implied_trm)] for s in a.stress],
             {(i, 4): RED for i in range(len(a.stress))})
        lay.addWidget(t); lay.addStretch()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("COPGuard")
    app.setStyleSheet(QSS)
    icon = ASSETS / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    w = MainWindow(); w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
