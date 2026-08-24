#!/usr/bin/env python3
"""DEMEX local bridge server.

A tiny localhost HTTP server that lets the DEMEX.html UI run the backend Python
tools without a terminal. It does two jobs:

  1. Serves the project folder as static files, so the app is opened at
         http://localhost:8765/DEMEX.html
     and every fetch('/api/...') is same-origin (no CORS headaches).

  2. Exposes /api/* endpoints that shell out to the DEMEX tools and stream the
     results back as JSON (or a PDF). Stdout+stderr from each tool is captured
     and returned so first-run errors (e.g. missing Gmail credentials) surface
     directly in the app's toast instead of vanishing.

Run it via run_demex.bat (double-click). Standard library only.

Endpoints
  GET  /api/health                      -> {ok:true}
  GET  /api/token                       -> {token} (for X-DEMEX-Token on POSTs)
  GET  /api/rates[?refresh=1]           -> {ok, payload:{price_bands,...}}
  GET  /api/plans?project=<name>        -> {ok, payload:{plans:[{path,name,dir}]}}
  POST /api/ingest   {since?}           -> {ok, payload:{clients,projects}}
  POST /api/takeoff  {pdf,project,...}  -> {ok, payload:{clients,projects}}
  POST /api/pricing  {project,history,overheadPct,profitPct}
                                        -> {ok, payload:{estimate,estQty,...}}
  POST /api/proposal {project,company,client}  -> application/pdf (download)
  POST /api/ai       {prompt,max_tokens?,system?}  -> {text}

Security model (all localhost-only, defense in depth):
  * Every request must carry a Host header of localhost / 127.0.0.1 / [::1]
    (port ignored) -> otherwise 403. Defeats DNS-rebinding attacks.
  * Every POST /api/* must carry X-DEMEX-Token matching a per-run random
    token. The UI fetches it from GET /api/token; because the bridge never
    emits CORS headers, a cross-origin page cannot read that response.
  * Static GET serves only non-hidden *.html files; everything else is 404.
  * /api/takeoff confines the client-supplied pdf path to the project dir
    (or DEMEX_PLANS_DIR, if set) after realpath resolution.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("DEMEX_BRIDGE_PORT", "8765"))
PY = sys.executable                          # avoids the MS Store python stub
DB = str(ROOT / "demex_records.db")
TOOL_TIMEOUT = 1800                          # seconds; email scans can be slow

# Per-run auth token for state-changing requests. Held only in memory; the UI
# obtains it via GET /api/token (same-origin only -- no CORS headers exist).
TOKEN = secrets.token_urlsafe(32)

# Hosts a browser may legitimately use to reach this loopback-bound server.
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}

AI_API_URL = "https://api.anthropic.com/v1/messages"
AI_DEFAULT_MODEL = "claude-sonnet-4-6"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def load_dotenv() -> None:
    """Load .env (then demex_config.json) into os.environ if present.

    email_ingestor.load_credentials() already falls back to the
    DEMEX_GMAIL_USER / DEMEX_GMAIL_APP_PASSWORD env vars, so this is all the
    wiring the ingest button needs.
    """
    env = ROOT / ".env"
    if env.is_file():
        for raw in env.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    cfg = ROOT / "demex_config.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            if data.get("user"):
                os.environ.setdefault("DEMEX_GMAIL_USER", data["user"])
            if data.get("app_password"):
                os.environ.setdefault("DEMEX_GMAIL_APP_PASSWORD",
                                      data["app_password"])
        except Exception:
            pass


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s or "").strip().lower()
    return re.sub(r"[\s_-]+", "-", s)


def _tail(text: str, n: int = 1600) -> str:
    text = text or ""
    return text if len(text) <= n else "…" + text[-n:]


def run_tool(args: list[str]) -> tuple[int, str]:
    """Run [python, *args] in the project dir; return (rc, combined log)."""
    proc = subprocess.run([PY, *args], cwd=str(ROOT), text=True,
                          capture_output=True, env=dict(os.environ),
                          timeout=TOOL_TIMEOUT)
    log = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    return proc.returncode, log


def _tmp(suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, dir=str(ROOT))
    os.close(fd)
    return path


def _read_json_file(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def resolve_pdf_path(pdf: str) -> str | None:
    """Resolve a client-supplied pdf path and confine it to allowed dirs.

    Relative paths (e.g. the ROOT-relative values /api/plans now returns) are
    resolved against ROOT. The realpath'd result must live inside ROOT or
    inside DEMEX_PLANS_DIR (optional env var, also realpath'd); anything else
    returns None so the caller can 400.
    """
    if not isinstance(pdf, str) or not pdf.strip():
        return None
    candidate = pdf if os.path.isabs(pdf) else str(ROOT / pdf)
    rp = os.path.realpath(candidate)
    allowed = [os.path.realpath(str(ROOT))]
    plans_dir = (os.environ.get("DEMEX_PLANS_DIR") or "").strip()
    if plans_dir:
        allowed.append(os.path.realpath(plans_dir))
    for base in allowed:
        if rp == base or rp.startswith(base.rstrip(os.sep) + os.sep):
            return rp
    return None


def list_plans(project: str = "") -> list[dict]:
    """All PDFs under the project dir + projects/, preferring slug matches.

    Paths are returned ROOT-relative (never absolute filesystem paths);
    /api/takeoff resolves them back against ROOT.
    """
    seen, items = set(), []
    for base in (ROOT, ROOT / "projects"):
        if not base.is_dir():
            continue
        for p in base.rglob("*.pdf"):
            rp = str(p.resolve())
            if rp in seen:
                continue
            seen.add(rp)
            items.append({"path": os.path.relpath(rp, str(ROOT)),
                          "name": p.name, "dir": p.parent.name})
    if project:
        slug = _slug(project)
        token = slug.split("-")[0] if slug else ""
        pref = [it for it in items
                if token and token in _slug(it["name"] + " " + it["dir"])]
        if pref:
            return pref
    return items


# --------------------------------------------------------------------------- #
# HTTP handler                                                                 #
# --------------------------------------------------------------------------- #
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def log_message(self, fmt, *args):       # quieter, prefixed logging
        sys.stderr.write("[bridge] %s\n" % (fmt % args))

    # ---- low-level responders ----
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _pdf(self, data: bytes, filename: str):
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition",
                         'attachment; filename="%s"' % filename)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ---- security gates ----
    def _host_allowed(self) -> bool:
        """True iff the Host header (port ignored) is a loopback name.

        Blocks DNS rebinding: a hostile page at http://attacker.example that
        rebinds its name to 127.0.0.1 still sends Host: attacker.example.
        """
        host = (self.headers.get("Host") or "").strip()
        if host.startswith("["):                 # [::1] or [::1]:8765
            host = host[1:].split("]", 1)[0]
        else:
            host = host.rsplit(":", 1)[0] if ":" in host else host
        return host.lower() in ALLOWED_HOSTS

    def _token_ok(self) -> bool:
        supplied = self.headers.get("X-DEMEX-Token") or ""
        return hmac.compare_digest(supplied, TOKEN)

    # ---- routing ----
    def do_GET(self):
        if not self._host_allowed():
            return self._json({"ok": False, "error": "forbidden host"}, 403)
        u = urlparse(self.path)
        if u.path.startswith("/api/"):
            try:
                return self._api_get(u)
            except subprocess.TimeoutExpired:
                return self._json({"ok": False, "error": "Tool timed out."},
                                  504)
            except Exception as e:            # never leak a stack to the UI
                return self._json({"ok": False, "error": str(e)}, 500)
        return self._static(u)

    def do_HEAD(self):
        if not self._host_allowed():
            return self._json({"ok": False, "error": "forbidden host"}, 403)
        u = urlparse(self.path)
        if u.path.startswith("/api/"):
            return self.send_error(404, "Not found")
        if not self._static_allowed(u):
            return self.send_error(404, "Not found")
        return super().do_HEAD()

    def do_POST(self):
        if not self._host_allowed():
            return self._json({"ok": False, "error": "forbidden host"}, 403)
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self.send_error(404, "Not found")
        if not self._token_ok():
            return self._json({"error": "missing or bad token"}, 403)
        try:
            return self._api_post(u)
        except subprocess.TimeoutExpired:
            self._json({"ok": False, "error": "Tool timed out."}, 504)
        except Exception as e:                # never leak a stack to the UI
            self._json({"ok": False, "error": str(e)}, 500)

    # ---- static files: only visible .html files, nothing else ----
    @staticmethod
    def _static_allowed(u) -> bool:
        # Check the percent-decoded path -- the base handler decodes before
        # hitting the filesystem, so %2e games must not bypass these rules.
        try:
            decoded = unquote(u.path, errors="surrogatepass")
        except Exception:
            return False
        parts = [p for p in re.split(r"[/\\]", decoded) if p]
        if not parts or ".." in parts:
            return False
        if any(p.startswith(".") for p in parts):    # never serve dotfiles
            return False
        return decoded.endswith(".html")             # path only, no query

    def _static(self, u):
        if not self._static_allowed(u):
            return self.send_error(404, "Not found")
        return super().do_GET()

    def _api_get(self, u):
        path = u.path[len("/api/"):]
        q = parse_qs(u.query)
        if path == "health":
            return self._json({"ok": True, "service": "demex-bridge",
                               "port": PORT})
        if path == "token":
            # Same-origin pages only: the bridge never sends CORS headers, so
            # a cross-origin page cannot read this response.
            return self._json({"token": TOKEN})
        if path == "rates":
            return self._rates(q)
        if path == "plans":
            proj = (q.get("project") or [""])[0]
            return self._json({"ok": True,
                               "payload": {"plans": list_plans(proj)}})
        return self._json({"ok": False, "error": "Unknown endpoint: " + path}, 404)

    def _rates(self, q):
        """Serve the Master Reference Rate data for the cockpit accuracy panel.

        Returns demex_rate_reference.json as-is for the default (all-scopes) view.
        Recomputes live from demex_full_history_import.json via learning_loop.py
        when ?refresh=1, the file is missing, OR a ?trade=<scope> filter is given
        (the static reference holds only the global, all-trades bands).
        """
        ref = ROOT / "demex_rate_reference.json"
        refresh = (q.get("refresh") or ["0"])[0] in ("1", "true", "yes")
        trade = (q.get("trade") or [""])[0].strip()
        if ref.is_file() and not refresh and not trade:
            data = _read_json_file(str(ref))
            if data is not None:
                return self._json({"ok": True, "payload": data})
        hist = ROOT / "demex_full_history_import.json"
        if not hist.is_file():
            return self._json({"ok": False,
                               "error": "No rate reference or history file found."})
        out = _tmp(".json")
        args = ["learning_loop.py", "--input", str(hist), "--export-json", out]
        if trade:
            args += ["--trade", trade]
        rc, log = run_tool(args)
        learn = _read_json_file(out)
        _unlink(out)
        if learn is None:
            return self._json({"ok": False,
                               "error": _tail(log) or "Could not compute rates.",
                               "log": _tail(log)})
        payload = _read_json_file(str(ref)) or {"schema": "demex_rate_reference/live"}
        payload["price_bands"] = learn.get("price_bands")
        # Keep the size-tier model in sync on a live refresh; the cockpit
        # accuracy panel scales its risk bands off tiered_bands.
        if learn.get("tiered_bands"):
            payload["tiered_bands"] = learn.get("tiered_bands")
        # available_trades is the full roster (not trade-filtered) -> the scope
        # dropdown lists every scope with decided history regardless of selection.
        if learn.get("available_trades") is not None:
            payload["available_trades"] = learn.get("available_trades")
        payload["trade"] = trade or None
        payload["learning_state"] = {k: learn.get(k) for k in
                                     ("losing_streak", "markup_delta_pct",
                                      "rationale", "decided_bids")}
        return self._json({"ok": True, "payload": payload})

    def _api_post(self, u):
        path = u.path[len("/api/"):]
        body = self._body()
        if path == "ingest":
            return self._ingest(body)
        if path == "takeoff":
            return self._takeoff(body)
        if path == "pricing":
            return self._pricing(body)
        if path == "proposal":
            return self._proposal(body)
        if path == "ai":
            return self._ai(body)
        if path == "markup-extract":
            return self._markup_extract(body)
        return self._json({"ok": False, "error": "Unknown endpoint: " + path}, 404)

    # ---- endpoints ----
    def _ingest(self, body):
        out = _tmp(".json")
        args = ["email_ingestor.py", "--db", DB, "--export-json", out]
        if body.get("since"):
            args += ["--since", str(body["since"])]
        if body.get("limit"):
            args += ["--limit", str(int(body["limit"]))]
        rc, log = run_tool(args)
        payload = _read_json_file(out)
        _unlink(out)
        if payload is None:
            return self._json({"ok": False,
                               "error": _tail(log) or "Ingest failed.",
                               "log": _tail(log)})
        return self._json({"ok": True, "payload": payload, "log": _tail(log)})

    def _takeoff(self, body):
        pdf = body.get("pdf")
        project = body.get("project")
        if not pdf or not project:
            return self._json({"ok": False,
                               "error": "pdf and project are required."}, 400)
        pdf = resolve_pdf_path(str(pdf))
        if pdf is None:
            return self._json({"ok": False,
                               "error": "pdf path outside allowed directories"},
                              400)
        out = _tmp(".json")
        args = ["takeoff_scalar.py", "--pdf", pdf, "--project", project,
                "--mode", "auto-area", "--no-window", "--db", DB,
                "--export-json", out]
        if body.get("scope_type"):
            args += ["--scope-type", str(body["scope_type"])]
        if body.get("svc"):
            args += ["--svc", str(body["svc"])]
        if body.get("page"):
            args += ["--page", str(int(body["page"]))]
        rc, log = run_tool(args)
        payload = _read_json_file(out)
        _unlink(out)
        if rc != 0 or payload is None:
            return self._json({"ok": False,
                               "error": _tail(log) or "Takeoff failed.",
                               "log": _tail(log)})
        return self._json({"ok": True, "payload": payload, "log": _tail(log)})

    def _markup_extract(self, body):
        """Exact-quantities takeoff from a Bluebeam-marked-up PDF.

        Runs tools/takeoff_markup_extract.py against the DEMEX markup legend.
        This is the accurate takeoff path: it reads the measurement annotations
        the estimator drew in Bluebeam, so quantities are exact, colour-mapped
        to scope, and flagged (needs_review / UNMAPPED) instead of guessed.
        """
        pdf = body.get("pdf")
        if not pdf:
            return self._json({"ok": False, "error": "pdf is required."}, 400)
        pdf = resolve_pdf_path(str(pdf))
        if pdf is None:
            return self._json({"ok": False,
                               "error": "pdf path outside allowed directories"},
                              400)
        out = _tmp(".json")
        args = [os.path.join("tools", "takeoff_markup_extract.py"),
                "--pdf", pdf, "--out", out, "--quiet"]
        if body.get("profile"):
            args += ["--profile", str(body["profile"])]
        if body.get("tolerance") is not None:
            args += ["--tolerance", str(float(body["tolerance"]))]
        if body.get("project"):
            args += ["--project", str(body["project"]), "--db", DB]
        rc, log = run_tool(args)
        payload = _read_json_file(out)
        _unlink(out)
        if rc != 0 or payload is None:
            return self._json({"ok": False,
                               "error": _tail(log) or "Markup extraction failed.",
                               "log": _tail(log)})
        return self._json({"ok": True, "payload": payload, "log": _tail(log)})

    def _pricing(self, body):
        project = body.get("project") or {}
        history = body.get("history") or []
        if not project:
            return self._json({"ok": False, "error": "project is required."}, 400)
        profit = float(body.get("profitPct") or 0)
        overhead = float(body.get("overheadPct") or 0)

        # 1) learning loop -> markup delta from win/loss streak
        hist_in = _tmp(".json")
        ll_out = _tmp(".json")
        Path(hist_in).write_text(json.dumps({"projects": history}),
                                 encoding="utf-8")
        rc, ll_log = run_tool(["learning_loop.py", "--input", hist_in,
                               "--export-json", ll_out,
                               "--base-markup", str(profit)])
        learn = _read_json_file(ll_out) or {}
        markup_delta = float(learn.get("markup_delta_pct") or 0)
        _unlink(hist_in)
        _unlink(ll_out)

        # 2) pricing engine -> bulk curve + estimate
        proj_in = _tmp(".json")
        pr_out = _tmp(".json")
        Path(proj_in).write_text(json.dumps(project), encoding="utf-8")
        rc, pr_log = run_tool(["pricing_engine.py", "--input", proj_in,
                               "--export-json", pr_out,
                               "--overhead", str(overhead),
                               "--profit", str(profit),
                               "--markup-delta", str(markup_delta)])
        payload = _read_json_file(pr_out)
        _unlink(proj_in)
        _unlink(pr_out)
        if rc != 0 or payload is None:
            return self._json({"ok": False,
                               "error": _tail(pr_log) or "Pricing failed.",
                               "log": _tail(pr_log)})
        payload["learning"] = learn
        if learn.get("rationale"):
            payload["summary"] = (payload.get("summary", "") +
                                  " | " + learn["rationale"])
        return self._json({"ok": True, "payload": payload,
                           "log": _tail(ll_log + "\n" + pr_log)})

    def _proposal(self, body):
        project = body.get("project") or {}
        if not project.get("name"):
            return self._json({"ok": False,
                               "error": "project.name is required."}, 400)
        inp = _tmp(".json")
        pdf = _tmp(".pdf")
        Path(inp).write_text(json.dumps({
            "project": project,
            "company": body.get("company") or {},
            "client": body.get("client") or {},
        }), encoding="utf-8")
        rc, log = run_tool(["proposal_pdf.py", "--input", inp, "--out", pdf])
        _unlink(inp)
        data = b""
        if rc == 0 and Path(pdf).is_file():
            data = Path(pdf).read_bytes()
        _unlink(pdf)
        if rc != 0 or not data:
            return self._json({"ok": False,
                               "error": _tail(log) or "Proposal build failed.",
                               "log": _tail(log)})
        fname = (_slug(project["name"].split(",")[0]) or "proposal") + "_proposal.pdf"
        return self._pdf(data, fname)

    def _ai(self, body):
        """Proxy a prompt to the Anthropic Messages API; return {"text": ...}.

        The key comes from ANTHROPIC_API_KEY (loaded from .env by
        load_dotenv() at startup) and is never logged or echoed.
        """
        key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        if not key:
            return self._json({"error": "ANTHROPIC_API_KEY not set in .env"},
                              503)
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return self._json({"error": "prompt is required."}, 400)
        try:
            max_tokens = int(body.get("max_tokens") or 600)
        except (TypeError, ValueError):
            max_tokens = 600
        payload = {
            "model": os.environ.get("DEMEX_AI_MODEL", AI_DEFAULT_MODEL),
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if body.get("system"):
            payload["system"] = str(body["system"])
        req = urllib.request.Request(
            AI_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "x-api-key": key,
                     "anthropic-version": "2023-06-01"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Pass the API's own error message through (it never contains the
            # key); fall back to the bare status if the body isn't JSON.
            try:
                err = json.loads(e.read().decode("utf-8"))
                msg = ((err.get("error") or {}).get("message")
                       or "AI API error (HTTP %d)" % e.code)
            except Exception:
                msg = "AI API error (HTTP %d)" % e.code
            return self._json({"error": msg}, e.code)
        except Exception as e:
            return self._json({"error": "AI request failed: %s" % e}, 502)
        text = "".join(b.get("text", "") for b in data.get("content") or []
                       if isinstance(b, dict) and b.get("type") == "text")
        return self._json({"text": text})


def main() -> int:
    load_dotenv()
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}/DEMEX.html"
    print(f"DEMEX bridge running -> {url}")
    print(f"  serving:  {ROOT}")
    print(f"  python:   {PY}")
    print("  Ctrl+C to stop.")
    # The launcher (run_demex.bat) sets DEMEX_OPEN_BROWSER=1 so the server opens
    # the app itself once the port is bound -- more reliable than opening the
    # browser from the .bat (nested-quote issues). Gated so tests/app use don't
    # pop a tab.
    if os.environ.get("DEMEX_OPEN_BROWSER") == "1":
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
