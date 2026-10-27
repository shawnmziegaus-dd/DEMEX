#!/usr/bin/env python3
"""DEMEX desktop launcher.

Runs the bridge server (demex_bridge.py) in a background thread and shows
DEMEX.html in a native window (pywebview on Microsoft Edge WebView2). No
terminal, no browser tab, no run_demex.bat.

Two extra jobs when packaged with PyInstaller (see DEMEX.spec):

  * DEMEX.exe --run-script <script.py> [args...]
      The bridge shells out to the CLI tools (email_ingestor, pricing_engine,
      ...) with "python script.py". A frozen build has no python.exe, so the
      exe acts as the interpreter for scripts that live next to it.

  * DEMEX.exe --browser
      Skip the native window and open the app in the default browser instead
      (same as the old run_demex.bat). Also the automatic fallback if the
      WebView2 runtime is missing.

Data (demex_records.db, .env, projects/, demex_rate_card.json) stays in the
app folder, exactly as before. Browser-side data (localStorage) lives in the
window's own profile under %LOCALAPPDATA%\\DEMEX\\webview.
"""
from __future__ import annotations

import os
import runpy
import socket
import sys
import threading
import webbrowser
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = (Path(sys.executable).resolve().parent if FROZEN
        else Path(__file__).resolve().parent)
APP_TITLE = "DEMEX — Disassemble Dynamics Estimating"


def _run_script(argv: list[str]) -> int:
    """DEMEX.exe --run-script script.py args... -> run it as __main__."""
    if not argv:
        print("usage: DEMEX.exe --run-script <script.py> [args...]", file=sys.stderr)
        return 2
    script = Path(argv[0])
    if not script.is_absolute():
        script = ROOT / script
    # Scripts import each other by bare name (takeoff_scalar -> email_ingestor).
    for d in (str(script.parent), str(ROOT)):
        if d not in sys.path:
            sys.path.insert(0, d)
    sys.argv = [str(script), *argv[1:]]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as e:
        code = e.code
        return 0 if code is None else (code if isinstance(code, int) else 1)
    return 0


def _free_port(preferred: int) -> int:
    """Use the preferred port if free, otherwise any free loopback port."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


def _start_bridge() -> tuple[object, str]:
    port = _free_port(int(os.environ.get("DEMEX_BRIDGE_PORT", "8765")))
    os.environ["DEMEX_BRIDGE_PORT"] = str(port)
    sys.path.insert(0, str(ROOT))
    import demex_bridge  # noqa: E402  (reads DEMEX_BRIDGE_PORT at import)
    httpd = demex_bridge.make_server(port)
    t = threading.Thread(target=httpd.serve_forever, name="demex-bridge",
                         daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{port}/demex/"


def _open_window(url: str) -> bool:
    """Show the app in a native window. Returns False if that isn't possible."""
    try:
        import webview  # pywebview
    except Exception as e:  # pragma: no cover - depends on the install
        print(f"pywebview unavailable ({e}); falling back to the browser.")
        return False
    storage = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "DEMEX" / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True       # Export JSON / PDFs
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    except Exception:
        pass
    try:
        webview.create_window(APP_TITLE, url, width=1480, height=920,
                              min_size=(1024, 680), text_select=True)
        # private_mode=False keeps localStorage (the app's project database)
        # between launches; storage_path pins it to one known folder.
        webview.start(private_mode=False, storage_path=str(storage))
        return True
    except Exception as e:  # WebView2 runtime missing, etc.
        print(f"Native window failed ({e}); falling back to the browser.")
        return False


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--run-script":
        return _run_script(args[1:])
    os.chdir(ROOT)
    httpd, url = _start_bridge()
    print(f"DEMEX running -> {url}")
    try:
        if "--browser" in args or not _open_window(url):
            webbrowser.open(url)
            print("Close this window to stop DEMEX.")
            threading.Event().wait()           # keep the server alive
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
