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

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("DEMEX_BRIDGE_PORT", "8765"))
PY = sys.executable                          # avoids the MS Store python stub
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
        if path == "ratecard":
            return self._ratecard_get()
        if path == "plans":
            proj = (q.get("project") or [""])[0]
            return self._json({"ok": True,
                               "payload": {"plans": list_plans(proj)}})
        if path == "corrections":
            return self._corrections_get()
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
