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
  GET  /api/ratecard                    -> synced rate card JSON (404 if none)
  POST /api/ratecard {rates,rateSettings,rateOverrides}
                                        -> {ok:true,count:N} (writes
                                           demex_rate_card.json atomically)
  GET  /api/plans?project=<name>        -> {ok, payload:{plans:[{path,name,dir}]}}
  POST /api/ingest   {since?}           -> {ok, payload:{clients,projects}}
  POST /api/takeoff  {pdf,project,...}  -> {ok, payload:{clients,projects}}
  POST /api/pricing  {project,history,overheadPct,profitPct}
                                        -> {ok, payload:{estimate,estQty,...}}
  POST /api/proposal {project,company,client}  -> application/pdf (download)
  POST /api/ai       {prompt,max_tokens?,system?}  -> {text}
  GET  /api/corrections                 -> {ok, corrections:[...]} (newest
                                           first, cap 1000)
  POST /api/corrections {corrections:[{project_id,svc,unit,ai_qty,
                                       corrected_qty,delta_pct,kind,source,ts}]}
                                        -> {ok:true,count:N,total:M} (upserts
                                           estimator corrections into
                                           demex_records.db for the learning
                                           layer)
  POST /api/analyze-plan {pdf,pages?,project?}
                                        -> {ok, payload:{suggestions,...},
                                            model, ai_generated,
                                            pages_analyzed} (AI scope-item
                                           suggestions from plan-sheet text;
                                           never auto-applied)
  POST /api/vision-takeoff {pdf,pages?,project?,hints?,tiled?}
                                        -> {ok, payload:{line_items,
                                            sheet_summary, scale_found,
                                            scope_narrative, risks,
                                            exclusions_suggested},
                                            model, ai_generated,
                                            pages_analyzed, tiled,
                                            images_sent, disclaimer}
                                           (AI VISION takeoff over rendered
                                           plan-sheet images; quantities only
                                           where derivable from printed
                                           evidence -- otherwise qty null +
                                           needs_measure. Never auto-applied.
                                           tiled adds 4 overlapping zoomed
                                           quadrant crops per page for dense
                                           sheets: defaults true for 1-2
                                           pages, false for 3+; explicit
                                           value wins, capped at
                                           VISION_MAX_IMAGES images/request)

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
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

# When packaged as the desktop app (PyInstaller, see demex_desktop.py) the
# project files live next to DEMEX.exe, not next to this module.
FROZEN = bool(getattr(sys, "frozen", False))
ROOT = (Path(sys.executable).resolve().parent if FROZEN
        else Path(__file__).resolve().parent)
PORT = int(os.environ.get("DEMEX_BRIDGE_PORT", "8765"))
PY = sys.executable                          # avoids the MS Store python stub
# Command prefix used to run the CLI tools. In the frozen desktop build there
# is no python.exe; DEMEX.exe doubles as the interpreter via --run-script.
PY_CMD = [PY, "--run-script"] if FROZEN else [PY]
DB = str(ROOT / "demex_records.db")
TOOL_TIMEOUT = 1800                          # seconds; email scans can be slow

# Live rate card synced from the app (single source of truth for backend
# pricing). Written atomically by POST /api/ratecard; read at startup by
# pricing_engine.py and takeoff_scalar.py. Runtime state -- gitignored.
RATE_CARD_FILE = ROOT / "demex_rate_card.json"

# Per-run auth token for state-changing requests. Held only in memory; the UI
# obtains it via GET /api/token (same-origin only -- no CORS headers exist).
TOKEN = secrets.token_urlsafe(32)

# Hosts a browser may legitimately use to reach this loopback-bound server.
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}

# ---- Server mode (Fly.io / any Docker host) ---------------------------------
# DEMEX_BIND=0.0.0.0 makes the bridge listen publicly; DEMEX_ALLOWED_HOSTS adds
# the public hostname(s) (comma-separated) to the Host-header allowlist;
# DEMEX_ACCESS_KEY turns on sign-in: every request except /login needs the
# session cookie that /api/login sets after a correct key. Keys are compared in
# constant time; the cookie is an HMAC of the key, so the key itself never sits
# in the browser.
BIND = os.environ.get("DEMEX_BIND", "127.0.0.1")
ALLOWED_HOSTS |= {h.strip().lower() for h in
                  (os.environ.get("DEMEX_ALLOWED_HOSTS") or "").split(",") if h.strip()}
ACCESS_KEY = (os.environ.get("DEMEX_ACCESS_KEY") or "").strip()
# Workspaces: one access key per company. DEMEX_ACCESS_KEYS is a JSON object
#   {"<key>": {"org": "ddd", "name": "Disassemble Dynamics", "apps": ["hub","demex","estimatio","qualora","pavora"]},
#    "<key>": {"org": "wcp", "name": "Wayne's Carpet Plus",  "apps": ["estimatio","pavora"]}}
# DEMEX_ACCESS_KEY alone = the Disassemble Dynamics key with every app. Each
# workspace's data is its own row in app_state (demex is DDD-only; estimatio is
# stored per workspace), so one company never sees another's bids.
ORGS = {
    "ddd": {"name": "Disassemble Dynamics", "apps": ["hub", "demex", "estimatio", "qualora", "pavora"]},
    "wcp": {"name": "Wayne's Carpet Plus", "apps": ["estimatio", "pavora"]},
}
def _load_keys() -> dict:
    keys = {}
    raw = (os.environ.get("DEMEX_ACCESS_KEYS") or "").strip()
    if raw:
        try:
            for k, v in json.loads(raw).items():
                if k and isinstance(v, dict) and v.get("org"):
                    keys[k.strip()] = {"org": v["org"], "name": v.get("name") or ORGS.get(v["org"], {}).get("name", v["org"]),
                                       "apps": v.get("apps") or ORGS.get(v["org"], {}).get("apps", [])}
        except Exception as e:
            sys.stderr.write("[bridge] DEMEX_ACCESS_KEYS is not valid JSON: %s\n" % e)
    if ACCESS_KEY:
        keys.setdefault(ACCESS_KEY, {"org": "ddd", "name": ORGS["ddd"]["name"], "apps": ORGS["ddd"]["apps"]})
    return keys
ACCESS_KEYS = _load_keys()
SERVER_MODE = bool(ACCESS_KEYS)
import hashlib
import threading
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import demex_apps                            # app family: /hub/ /demex/ /estimatio/ /qualora/
APP_VERSION = os.environ.get("DEMEX_VERSION") or datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
SESSION_COOKIE = "demex_session"
_SESSION_SECRET = hashlib.sha256(("demex-session:" + "|".join(sorted(ACCESS_KEYS))).encode()).digest()
def _session_value(org: str = "ddd") -> str:
    """Cookie = <org>.<hmac>; the key never sits in the browser."""
    return org + "." + hmac.new(_SESSION_SECRET, org.encode(), hashlib.sha256).hexdigest()
def _session_org(cookie: str) -> str | None:
    if "." not in (cookie or ""):
        return None
    org = cookie.split(".", 1)[0]
    return org if hmac.compare_digest(cookie, _session_value(org)) else None
# Shared app state (the UI's whole DB as JSON) lives in SQLite so every device
# sees the same data. One row per app ("demex", "estimatio").
STATE_TABLE_SQL = """CREATE TABLE IF NOT EXISTS app_state (
    app TEXT PRIMARY KEY, data TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL)"""

AI_API_URL = "https://api.anthropic.com/v1/messages"
AI_DEFAULT_MODEL = "claude-sonnet-4-6"

# Canonical scope/service vocabulary the app understands. /api/analyze-plan
# instructs the model to suggest ONLY these svc ids.
SVC_VOCAB = (
    "demo_walls", "demo_floor", "demo_base", "demo_ceiling",
    "demo_gyp_ceiling", "millwork", "doors", "storefront", "mep",
    "light_fixture", "diffuser", "plumb_fixture", "concrete_flat",
    "concrete_cut", "masonry", "exterior_demo", "asphalt", "mezzanine_demo",
    "casework_demo", "slab_cut_patch", "door_window_infill",
    "water_heater_demo", "interior_strip_out", "slab_removal",
    "structure_demo", "fence_demo", "utility_demo", "abate_containment",
    "abate_linear",
)

# Text handed to the model per analyze-plan call is capped at this many chars.
PLAN_TEXT_CAP = 12000

# /api/vision-takeoff page rendering knobs. Pages render at up to ~2.2x
# (targeting a long edge of ~2500 px, within the 2200-2800 px sweet spot for
# reading plan-sheet text) and any PNG over the byte cap is re-rendered
# smaller so each image stays well inside the Messages API image limits.
VISION_PAGE_DEFAULT = 4                      # pages analyzed when none given
VISION_PAGE_CAP = 6                          # hard cap on pages per request
VISION_TARGET_LONG_PX = 2500.0               # aim: long edge ~2200-2800 px
VISION_MAX_SCALE = 2.2                       # never upscale beyond ~2.2x
VISION_MIN_SCALE = 0.35                      # floor for giant E-size sheets
VISION_PNG_BYTE_CAP = int(4.5 * 1024 * 1024)  # re-render smaller above this
VISION_HINTS_CAP = 2000                      # chars of estimator hints kept

# Tiled vision mode: dense sheets (medical renos etc.) defeat a single
# full-page image -- keynote tags become unreadable. When tiled, each page is
# sent as the normal full view PLUS 4 overlapping quadrant crops. Each
# quadrant covers VISION_TILE_FRAC of the page's width/height, leaving a
# (2*FRAC - 1) = 10% overlap band in the middle, and is cut from ONE
# high-detail render (crop with PIL, never re-render per tile) whose scale
# targets a tile long edge of ~VISION_TILE_TARGET_LONG_PX (~2x the effective
# resolution of the full view). VISION_PNG_BYTE_CAP still applies per image
# (oversize images are progressively downscaled). Total images per request
# (pages x 5 when tiled) are hard-capped at VISION_MAX_IMAGES, so tiled mode
# effectively allows 2 pages; the default therefore turns tiling on only for
# 1-2 page requests.
VISION_TILE_FRAC = 0.55                      # quadrant covers 55% of w/h
VISION_TILE_TARGET_LONG_PX = 2400.0          # tile long edge ~2200-2600 px
VISION_TILE_MAX_SCALE = 8.0                  # sanity clamp, high-detail render
VISION_MAX_IMAGES = 10                       # images per request, hard cap


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
    proc = subprocess.run([*PY_CMD, *args], cwd=str(ROOT), text=True,
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


class _AIError(Exception):
    """Anthropic call failure carrying the HTTP status to send to the UI."""

    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.msg, self.code = msg, code


def call_ai(prompt: str, max_tokens: int = 600,
            system: str | None = None,
            content: list | None = None) -> tuple[str, str]:
    """Send one prompt to the Anthropic Messages API; return (text, model).

    Shared by /api/ai, /api/analyze-plan and /api/vision-takeoff. The key
    comes from ANTHROPIC_API_KEY (loaded from .env by load_dotenv() at
    startup) and is never logged or echoed. Raises _AIError with a UI-safe
    message + status.

    When `content` is given it is used verbatim as the user message content
    (a Messages-API content-block list, e.g. image + text blocks for vision
    calls) and `prompt` is ignored; existing text-only callers are unchanged.
    """
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise _AIError("ANTHROPIC_API_KEY not set in .env", 503)
    model = os.environ.get("DEMEX_AI_MODEL", AI_DEFAULT_MODEL)
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user",
                      "content": content if content is not None else prompt}],
    }
    if system:
        payload["system"] = system
    req = urllib.request.Request(
        AI_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "x-api-key": key,
                 "anthropic-version": "2023-06-01"},
        method="POST")
    # Vision (content-block) calls carry images and larger outputs; give
    # them longer on the wire than plain text prompts.
    timeout = 180 if content is not None else 60
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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
        raise _AIError(msg, e.code) from None
    except Exception as e:
        raise _AIError("AI request failed: %s" % e, 502) from None
    text = "".join(b.get("text", "") for b in data.get("content") or []
                   if isinstance(b, dict) and b.get("type") == "text")
    return text, model


def extract_plan_text(pdf_path: str,
                      pages: list[int] | None = None) -> tuple[str, list[int]]:
    """Extract text from the given 1-based pages of a plan PDF via pypdf.

    Defaults to the first 3 pages; out-of-range page numbers are skipped.
    Returns (text, pages_actually_read). pypdf is imported here -- not at
    module level -- so the bridge still runs without it; the caller turns the
    ImportError into a 503.
    """
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    n = len(reader.pages)
    want = pages if pages else [1, 2, 3]
    used, chunks = [], []
    for p in want:
        if isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= n:
            used.append(p)
            chunks.append("--- page %d ---\n%s"
                          % (p, reader.pages[p - 1].extract_text() or ""))
    return "\n".join(chunks), used


def _pdf_page_count(pdf_path: str) -> int:
    """Page count via pypdfium2 (imported here; ImportError -> caller's 503)."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()


def render_plan_pages(pdf_path: str,
                      pages: list[int] | None = None,
                      tiled: bool = False) -> list[dict]:
    """Render 1-based plan pages to base64 PNGs via pypdfium2.

    Defaults to the first VISION_PAGE_DEFAULT pages; out-of-range page
    numbers are skipped (mirrors extract_plan_text). Scale targets a long
    edge of ~VISION_TARGET_LONG_PX px, clamped to
    [VISION_MIN_SCALE, VISION_MAX_SCALE]; any PNG that exceeds
    VISION_PNG_BYTE_CAP is progressively shrunk.

    Non-tiled: one full-view image per page, kind "full".
    Tiled: per page, the page is rendered ONCE at high detail (scale targets
    a tile long edge of ~VISION_TILE_TARGET_LONG_PX px, i.e. ~2x the full
    view's effective resolution), then the full view is a PIL downscale of
    that render and 4 overlapping quadrant tiles (each VISION_TILE_FRAC of
    width/height -> 10% overlap band in the middle) are PIL crops of it --
    never a re-render per tile. Tile entries carry kind "top-left" /
    "top-right" / "bottom-left" / "bottom-right" plus their crop "box"
    [l, t, r, b] in high-render pixels; order per page is full view first,
    then the 4 tiles.

    Returns [{"page", "kind", "b64", "px": [w, h], "bytes", "box"?}] for the
    images actually rendered. pypdfium2/Pillow are imported here -- not at
    module level -- so the bridge still runs without them; the caller turns
    ImportError into 503.
    """
    import base64
    import io
    import pypdfium2 as pdfium
    from PIL import Image

    def _encode(pil) -> bytes:
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue()

    def _png(page, scale: float) -> tuple[bytes, tuple[int, int]]:
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil().convert("RGB")
        return _encode(pil), pil.size

    def _shrink_to_cap(pil) -> tuple[bytes, tuple[int, int]]:
        """Encode; while over the byte cap, downscale ~30% per try (max 3)."""
        data = _encode(pil)
        tries = 0
        while len(data) > VISION_PNG_BYTE_CAP and tries < 3:
            w, h = pil.size
            pil = pil.resize((max(1, int(w * 0.7)), max(1, int(h * 0.7))),
                             Image.LANCZOS)
            data = _encode(pil)
            tries += 1
        return data, pil.size

    def _entry(page_no: int, kind: str, data: bytes,
               px: tuple[int, int], box=None) -> dict:
        d = {"page": page_no, "kind": kind,
             "b64": base64.b64encode(data).decode("ascii"),
             "px": [px[0], px[1]], "bytes": len(data)}
        if box is not None:
            d["box"] = list(box)
        return d

    doc = pdfium.PdfDocument(pdf_path)
    try:
        n = len(doc)
        want = pages if pages else list(range(1, min(VISION_PAGE_DEFAULT, n) + 1))
        out = []
        for p in want:
            if not (isinstance(p, int) and not isinstance(p, bool)
                    and 1 <= p <= n):
                continue
            page = doc[p - 1]
            try:
                w_pts, h_pts = page.get_size()
                long_pts = max(w_pts, h_pts) or 1.0
                full_scale = max(VISION_MIN_SCALE,
                                 min(VISION_MAX_SCALE,
                                     VISION_TARGET_LONG_PX / long_pts))
                if tiled:
                    # ONE high-detail render per page; full view + tiles are
                    # derived from it with PIL below (after page.close()).
                    hi_scale = max(full_scale,
                                   min(VISION_TILE_MAX_SCALE,
                                       VISION_TILE_TARGET_LONG_PX
                                       / (VISION_TILE_FRAC * long_pts)))
                    hi = page.render(scale=hi_scale).to_pil().convert("RGB")
                else:
                    data, px = _png(page, full_scale)
                    tries = 0
                    while len(data) > VISION_PNG_BYTE_CAP and tries < 3:
                        full_scale *= 0.7   # ~2x fewer pixels per retry
                        data, px = _png(page, full_scale)
                        tries += 1
            finally:
                page.close()
            if not tiled:
                out.append(_entry(p, "full", data, px))
                continue
            # Full view: downscale the high render to the normal full-view
            # size instead of re-rendering.
            fw = max(1, int(round(w_pts * full_scale)))
            fh = max(1, int(round(h_pts * full_scale)))
            full_img = (hi.resize((fw, fh), Image.LANCZOS)
                        if (fw, fh) != hi.size else hi)
            data, px = _shrink_to_cap(full_img)
            out.append(_entry(p, "full", data, px))
            # 4 overlapping quadrant crops (crop, then check byte size).
            W, H = hi.size
            lw = int(round(W * VISION_TILE_FRAC))
            lh = int(round(H * VISION_TILE_FRAC))
            x1, y1 = W - lw, H - lh          # right/bottom tiles start at 45%
            for kind, box in (("top-left", (0, 0, lw, lh)),
                              ("top-right", (x1, 0, W, lh)),
                              ("bottom-left", (0, y1, lw, H)),
                              ("bottom-right", (x1, y1, W, H))):
                tdata, tpx = _shrink_to_cap(hi.crop(box))
                out.append(_entry(p, kind, tdata, tpx, box=box))
        return out
    finally:
        doc.close()


def _corrections_db() -> sqlite3.Connection:
    """Open demex_records.db with WAL + busy timeout; ensure corrections table.

    The UNIQUE key columns (project_id, svc, unit, source) are normalized to
    "" (never NULL) by the POST handler -- SQLite treats NULLs as distinct in
    UNIQUE constraints, which would break the upsert.
    """
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS corrections (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id    TEXT,
            project_name  TEXT,
            svc           TEXT NOT NULL,
            unit          TEXT,
            ai_qty        REAL,
            corrected_qty REAL,
            delta_pct     REAL,
            kind          TEXT,
            source        TEXT,
            ts            TEXT,
            UNIQUE(project_id, svc, unit, source)
        )""")
    return conn


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

    # ---- server-mode sign-in ----
    def _cookies(self) -> dict:
        out = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out

    def _signed_in(self) -> bool:
        if not SERVER_MODE:
            return True
        return _session_org(self._cookies().get(SESSION_COOKIE, "")) is not None

    def _org(self) -> str:
        if not SERVER_MODE:
            return "ddd"
        return _session_org(self._cookies().get(SESSION_COOKIE, "")) or "ddd"

    def _org_info(self) -> dict:
        org = self._org()
        for v in ACCESS_KEYS.values():
            if v["org"] == org:
                return {"org": org, "name": v["name"], "apps": v["apps"]}
        return {"org": org, "name": ORGS.get(org, {}).get("name", org), "apps": ORGS.get(org, {}).get("apps", [])}

    def _app_allowed(self, key: str) -> bool:
        return (not SERVER_MODE) or key in self._org_info()["apps"]

    @staticmethod
    def _safe_next(p: str) -> str:
        """Only same-origin absolute paths may be a sign-in return target."""
        p = (p or "").strip()
        return p if (p.startswith("/") and not p.startswith("//") and "\\" not in p) else "/hub/"

    def _login_page(self, bad=False, next_path="/hub/"):
        if self._safe_next(next_path).startswith("/estimatio"):
            return self._login_page_estimatio(bad, next_path)
        body = ("""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DEMEX — sign in</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0c0a0a;color:#fff;font-family:Inter,system-ui,sans-serif}
form{background:#151212;border:1px solid rgba(201,191,181,.16);border-top:3px solid #f2000e;text-align:center;border-radius:16px;padding:28px;width:min(360px,90vw);box-shadow:0 8px 24px rgba(0,0,0,.45)}
h1{font-size:22px;margin:0 0 4px;letter-spacing:-.02em}p{color:#a39a92;margin:0 0 18px;font-size:14px}input{width:100%;box-sizing:border-box;padding:11px;border-radius:8px;border:1px solid rgba(201,191,181,.28);background:#1d1919;color:#fff;font:inherit}
button{margin-top:12px;width:100%;padding:11px;border:0;border-radius:8px;background:#f2000e;color:#fff;font:600 14px Inter,system-ui;cursor:pointer}button:hover{background:#c8000c}.logo{display:block;width:112px;height:112px;border-radius:50%;margin:0 auto 14px}.err{color:#FF5C5C;font-size:13px;margin-top:8px}</style></head>
<body><form method="post" action="/api/login"><img class="logo" src="data:image/webp;base64,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" alt=""><h1>Disassemble Dynamics</h1><p>Enter the access key to continue.</p><input type="hidden" name="next" value='""" + self._safe_next(next_path).replace("'", "").replace('"', '') + """'><input type="password" name="key" placeholder="Access key" autofocus autocomplete="current-password">"""
        + ('<div class="err">That key is not correct.</div>' if bad else "") +
        """<button type="submit">Sign in</button></form></body></html>""").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _login_page_estimatio(self, bad=False, next_path="/estimatio/"):
        """Estimatio signs in on its own public page (/estimatio/#login), which posts
        the key to /api/login like the other apps."""
        return self._redirect("/estimatio/#login" + ("-bad" if bad else ""), 303)

    def _logout(self):
        ref = self.headers.get("Referer") or ""
        back = "/login?next=/estimatio/" if "/estimatio" in ref else "/login"
        self.send_response(303)
        self.send_header("Location", back)
        self.send_header("Set-Cookie", "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % SESSION_COOKIE)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _login(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        ctype = self.headers.get("Content-Type") or ""
        if "json" in ctype:
            j = json.loads(raw or "{}")
            key = (j.get("key") or "")
            nxt = j.get("next") or "/hub/"
        else:
            form = parse_qs(raw)
            key = form.get("key", [""])[0]
            nxt = form.get("next", ["/hub/"])[0]
        match = None
        for k, v in ACCESS_KEYS.items():
            if hmac.compare_digest(key.strip(), k):
                match = v
        if match:
            nxt_ok = self._safe_next(nxt)
            first = nxt_ok.strip("/").split("/")[0]
            if first in ("hub", "demex", "estimatio", "qualora", "pavora") and first not in match["apps"]:
                nxt_ok = "/%s/" % match["apps"][0] if match["apps"] else "/hub/"
            self.send_response(303)
            self.send_header("Location", nxt_ok)
            self.send_header("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000%s"
                             % (SESSION_COOKIE, _session_value(match["org"]), "; Secure" if BIND != "127.0.0.1" else ""))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        return self._login_page(bad=True, next_path=nxt)

    # ---- shared app state ----
    def _state_key(self, app: str) -> str:
        """demex is Disassemble-only; every other app is stored per workspace."""
        app = re.sub(r"[^a-z0-9_]", "", (app or "demex").lower()) or "demex"
        return app if app == "demex" else "%s:%s" % (app, self._org())

    def _state_get(self, q):
        app = self._state_key((q.get("app") or ["demex"])[0])
        if app == "demex" and not self._app_allowed("demex"):
            return self._json({"ok": False, "error": "DEMEX is not part of your workspace"}, 403)
        conn = sqlite3.connect(DB)
        try:
            conn.execute(STATE_TABLE_SQL)
            row = conn.execute("SELECT data, version, updated_at FROM app_state WHERE app=?", (app,)).fetchone()
        finally:
            conn.close()
        if not row:
            return self._json({"ok": True, "payload": None, "version": 0})
        return self._json({"ok": True, "payload": json.loads(row[0]), "version": row[1], "updatedAt": row[2]})

    def _state_put(self, body):
        app = self._state_key(str(body.get("app") or "demex"))
        if app == "demex" and not self._app_allowed("demex"):
            return self._json({"ok": False, "error": "DEMEX is not part of your workspace"}, 403)
        data = body.get("data")
        if not isinstance(data, dict):
            return self._json({"ok": False, "error": "data must be an object"}, 400)
        expected = body.get("version")
        conn = sqlite3.connect(DB)
        try:
            conn.execute(STATE_TABLE_SQL)
            row = conn.execute("SELECT version FROM app_state WHERE app=?", (app,)).fetchone()
            cur = row[0] if row else 0
            if expected is not None and int(expected) != cur:
                return self._json({"ok": False, "error": "conflict", "version": cur}, 409)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT INTO app_state(app,data,version,updated_at) VALUES(?,?,?,?) "
                         "ON CONFLICT(app) DO UPDATE SET data=excluded.data, version=excluded.version, updated_at=excluded.updated_at",
                         (app, json.dumps(data), cur + 1, now))
            conn.commit()
        finally:
            conn.close()
        return self._json({"ok": True, "version": cur + 1})

    # ---- routing ----
    def do_GET(self):
        if not self._host_allowed():
            return self._json({"ok": False, "error": "forbidden host"}, 403)
        u = urlparse(self.path)
        if u.path in ("/", ""):
            return self._redirect("/hub/")
        if u.path == "/login":
            return self._login_page(next_path=(parse_qs(u.query).get("next") or ["/hub/"])[0])
        if u.path == "/healthz":
            return self._json({"ok": True})
        if u.path == "/api/logout":
            return self._logout()
        # App-family public files: manifests, service workers and icons carry no
        # data and must load before sign-in so the home-screen icon is right.
        app_m = re.fullmatch(r"/(hub|demex|estimatio|qualora|pavora)/(manifest\.webmanifest|sw\.js)", u.path)
        if app_m:
            key, what = app_m.group(1), app_m.group(2)
            if what == "sw.js":
                return self._raw(demex_apps.service_worker(key, APP_VERSION).encode(), "application/javascript; charset=utf-8",
                                 extra={"Service-Worker-Allowed": "/%s/" % key})
            return self._raw(demex_apps.manifest(key).encode(), "application/manifest+json; charset=utf-8")
        if u.path.startswith(demex_apps.PUBLIC_PREFIX) and self._static_allowed(u):
            return super().do_GET()
        # Estimatio's page is public: signed out it shows its main page, pricing and
        # sign-in form. Its data (/api/state and the rest) still needs the session.
        if u.path == "/api/mode":
            return self._json({"ok": True, "server": SERVER_MODE, "signedIn": self._signed_in()})
        if u.path == "/estimatio" and not self._signed_in():
            return self._redirect("/estimatio/")
        if u.path == "/estimatio/" and not self._signed_in():
            return self._app_page("estimatio")
        if not self._signed_in():
            if u.path.startswith("/api/"):
                return self._json({"ok": False, "error": "sign in required"}, 401)
            return self._login_page(next_path=u.path)
        # Clean app addresses: /demex -> /demex/ -> DEMEX.html with PWA head tags.
        if re.fullmatch(r"/(hub|demex|estimatio|qualora|pavora)", u.path):
            return self._redirect(u.path + "/" + ("?" + u.query if u.query else ""))
        app_m = re.fullmatch(r"/(hub|demex|estimatio|qualora|pavora)/", u.path)
        if app_m:
            if not self._app_allowed(app_m.group(1)):
                info = self._org_info()
                return self._raw(("<!DOCTYPE html><meta charset=utf-8><body style='font-family:Inter,system-ui;background:#0c0a0a;color:#fff;display:grid;place-items:center;min-height:100vh;margin:0'><div style='text-align:center'><h1 style='margin:0 0 6px'>Not part of your workspace</h1><p style='color:#a39a92'>%s has access to: %s.</p><a style='color:#ff3b44' href='/%s/'>Open %s</a> · <a style='color:#a39a92' href='/api/logout'>Sign out</a></div></body>"
                                  % (info["name"], ", ".join(info["apps"]) or "nothing yet", (info["apps"] or ["hub"])[0], (info["apps"] or ["hub"])[0])).encode(), "text/html; charset=utf-8", 403)
            return self._app_page(app_m.group(1))
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
        if u.path == "/api/login":
            return self._login()
        if not u.path.startswith("/api/"):
            return self.send_error(404, "Not found")
        if not self._signed_in():
            return self._json({"ok": False, "error": "sign in required"}, 401)
        if not self._token_ok():
            return self._json({"error": "missing or bad token"}, 403)
        try:
            return self._api_post(u)
        except subprocess.TimeoutExpired:
            self._json({"ok": False, "error": "Tool timed out."}, 504)
        except Exception as e:                # never leak a stack to the UI
            self._json({"ok": False, "error": str(e)}, 500)

    def _redirect(self, where: str, code: int = 302):
        self.send_response(code); self.send_header("Location", where); self.send_header("Content-Length", "0"); self.end_headers()

    def _raw(self, body: bytes, ctype: str, code: int = 200, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _app_page(self, key: str):
        """Serve an app of the family with its manifest / iPhone / service-worker
        head tags injected. Qualora forwards to QUALORA_URL when it runs elsewhere."""
        if key == "qualora" and not (ROOT / "apps/qualora/app.html").is_file() and os.environ.get("QUALORA_URL"):
            return self._redirect(os.environ["QUALORA_URL"])
        f = demex_apps.app_file(key)
        if not f:
            return self.send_error(404, "App not deployed on this server")
        html = f.read_text(encoding="utf-8", errors="replace")
        return self._raw(demex_apps.inject(html, key).encode("utf-8"), "text/html; charset=utf-8")

    # ---- static files: only visible .html files (plus app icons), nothing else ----
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
        if decoded.startswith(demex_apps.PUBLIC_PREFIX) and decoded.endswith(".png"):
            return True
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
        if path == "ratecard":
            return self._ratecard_get()
        if path == "plans":
            proj = (q.get("project") or [""])[0]
            return self._json({"ok": True,
                               "payload": {"plans": list_plans(proj)}})
        if path == "corrections":
            return self._corrections_get()
        if path == "itb-imports":
            return self._itb_imports(q)
        if path == "state":
            return self._state_get(q)
        if path == "me":
            return self._json(dict({"ok": True, "server": SERVER_MODE}, **self._org_info()))
        if path == "mode":
            return self._json({"ok": True, "server": SERVER_MODE})
        if path == "apps":
            return self._json({"ok": True, "apps": demex_apps.status(), "backup": _backup_status()})
        if path == "backup":
            return self._json({"ok": True, "backup": _backup_status()})
        return self._json({"ok": False, "error": "Unknown endpoint: " + path}, 404)

    def _itb_imports(self, q):
        """Merge every demex_itb_import_*.json (the daily inbox-sweep drops)
        plus demex_pipeline_update_*.json into one clients/projects payload.

        The UI calls this on startup and adds anything it hasn't seen, so the
        sweep files no longer need a manual Pipeline -> Import. Files are read
        in date order; when the same project shows up on several days (same
        normalized name) the LATEST copy wins, so moved due dates come through.
        ?since=YYYY-MM-DD limits to files on/after that date.
        """
        since = (q.get("since") or [""])[0].strip()
        pats = ("demex_itb_import_*.json", "demex_pipeline_update_*.json")
        # The desktop build lives in its own folder (dist\DEMEX or
        # %LOCALAPPDATA%\DEMEX) while the morning sweep keeps dropping files in
        # the source folder, so also scan DEMEX_IMPORTS_DIR (set in .env).
        dirs = [ROOT]
        extra = (os.environ.get("DEMEX_IMPORTS_DIR") or "").strip()
        if extra and Path(extra).is_dir() and Path(extra).resolve() != ROOT:
            dirs.append(Path(extra))
        files = []
        for d in dirs:
            for pat in pats:
                files += [f for f in d.glob(pat) if f.is_file()]
        date_re = re.compile(r"(\d{4}-\d{2}-\d{2})")

        def fdate(f):
            m = date_re.search(f.name)
            return m.group(1) if m else "0000-00-00"
        files.sort(key=lambda f: (fdate(f), f.name))
        if since:
            files = [f for f in files if fdate(f) >= since]

        def key(name):
            return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
        clients, projects, bad = {}, {}, []
        for f in files:
            data = _read_json_file(str(f))
            if not isinstance(data, dict):
                bad.append(f.name)
                continue
            for c in data.get("clients") or []:
                if isinstance(c, dict) and c.get("id"):
                    clients[c["id"]] = c
            for pj in data.get("projects") or []:
                if not isinstance(pj, dict) or not pj.get("id"):
                    continue
                k = key(pj.get("name")) or pj["id"]
                prev = projects.get(k)
                if prev:
                    # keep the first id the UI may already know; refresh the
                    # fields that change between sweeps
                    pj = dict(prev, **{kk: vv for kk, vv in pj.items()
                                       if vv not in (None, "", 0, [])})
                    pj["id"] = prev["id"]
                pj["_source_file"] = f.name
                projects[k] = pj
        return self._json({"ok": True, "payload": {
            "clients": list(clients.values()),
            "projects": list(projects.values()),
            "files": len(files), "unreadable": bad}})

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

    def _ratecard_get(self):
        """Serve the last rate card the app synced (GET /api/ratecard)."""
        if not RATE_CARD_FILE.is_file():
            return self._json({"error": "no rate card synced yet"}, 404)
        data = _read_json_file(str(RATE_CARD_FILE))
        if data is None:
            return self._json({"error": "rate card file is unreadable"}, 500)
        return self._json(data)

    def _ratecard_post(self, body):
        """Persist the app's live rate card (POST /api/ratecard, token-gated).

        Body: {"rates":[{id,label,unit,low,high,...}...],
               "rateSettings":{tier,regionMult,region},
               "rateOverrides":{svc: pct}}
        Validated, stamped with savedAt/source, and written ATOMICALLY to
        demex_rate_card.json (tempfile in ROOT + os.replace) so a concurrent
        reader (pricing_engine / takeoff_scalar) never sees a partial file.
        """
        rates = body.get("rates")
        if not isinstance(rates, list) or not rates:
            return self._json({"ok": False,
                               "error": "rates must be a non-empty list"}, 400)
        for i, r in enumerate(rates):
            if not isinstance(r, dict):
                return self._json({"ok": False,
                                   "error": f"rates[{i}] is not an object"},
                                  400)
            if not isinstance(r.get("id"), str) or not r["id"].strip():
                return self._json({"ok": False,
                                   "error": f"rates[{i}].id must be a "
                                            f"non-empty string"}, 400)
            for k in ("low", "high"):
                v = r.get(k)
                if isinstance(v, bool) or not isinstance(v, (int, float)) \
                        or v < 0:
                    return self._json(
                        {"ok": False,
                         "error": f"rates[{i}].{k} must be a number >= 0"},
                        400)
        settings = body.get("rateSettings")
        if settings is not None and not isinstance(settings, dict):
            return self._json({"ok": False,
                               "error": "rateSettings must be an object"}, 400)
        overrides = body.get("rateOverrides")
        if overrides is not None and not isinstance(overrides, dict):
            return self._json({"ok": False,
                               "error": "rateOverrides must be an object"},
                              400)
        doc = dict(body)
        doc["savedAt"] = (datetime.now(timezone.utc)
                          .isoformat(timespec="seconds")
                          .replace("+00:00", "Z"))
        doc["source"] = "DEMEX.html"
        tmp = _tmp(".json")
        try:
            Path(tmp).write_text(json.dumps(doc, indent=2), encoding="utf-8")
            os.replace(tmp, str(RATE_CARD_FILE))
        except BaseException:
            _unlink(tmp)
            raise
        return self._json({"ok": True, "count": len(rates)})

    def _api_post(self, u):
        path = u.path[len("/api/"):]
        body = self._body()
        if path == "backup":
            res = run_backup_now()
            return self._json({"ok": bool(res.get("ok")), "backup": res}, 200 if res.get("ok") else 500)
        if path == "state":
            return self._state_put(body)
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
        if path == "ratecard":
            return self._ratecard_post(body)
        if path == "corrections":
            return self._corrections_post(body)
        if path == "analyze-plan":
            return self._analyze_plan(body)
        if path == "vision-takeoff":
            return self._vision_takeoff(body)
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

        Thin wrapper over the shared call_ai() helper (same behavior as
        before the /api/analyze-plan refactor: key checked first, then the
        prompt, so an unconfigured install always surfaces the 503).
        """
        if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
            return self._json({"error": "ANTHROPIC_API_KEY not set in .env"},
                              503)
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return self._json({"error": "prompt is required."}, 400)
        try:
            max_tokens = int(body.get("max_tokens") or 600)
        except (TypeError, ValueError):
            max_tokens = 600
        system = str(body["system"]) if body.get("system") else None
        try:
            text, _model = call_ai(prompt, max_tokens, system)
        except _AIError as e:
            return self._json({"error": e.msg}, e.code)
        return self._json({"text": text})

    def _corrections_post(self, body):
        """Record estimator corrections to AI takeoffs (learning signal).

        Body: {"corrections":[{project_id, project_name?, svc, unit, ai_qty,
                               corrected_qty, delta_pct, kind, source, ts}]}
        Each row is upserted on (project_id, svc, unit, source) so re-syncing
        the same project updates the latest correction instead of duplicating.
        """
        corr = body.get("corrections")
        if not isinstance(corr, list) or not corr:
            return self._json({"ok": False,
                               "error": "corrections must be a non-empty "
                                        "list"}, 400)
        rows = []
        for i, c in enumerate(corr):
            if not isinstance(c, dict):
                return self._json({"ok": False,
                                   "error": f"corrections[{i}] is not an "
                                            f"object"}, 400)
            svc = c.get("svc")
            if not isinstance(svc, str) or not svc.strip():
                return self._json({"ok": False,
                                   "error": f"corrections[{i}].svc must be a "
                                            f"non-empty string"}, 400)
            for k in ("ai_qty", "corrected_qty"):
                v = c.get(k)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    return self._json(
                        {"ok": False,
                         "error": f"corrections[{i}].{k} must be a number"},
                        400)
            kind = str(c.get("kind") or "adjusted")
            if kind not in ("adjusted", "removed", "added"):
                return self._json(
                    {"ok": False,
                     "error": f"corrections[{i}].kind must be adjusted, "
                              f"removed or added"}, 400)
            delta = c.get("delta_pct")
            if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                delta = None
            # UNIQUE-key columns normalized to "" (NULLs are distinct in
            # SQLite UNIQUE constraints and would break the upsert).
            rows.append((str(c.get("project_id") or ""),
                         str(c.get("project_name") or ""),
                         svc.strip(),
                         str(c.get("unit") or ""),
                         float(c["ai_qty"]),
                         float(c["corrected_qty"]),
                         delta,
                         kind,
                         str(c.get("source") or ""),
                         str(c.get("ts") or "")))
        conn = _corrections_db()
        try:
            with conn:
                for row in rows:
                    conn.execute("""
                        INSERT INTO corrections
                            (project_id, project_name, svc, unit, ai_qty,
                             corrected_qty, delta_pct, kind, source, ts)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(project_id, svc, unit, source) DO UPDATE
                        SET project_name  = excluded.project_name,
                            ai_qty        = excluded.ai_qty,
                            corrected_qty = excluded.corrected_qty,
                            delta_pct     = excluded.delta_pct,
                            kind          = excluded.kind,
                            ts            = excluded.ts""", row)
            total = conn.execute(
                "SELECT COUNT(*) FROM corrections").fetchone()[0]
        finally:
            conn.close()
        return self._json({"ok": True, "count": len(rows), "total": total})

    def _corrections_get(self):
        """All recorded corrections, newest first (cap 1000)."""
        cols = ("id", "project_id", "project_name", "svc", "unit", "ai_qty",
                "corrected_qty", "delta_pct", "kind", "source", "ts")
        conn = _corrections_db()
        try:
            cur = conn.execute(
                "SELECT %s FROM corrections ORDER BY ts DESC, id DESC "
                "LIMIT 1000" % ", ".join(cols))
            out = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            conn.close()
        return self._json({"ok": True, "corrections": out})

    def _analyze_plan(self, body):
        """AI scope-suggestion pass over plan-sheet TEXT (never auto-applied).

        Body: {"pdf": path, "pages": [1-based page numbers] (default first
        3), "project": optional name}. Extracts text with pypdf, asks the
        model for scope ITEMS ONLY (no quantities -- those come from
        measurement) restricted to the SVC_VOCAB ids, and returns the parsed
        suggestions for the estimator to review.
        """
        pdf = body.get("pdf")
        if not pdf:
            return self._json({"ok": False, "error": "pdf is required."}, 400)
        pdf = resolve_pdf_path(str(pdf))
        if pdf is None:
            return self._json({"ok": False,
                               "error": "pdf path outside allowed directories"},
                              400)
        if not os.path.isfile(pdf):
            return self._json({"ok": False, "error": "pdf not found"}, 400)
        pages = body.get("pages")
        if pages is not None:
            if not isinstance(pages, list):
                return self._json({"ok": False,
                                   "error": "pages must be a list of page "
                                            "numbers"}, 400)
            try:
                pages = [int(p) for p in pages]
            except (TypeError, ValueError):
                return self._json({"ok": False,
                                   "error": "pages must be a list of page "
                                            "numbers"}, 400)
        project = str(body.get("project") or "").strip()
        if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
            return self._json({"ok": False,
                               "error": "ANTHROPIC_API_KEY not set in .env"},
                              503)
        try:
            text, used = extract_plan_text(pdf, pages)
        except ImportError:
            return self._json({"ok": False,
                               "error": "pypdf is not installed -- run "
                                        "'pip install pypdf' to enable "
                                        "/api/analyze-plan"}, 503)
        except Exception as e:
            return self._json({"ok": False,
                               "error": "Could not read PDF text: %s" % e},
                              400)
        if not text.strip() or not used:
            return self._json({"ok": False,
                               "error": "No text could be extracted from the "
                                        "requested pages (scanned/raster "
                                        "sheets have no text layer)."})
        truncated = len(text) > PLAN_TEXT_CAP
        prompt = (
            "You are a commercial demolition estimator's assistant analyzing "
            "the text extracted from construction plan sheets.\n"
            "Project: %s\n"
            "Pages analyzed (1-based): %s\n\n"
            "Allowed svc ids (use ONLY these, verbatim):\n%s\n\n"
            "Plan-sheet text%s:\n<<<PLAN TEXT\n%s\nPLAN TEXT>>>\n\n"
            "Identify demolition scope ITEMS suggested by the notes, "
            "keynotes and legends in the text. Return STRICT JSON only -- no "
            "markdown fences, no commentary -- with exactly this shape:\n"
            '{"suggestions":[{"svc":"<one allowed svc id>",'
            '"item":"<short human label>","unit":"SF|LF|EA",'
            '"reason":"<why -- cite the note/keynote text>",'
            '"sheet_ref":"<page or sheet id if inferable, else null>",'
            '"confidence":"high|medium|low"}],'
            '"notes_found":<count of demolition-relevant notes seen>,'
            '"warnings":["<anything ambiguous or concerning>"]}\n\n'
            "Rules: suggestions are scope ITEMS ONLY -- never include "
            "quantities (quantities come from measurement, not from notes). "
            "Max 25 suggestions. Include only items with textual evidence in "
            "the plan text above; never invent scope that is not supported "
            "by the text."
            % (project or "(unnamed)",
               used,
               ", ".join(SVC_VOCAB),
               " (truncated to first %d characters)" % PLAN_TEXT_CAP
               if truncated else "",
               text[:PLAN_TEXT_CAP]))
        try:
            reply, model = call_ai(prompt, max_tokens=2000)
        except _AIError as e:
            return self._json({"ok": False, "error": e.msg}, e.code)
        # Tolerant parse: strip markdown fences, take first { .. last }.
        raw = reply.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        i, j = raw.find("{"), raw.rfind("}")
        parsed = None
        if i >= 0 and j > i:
            try:
                parsed = json.loads(raw[i:j + 1])
            except Exception:
                parsed = None
        if not isinstance(parsed, dict):
            return self._json({"ok": False,
                               "error": "AI reply was not valid JSON",
                               "raw": reply[:500]})
        return self._json({"ok": True, "payload": parsed, "model": model,
                           "ai_generated": True, "pages_analyzed": used})

    def _vision_takeoff(self, body):
        """AI VISION takeoff over rendered plan-sheet images (never auto-applied).

        Body: {"pdf": path, "pages": [1-based page numbers] (default first
        VISION_PAGE_DEFAULT, hard cap VISION_PAGE_CAP), "project": optional
        name, "hints": optional free text from the estimator, "tiled":
        optional bool -- defaults true when 1-2 pages are requested/resolved,
        false for 3+ (cost control); an explicit value always wins}. Renders
        the pages to PNG with pypdfium2 (tiled mode adds 4 overlapping zoomed
        quadrant crops per page so dense sheets' keynote tags stay readable;
        total images hard-capped at VISION_MAX_IMAGES per request -> 400),
        sends them to the model as image content
        blocks, and returns line items where a quantity is stated ONLY when
        it is derivable from printed evidence (counts, printed dimensions,
        schedules, stated areas); scope that is visible but not quantifiable
        comes back with qty null + needs_measure true. The estimator reviews
        everything -- nothing is auto-applied.
        """
        pdf = body.get("pdf")
        if not pdf:
            return self._json({"ok": False, "error": "pdf is required."}, 400)
        pdf = resolve_pdf_path(str(pdf))
        if pdf is None:
            return self._json({"ok": False,
                               "error": "pdf path outside allowed directories"},
                              400)
        if not os.path.isfile(pdf):
            return self._json({"ok": False, "error": "pdf not found"}, 400)
        pages = body.get("pages")
        if pages is not None:
            if not isinstance(pages, list):
                return self._json({"ok": False,
                                   "error": "pages must be a list of page "
                                            "numbers"}, 400)
            try:
                pages = [int(p) for p in pages]
            except (TypeError, ValueError):
                return self._json({"ok": False,
                                   "error": "pages must be a list of page "
                                            "numbers"}, 400)
            if len(pages) > VISION_PAGE_CAP:
                return self._json({"ok": False,
                                   "error": "at most %d pages per "
                                            "vision-takeoff request"
                                            % VISION_PAGE_CAP}, 400)
        project = str(body.get("project") or "").strip()
        hints = str(body.get("hints") or "").strip()[:VISION_HINTS_CAP]
        tiled = body.get("tiled")
        if tiled is not None and not isinstance(tiled, bool):
            return self._json({"ok": False,
                               "error": "tiled must be true or false"}, 400)
        # Resolve which pages will actually render so the tiled default
        # (true for 1-2 pages, false for 3+) and the image cap are computed
        # on real pages, then enforce the cap BEFORE any rendering happens.
        try:
            n_doc = _pdf_page_count(pdf)
        except ImportError:
            return self._json({"ok": False,
                               "error": "pypdfium2/Pillow are not installed "
                                        "-- run 'pip install -r "
                                        "requirements.txt' to enable "
                                        "/api/vision-takeoff"}, 503)
        except Exception as e:
            return self._json({"ok": False,
                               "error": "Could not read PDF: %s" % e}, 400)
        if pages:
            resolved = [p for p in pages if 1 <= p <= n_doc]
        else:
            resolved = list(range(1, min(VISION_PAGE_DEFAULT, n_doc) + 1))
        if tiled is None:
            tiled = 0 < len(resolved) <= 2
        per_page = 5 if tiled else 1        # full view + 4 quadrant tiles
        if len(resolved) * per_page > VISION_MAX_IMAGES:
            return self._json(
                {"ok": False,
                 "error": "at most %d images per vision-takeoff request; "
                          "tiled mode sends %d per page (full sheet + 4 "
                          "zoomed tiles), so %d pages would send %d -- "
                          "request at most %d pages or set \"tiled\": false"
                          % (VISION_MAX_IMAGES, per_page, len(resolved),
                             len(resolved) * per_page,
                             VISION_MAX_IMAGES // per_page)}, 400)
        # Render BEFORE the key check so a 503 from call_ai() proves the whole
        # render pipeline ran (and so render errors surface even unconfigured).
        try:
            rendered = render_plan_pages(pdf, pages, tiled=tiled)
        except ImportError:
            return self._json({"ok": False,
                               "error": "pypdfium2/Pillow are not installed "
                                        "-- run 'pip install -r "
                                        "requirements.txt' to enable "
                                        "/api/vision-takeoff"}, 503)
        except Exception as e:
            return self._json({"ok": False,
                               "error": "Could not render PDF pages: %s" % e},
                              400)
        if not rendered:
            return self._json({"ok": False,
                               "error": "No valid pages to render (check the "
                                        "requested page numbers)."}, 400)
        used = list(dict.fromkeys(r["page"] for r in rendered))
        tiling_note = ""
        if tiled:
            tiling_note = (
                "Each page appears as one FULL SHEET image followed by four "
                "ZOOMED quadrant tiles (top-left, top-right, bottom-left, "
                "bottom-right) at roughly 2x detail. COUNT tags/symbols "
                "(keynote tags, fixtures, doors, diffusers) on the zoomed "
                "tiles -- they are the legible copies -- and use the full "
                "view for context, geometry and the title block. The tiles "
                "OVERLAP: a ~10 percent band through the middle of the sheet "
                "appears on two adjacent tiles, so an item in that band "
                "shows up on two tiles -- count each item ONCE by its "
                "position on the sheet, never twice. Report the per-keynote "
                "counts you derived (e.g. 'keynote 3 x 12 across tiles') in "
                "the basis fields.\n\n")
        instructions = (
            "You are a senior commercial demolition estimator performing a "
            "takeoff from the plan-sheet images above.\n"
            "Project: %s\n"
            "Pages shown (1-based): %s\n"
            "Estimator hints: %s\n\n"
            "%s"                             # tiling note ("" when not tiled)
            "Allowed svc ids (use ONLY these, verbatim):\n%s\n\n"
            "For each sheet: identify the sheet type and the drawing scale "
            "(from the title block, graphic scale bar, or dimension strings). "
            "Read the demolition notes, keynotes, legends and schedules.\n\n"
            "Produce demolition takeoff line items. A line item may carry a "
            "QUANTITY ONLY where it is derivable from printed evidence on "
            "the sheets: counting discrete items (doors, fixtures, "
            "diffusers, keynote tags), printed dimensions, room/area "
            "schedules, or explicitly stated areas. For scope that is "
            "visible but NOT quantifiable from printed evidence, include "
            "the line with qty null and needs_measure true. NEVER estimate "
            "a quantity by eyeballing distances on the drawing.\n\n"
            "Return STRICT JSON only -- no markdown fences, no commentary -- "
            "with exactly this shape:\n"
            '{"sheet_summary":"<what sheets these are>",'
            '"scale_found":"<scale(s) found or null>",'
            '"scope_narrative":"<2-3 sentence narrative of the demolition '
            'scope>",'
            '"line_items":[{"item":"<short human label>",'
            '"svc":"<one allowed svc id>","unit":"SF|LF|EA",'
            '"qty":<number or null>,"needs_measure":<true|false>,'
            '"confidence":"high|medium|low",'
            '"basis":"<exactly where the number came from: which '
            'note/keynote/dimension/schedule/count and on which sheet; for '
            'needs_measure lines, what was seen>",'
            '"sheet_ref":"<sheet id or page>"}],'
            '"risks":["<risk>"],'
            '"exclusions_suggested":["<exclusion>"]}\n\n'
            "Rules: every qty must trace to printed evidence named in basis; "
            "qty null whenever needs_measure is true; include only scope "
            "with visible evidence on these sheets; never invent scope."
            % (project or "(unnamed)",
               used,
               hints or "(none)",
               tiling_note,
               ", ".join(SVC_VOCAB)))
        content = []
        for r in rendered:
            kind = r.get("kind", "full")
            if not tiled:
                label = "--- page %d ---" % r["page"]
            elif kind == "full":
                label = "--- page %d (full sheet) ---" % r["page"]
            else:
                label = "--- page %d, tile %s (zoomed) ---" % (r["page"], kind)
            content.append({"type": "text", "text": label})
            content.append({"type": "image",
                            "source": {"type": "base64",
                                       "media_type": "image/png",
                                       "data": r["b64"]}})
        content.append({"type": "text", "text": instructions})
        try:
            reply, model = call_ai("", max_tokens=4000, content=content)
        except _AIError as e:
            return self._json({"ok": False, "error": e.msg}, e.code)
        # Tolerant parse: strip markdown fences, take first { .. last }.
        raw = reply.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        i, j = raw.find("{"), raw.rfind("}")
        parsed = None
        if i >= 0 and j > i:
            try:
                parsed = json.loads(raw[i:j + 1])
            except Exception:
                parsed = None
        if not isinstance(parsed, dict):
            return self._json({"ok": False,
                               "error": "AI reply was not valid JSON",
                               "raw": reply[:500]})
        return self._json({"ok": True, "payload": parsed, "model": model,
                           "ai_generated": True, "pages_analyzed": used,
                           "tiled": tiled, "images_sent": len(rendered),
                           "disclaimer": "AI vision takeoff -- verify every "
                                         "quantity; needs_measure lines have "
                                         "no quantity by design"})


# ---- GitHub backup -----------------------------------------------------------
# tools/github_backup.py pushes the live data (shared app state, corrections,
# rate card, ITB drops, project index) to a private GitHub repo. Runs nightly in
# the background when GITHUB_TOKEN + GITHUB_BACKUP_REPO are set, and on demand
# via POST /api/backup. Status is read from the last run's receipt file.
BACKUP_RECEIPT = ROOT / "backup_last.json"
_backup_lock = threading.Lock()


def _backup_status() -> dict:
    try:
        d = json.loads(BACKUP_RECEIPT.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    d["configured"] = bool(os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_BACKUP_REPO"))
    d.setdefault("last", None)
    return d


def run_backup_now() -> dict:
    if not _backup_lock.acquire(blocking=False):
        return {"ok": False, "error": "a backup is already running"}
    try:
        code, out = run_tool([str(ROOT / "tools" / "github_backup.py"), "--json"])
        try:
            res = json.loads(out.strip().splitlines()[-1])
        except Exception:
            res = {"ok": code == 0, "error": _tail(out)}
        return res
    finally:
        _backup_lock.release()


def _backup_scheduler() -> None:
    hour = int(os.environ.get("GITHUB_BACKUP_HOUR_UTC", "6"))     # 6:00 UTC = 2:00 am Eastern
    import time
    while True:
        now = datetime.now(timezone.utc)
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            from datetime import timedelta
            nxt += timedelta(days=1)
        time.sleep(max(60, (nxt - now).total_seconds()))
        try:
            print("[backup] nightly ->", json.dumps(run_backup_now())[:300])
        except Exception as e:                # the scheduler must never die
            print("[backup] failed:", e, file=sys.stderr)


def make_server(port: int | None = None) -> ThreadingHTTPServer:
    """Load .env and bind the bridge on loopback. Used by main() and by the
    desktop launcher (demex_desktop.py), which runs it in a thread."""
    load_dotenv()
    return ThreadingHTTPServer((BIND, port or PORT), Handler)


def main() -> int:
    httpd = make_server()
    if os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_BACKUP_REPO"):
        threading.Thread(target=_backup_scheduler, name="github-backup", daemon=True).start()
        print("  backup:   nightly to GitHub " + os.environ["GITHUB_BACKUP_REPO"])
    url = f"http://localhost:{PORT}/hub/"
    print(f"DEMEX bridge running -> {url}  (apps: /demex/ /estimatio/ /qualora/)")
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
