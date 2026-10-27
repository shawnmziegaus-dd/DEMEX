# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the DEMEX desktop app. Built by build_desktop.bat.
#
# Produces dist\DEMEX\DEMEX.exe (+ _internal\ with Python and libraries).
# The app files themselves (DEMEX.html, the *.py tools, tools\, standards\,
# integrations\, demex_seed.json, .env.example) are copied next to the exe by
# build_desktop.bat so the folder works exactly like the source folder does.
from PyInstaller.utils.hooks import collect_all, collect_submodules

hidden = []
datas, binaries = [], []
# Native window (WebView2 via pythonnet) and every library the CLI tools import
# lazily inside try/except -- PyInstaller can't see those, so list them.
for pkg in ("webview", "pythonnet", "clr_loader", "pypdfium2", "pypdfium2_raw",
            "pdfplumber", "pdfminer", "pypdf", "reportlab", "PIL", "cv2", "numpy"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception:
        pass
hidden += collect_submodules("email") + ["imaplib", "ssl", "sqlite3", "runpy"]

a = Analysis(
    ["../demex_desktop.py"],
    pathex=[".."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "IPython", "jupyter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="DEMEX",
    console=False,            # no black terminal window
    icon="icon.ico" if __import__("os").path.exists("icon.ico") else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="DEMEX")
