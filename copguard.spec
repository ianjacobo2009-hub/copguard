# PyInstaller spec: builds COPGuard.app (macOS) or COPGuard.exe (Windows)
import sys
a = Analysis(["run_copguard.py"], pathex=["."], datas=[("assets", "assets")],
             hiddenimports=["PySide6.QtCharts", "scipy.optimize", "scipy.stats"],
             excludes=["tkinter", "matplotlib", "IPython", "pytest"])
pyz = PYZ(a.pure)
icon = "assets/icon.icns" if sys.platform == "darwin" else None
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="COPGuard", console=False, icon=icon)
coll = COLLECT(exe, a.binaries, a.datas, name="COPGuard")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="COPGuard.app", icon=icon, bundle_identifier="com.bluepeak.copguard",
                 info_plist={"CFBundleShortVersionString": "1.0.0", "NSHighResolutionCapable": True})
