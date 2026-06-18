#!/usr/bin/env python3
"""
email_ingestor.py  --  DEMEX bid-email ingestion pipeline
=========================================================

Connects (read-only) to a business Gmail account over IMAP-SSL, finds
bid-related email, extracts structural metadata, downloads PDF attachments
into per-project folders, runs a rule-based line-item parser over those PDFs,
and writes everything into a local SQLite database (`demex_records.db`) whose
schema mirrors the DEMEX v4 estimating columns.

WHAT IS / ISN'T STANDARD LIBRARY
--------------------------------
  * imaplib, email, sqlite3, re, json, hashlib, datetime, argparse, os, ssl
    are all Python standard library -> connection, email parsing, metadata,
    and the database are pure stdlib.
  * PDF text extraction is NOT possible with the standard library. This script
    uses `pdfplumber` if installed, else falls back to `pypdf`. If neither is
    present, email + metadata + DB ingestion still run; PDF line-item parsing
    is skipped with a clear warning. Install with:
        pip install pdfplumber          (preferred)
        pip install pypdf               (lighter fallback)

THE PARSER IS A HEURISTIC, NOT AI
---------------------------------
The "line item reverse-engineering" is deterministic regex + keyword
classification (demolition vs. hazardous-abatement containment). It produces
DRAFT rows flagged `needs_review=1`. A human estimator should confirm prices
and quantities before they are used in a live bid.

SECURITY
--------
  * Authentication uses a Google **App Password** (requires 2-Step Verification
    on the account). Never your normal password; never OAuth secrets in code.
  * Credentials are read from environment variables by default:
        DEMEX_GMAIL_USER          e.g. you@yourcompany.com
        DEMEX_GMAIL_APP_PASSWORD  16-char app password (no spaces)
  * Or from a JSON file passed via --credentials-file, which MUST live OUTSIDE
    the git repo and be chmod 600. The script refuses world-readable cred files
    on POSIX and never logs or stores the password.
  * The IMAP mailbox is opened READ-ONLY: this tool cannot delete, move, or
    modify your mail.

USAGE (do not run until credentials are configured -- see the README outline)
    python email_ingestor.py --since 2026-01-01
    python email_ingestor.py --since 2026-01-01 --folders INBOX "[Gmail]/All Mail"
    python email_ingestor.py --since 2026-01-01 --no-pdf-parse
    python email_ingestor.py --export-json demex_email_import.json
"""

from __future__ import annotations

import argparse
import email
import email.utils
import hashlib
import imaplib
import json
import logging
import os
import re
import sqlite3
import ssl
import sys
from datetime import datetime, date
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Optional PDF backend (graceful degradation -- see module docstring)          #
# --------------------------------------------------------------------------- #
_PDF_BACKEND = None
try:
    import pdfplumber  # type: ignore
    _PDF_BACKEND = "pdfplumber"
except Exception:  # pragma: no cover - environment dependent
    try:
        import pypdf  # type: ignore
        _PDF_BACKEND = "pypdf"
    except Exception:
        _PDF_BACKEND = None


# --------------------------------------------------------------------------- #
# Configuration                                                                #
# --------------------------------------------------------------------------- #
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

# Subject/body keywords that mark an email as bid-relevant.
KEYWORDS = [
    "Invitation to Bid",
    "ITB",
    "Contract Award",
    "Change Order",
    "Proposal",
]

DEFAULT_FOLDERS = ["INBOX", "[Gmail]/All Mail"]

# Keyword -> coarse pipeline status, so win/loss can be inferred from intent.
KEYWORD_STATUS = {
    "invitation to bid": "invited",
    "itb": "invited",
    "proposal": "estimating",
    "contract award": "won",
    "change order": "won",
}

# Scope classification vocabularies (lowercased substring match).
CONTAINMENT_TERMS = [
    "asbestos", "abate", "abatement", "mold", "microbial", "lead-based",
    "lead based", "hazmat", "hazardous", "remediation", "encapsul",
    "negative air", "hepa", "friable", "acm", "racm", "containment",
]
DEMOLITION_TERMS = [
    "demo", "demolition", "selective", "removal", "gut", "interior strip",
    "sawcut", "saw-cut", "slab", "masonry", "concrete", "mep", "ceiling",
    "partition", "millwork", "casework", "dismantle", "disassemble", "wreck",
]

# Units we recognise in a line item.
UNIT_TOKENS = ["SF", "LF", "SY", "CY", "EA", "TON", "TONS", "HR", "HRS", "LS", "SQ", "CF"]

# --------------------------------------------------------------------------- #
# Regex patterns                                                               #
# --------------------------------------------------------------------------- #
_UNIT_RE = r"(?:" + "|".join(UNIT_TOKENS) + r")"
RE_QTY = re.compile(r"(?P<qty>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>" + _UNIT_RE + r")\b", re.IGNORECASE)
RE_PRICE = re.compile(r"\$\s*(?P<price>\d[\d,]*(?:\.\d+)?)")
# "over 1,000 SF", ">= 500 LF", "for quantities exceeding 2000 SF"
RE_BULK = re.compile(
    r"(?:over|exceed(?:ing|s)?|more than|>=?|greater than|above)\s+"
    r"(?P<min>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>" + _UNIT_RE + r")",
    re.IGNORECASE,
)

# Date phrases like "Bids Due", "Proposal Due", "Due Date", "RFP Due".
RE_DUE = re.compile(
    r"(?:bids?\s+due|proposals?\s+due|due\s+date|rfp\s+due|response\s+due|"
    r"submission\s+deadline|due\s+by)\s*[:\-]?\s*(?P<date>[^\n\r;]{4,40})",
    re.IGNORECASE,
)

# Date formats we attempt, most specific first.
DATE_FORMATS = [
    "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y",
    "%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y",
    "%m/%d/%y",
]

log = logging.getLogger("demex.ingest")


# --------------------------------------------------------------------------- #
# Credentials                                                                  #
# --------------------------------------------------------------------------- #
class Credentials:
    def __init__(self, user: str, app_password: str):
        self.user = user
        self.app_password = app_password


def load_credentials(creds_file: Optional[str]) -> Credentials:
    """Resolve credentials from a JSON file (if given) or environment vars.

    Never logs the password; refuses world/group-readable cred files on POSIX.
    """
    if creds_file:
        path = Path(creds_file).expanduser()
        if not path.is_file():
            raise SystemExit(f"Credentials file not found: {path}")
        # Refuse loose permissions on POSIX (Windows ACLs are out of scope).
        if os.name == "posix":
            mode = path.stat().st_mode & 0o077
            if mode:
                raise SystemExit(
                    f"Refusing to read {path}: file is group/world accessible. "
                    f"Run: chmod 600 {path}"
                )
        data = json.loads(path.read_text(encoding="utf-8"))
        user = data.get("user")
        pw = data.get("app_password")
    else:
        user = os.environ.get("DEMEX_GMAIL_USER")
        pw = os.environ.get("DEMEX_GMAIL_APP_PASSWORD")

    if not user or not pw:
        raise SystemExit(
            "Missing credentials. Set DEMEX_GMAIL_USER and "
            "DEMEX_GMAIL_APP_PASSWORD environment variables, or pass "
            "--credentials-file pointing at a chmod-600 JSON with "
            '{"user": "...", "app_password": "..."}.'
        )
    # App passwords are commonly pasted with spaces; Google ignores them.
    pw = pw.replace(" ", "")
    return Credentials(user, pw)


# --------------------------------------------------------------------------- #
# IMAP                                                                         #
# --------------------------------------------------------------------------- #
def imap_connect(creds: Credentials) -> imaplib.IMAP4_SSL:
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ctx)
    conn.login(creds.user, creds.app_password)
    log.info("Connected to %s as %s (read-only)", IMAP_HOST, creds.user)
    return conn


def list_all_folders(conn: imaplib.IMAP4_SSL) -> list[str]:
    """Enumerate every selectable mailbox/label on the server.

    Returns folder names with the [Gmail]/All Mail label hoisted to the front
    (so the most complete archive is scanned first). \\Noselect containers like
    the bare "[Gmail]" parent are skipped because they can't be opened.
    """
    typ, data = conn.list()
    if typ != "OK" or not data:
        log.warning("LIST failed; falling back to %s", DEFAULT_FOLDERS)
        return list(DEFAULT_FOLDERS)

    folders: list[str] = []
    for raw in data:
        line = raw.decode() if isinstance(raw, bytes) else str(raw)
        # Format: (\HasNoChildren \Trash) "/" "[Gmail]/Trash"
        if "\\Noselect" in line:
            continue
        # The mailbox name is the last quoted token (or last whitespace token).
        if line.endswith('"'):
            name = line[line.rindex('"', 0, len(line) - 1) + 1:-1]
        else:
            name = line.rsplit(" ", 1)[-1]
        if name:
            folders.append(name)

    all_mail = "[Gmail]/All Mail"
    if all_mail in folders:
        folders.remove(all_mail)
        folders.insert(0, all_mail)
    log.info("Discovered %d selectable folder(s): %s", len(folders), ", ".join(folders))
    return folders


def _imap_date(d: date) -> str:
    return d.strftime("%d-%b-%Y")


def search_folder(conn: imaplib.IMAP4_SSL, folder: str, since: Optional[date]) -> list[bytes]:
    """Select a folder READ-ONLY and return matching message UIDs.

    Prefers Gmail's X-GM-RAW (one query for all keywords); falls back to a
    union of standard IMAP TEXT searches for non-Gmail servers.
    """
    typ, _ = conn.select(f'"{folder}"', readonly=True)
    if typ != "OK":
        log.warning("Cannot open folder %s -- skipping", folder)
        return []

    # --- Gmail-native search ---------------------------------------------- #
    quoted = " OR ".join(f'"{k}"' for k in KEYWORDS)
    gm_query = f"({quoted})"
    if since:
        gm_query += f" after:{since.strftime('%Y/%m/%d')}"
    try:
        typ, data = conn.uid("SEARCH", "X-GM-RAW", gm_query)
        if typ == "OK":
            uids = data[0].split() if data and data[0] else []
            log.info("  %s: %d match(es) via X-GM-RAW", folder, len(uids))
            return uids
    except imaplib.IMAP4.error:
        pass  # not Gmail, or server rejected the extension

    # --- Standard IMAP fallback (union of per-keyword TEXT searches) ------ #
    seen: dict[bytes, None] = {}
    for kw in KEYWORDS:
        crit = ["TEXT", f'"{kw}"']
        if since:
            crit = ["SINCE", _imap_date(since)] + crit
        typ, data = conn.uid("SEARCH", None, *crit)
        if typ == "OK" and data and data[0]:
            for uid in data[0].split():
                seen[uid] = None
    uids = list(seen.keys())
    log.info("  %s: %d match(es) via TEXT fallback", folder, len(uids))
    return uids


def fetch_message(conn: imaplib.IMAP4_SSL, uid: bytes) -> Optional[Message]:
    typ, data = conn.uid("FETCH", uid, "(RFC822)")
    if typ != "OK" or not data or not isinstance(data[0], tuple):
        return None
    return email.message_from_bytes(data[0][1])


# --------------------------------------------------------------------------- #
# Email parsing                                                                #
# --------------------------------------------------------------------------- #
def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def extract_body_and_attachments(msg: Message) -> tuple[str, list[tuple[str, bytes]]]:
    """Return (plain-text body, [(filename, pdf_bytes), ...])."""
    body_parts: list[str] = []
    attachments: list[tuple[str, bytes]] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = (part.get("Content-Disposition") or "").lower()
        filename = _decode(part.get_filename())

        is_pdf = ctype == "application/pdf" or filename.lower().endswith(".pdf")
        if is_pdf or "attachment" in disp:
            if is_pdf:
                payload = part.get_payload(decode=True) or b""
                if payload:
                    attachments.append((filename or "attachment.pdf", payload))
            continue

        if ctype == "text/plain":
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            body_parts.append(payload.decode(charset, errors="replace"))
        elif ctype == "text/html" and not body_parts:
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            html = payload.decode(charset, errors="replace")
            body_parts.append(_strip_html(html))

    return ("\n".join(body_parts).strip(), attachments)


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    return re.sub(r"[ \t]+", " ", html).strip()


# --------------------------------------------------------------------------- #
# Heuristic metadata extraction                                               #
# --------------------------------------------------------------------------- #
def parse_date_string(raw: str) -> Optional[str]:
    """Best-effort parse of a free-text date -> ISO 'YYYY-MM-DD' or None."""
    raw = raw.strip().strip(".,;:")
    # Pull out the most date-looking token cluster.
    m = re.search(
        r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
        r"\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}",
        raw,
    )
    candidate = m.group(0) if m else raw
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def extract_due_date(text: str) -> Optional[str]:
    for m in RE_DUE.finditer(text):
        iso = parse_date_string(m.group("date"))
        if iso:
            return iso
    return None


def extract_client_name(msg: Message, body: str) -> str:
    """Heuristic GC/client identity: display name, else sender domain."""
    frm = _decode(msg.get("From"))
    name, addr = email.utils.parseaddr(frm)
    if name and not name.lower().startswith(("no-reply", "noreply", "donotreply")):
        return name.strip()
    if addr and "@" in addr:
        domain = addr.split("@", 1)[1]
        core = domain.split(".")[0]
        return core.replace("-", " ").title()
    return "Unknown Client"


def extract_scope_summary(text: str, max_len: int = 280) -> str:
    """First passage that mentions demo/abatement scope language."""
    candidates = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    terms = CONTAINMENT_TERMS + DEMOLITION_TERMS + ["scope", "square", "demolish"]
    for chunk in candidates:
        low = chunk.lower()
        if any(t in low for t in terms) and len(chunk.strip()) > 20:
            return re.sub(r"\s+", " ", chunk).strip()[:max_len]
    # Fall back to the first non-trivial line.
    for chunk in candidates:
        if len(chunk.strip()) > 20:
            return re.sub(r"\s+", " ", chunk).strip()[:max_len]
    return ""


def extract_project_name(msg: Message, body: str) -> str:
    subj = _decode(msg.get("Subject")).strip()
    # Strip common bid-noise prefixes for a cleaner project name.
    for kw in KEYWORDS:
        subj = re.sub(rf"(?i)\b{re.escape(kw)}\b[:\-\s]*", "", subj)
    subj = re.sub(r"(?i)^(re|fwd?)\s*:\s*", "", subj).strip(" -:")
    return subj or "Untitled Project"


def classify_scope(text: str) -> str:
    """Return 'containment', 'demolition', or 'both' for a text blob."""
    low = text.lower()
    has_c = any(t in low for t in CONTAINMENT_TERMS)
    has_d = any(t in low for t in DEMOLITION_TERMS)
    if has_c and has_d:
        return "both"
    if has_c:
        return "containment"
    return "demolition"


# --------------------------------------------------------------------------- #
# PDF text + heuristic line-item parsing                                       #
# --------------------------------------------------------------------------- #
def extract_pdf_text(pdf_path: Path) -> str:
    if _PDF_BACKEND == "pdfplumber":
        try:
            import pdfplumber  # type: ignore
            with pdfplumber.open(str(pdf_path)) as pdf:
                return "\n".join((page.extract_text() or "") for page in pdf.pages)
        except Exception as e:  # pragma: no cover
            log.warning("pdfplumber failed on %s: %s", pdf_path.name, e)
            return ""
    if _PDF_BACKEND == "pypdf":
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(str(pdf_path))
            return "\n".join((pg.extract_text() or "") for pg in reader.pages)
        except Exception as e:  # pragma: no cover
            log.warning("pypdf failed on %s: %s", pdf_path.name, e)
            return ""
    return ""


def _to_float(num: str) -> float:
    return float(num.replace(",", ""))


def parse_line_items(text: str) -> dict:
    """Rule-based extraction of draft line items from PDF text.

    Returns a dict with three lists shaped to the DEMEX v4 columns:
        estimated_quantities  : [{item, scope_type, qty, unit, ...}]
        unit_rates            : [{item, scope_type, unit, rate}]
        bulk_pricing_thresholds: [{item, min_qty, unit, rate}]
    Every row is heuristic and should be reviewed by an estimator.
    """
    est_qty: list[dict] = []
    unit_rates: list[dict] = []
    bulk: list[dict] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if len(line) < 6:
            continue

        qm = RE_QTY.search(line)
        pm = RE_PRICE.search(line)
        if not (qm or pm):
            continue

        # Description = the line with numeric/price tokens stripped out.
        desc = RE_PRICE.sub("", RE_QTY.sub("", line)).strip(" .:-\t|")
        desc = re.sub(r"\s{2,}", " ", desc)[:120]
        if not desc:
            desc = line[:120]
        scope = classify_scope(line)

        if qm:
            est_qty.append({
                "item": desc,
                "scope_type": scope,
                "qty": _to_float(qm.group("qty")),
                "unit": qm.group("unit").upper(),
                "location": "",
                "notes": "auto-extracted (review)",
            })
        if pm and qm:
            unit_rates.append({
                "item": desc,
                "scope_type": scope,
                "unit": qm.group("unit").upper(),
                "rate": _to_float(pm.group("price")),
                "source": "pdf-heuristic",
            })

        bm = RE_BULK.search(line)
        if bm and pm:
            bulk.append({
                "item": desc,
                "scope_type": scope,
                "min_qty": _to_float(bm.group("min")),
                "unit": bm.group("unit").upper(),
                "rate": _to_float(pm.group("price")),
                "note": "auto-extracted bulk threshold (review)",
            })

    return {
        "estimated_quantities": est_qty,
        "unit_rates": unit_rates,
        "bulk_pricing_thresholds": bulk,
    }


# --------------------------------------------------------------------------- #
# SQLite (schema mirrors DEMEX v4 estimating columns)                          #
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id              TEXT PRIMARY KEY,
    name                    TEXT,
    client_name             TEXT,
    address                 TEXT,
    scope_type              TEXT,              -- demolition | containment | both
    due_date                TEXT,              -- ISO yyyy-mm-dd
    scope_summary           TEXT,
    status                  TEXT,
    win_loss_status         TEXT,              -- won | lost | pending
    estimated_quantities    TEXT DEFAULT '[]', -- JSON
    unit_rates              TEXT DEFAULT '[]', -- JSON
    bulk_pricing_thresholds TEXT DEFAULT '[]', -- JSON
    actual_realized_costs   TEXT DEFAULT '{}', -- JSON
    source_message_id       TEXT,
    needs_review            INTEGER DEFAULT 1,
    created_at              TEXT,
    updated_at              TEXT
);
CREATE TABLE IF NOT EXISTS emails (
    message_id      TEXT PRIMARY KEY,
    project_id      TEXT,
    folder          TEXT,
    subject         TEXT,
    sender          TEXT,
    sent_at         TEXT,
    matched_keyword TEXT,
    body            TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
CREATE TABLE IF NOT EXISTS line_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   TEXT,
    kind         TEXT,    -- quantity | unit_rate | bulk_threshold
    description  TEXT,
    scope_type   TEXT,
    qty          REAL,
    unit         TEXT,
    unit_price   REAL,
    min_qty      REAL,
    source_pdf   TEXT,
    needs_review INTEGER DEFAULT 1,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
CREATE TABLE IF NOT EXISTS attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT,
    message_id  TEXT,
    filename    TEXT,
    path        TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
"""


def db_init(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def project_id_for(message_id: str, subject: str) -> str:
    basis = (message_id or subject or "").encode("utf-8")
    return "pj_" + hashlib.sha1(basis).hexdigest()[:12]


def upsert_project(conn: sqlite3.Connection, row: dict, line_data: dict) -> None:
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    # Merge any PDF-derived line data into the v4 JSON columns.
    est_qty = json.dumps(line_data.get("estimated_quantities", []))
    rates = json.dumps(line_data.get("unit_rates", []))
    bulk = json.dumps(line_data.get("bulk_pricing_thresholds", []))
    actuals = json.dumps({
        "labor": 0, "disposal": 0, "equipment": 0, "material": 0,
        "sub": 0, "total": 0, "recordedAt": None, "source": "",
    })
    conn.execute(
        """
        INSERT INTO projects (
            project_id, name, client_name, address, scope_type, due_date,
            scope_summary, status, win_loss_status, estimated_quantities,
            unit_rates, bulk_pricing_thresholds, actual_realized_costs,
            source_message_id, needs_review, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_id) DO UPDATE SET
            scope_type=excluded.scope_type,
            due_date=COALESCE(excluded.due_date, projects.due_date),
            scope_summary=excluded.scope_summary,
            status=excluded.status,
            win_loss_status=excluded.win_loss_status,
            estimated_quantities=excluded.estimated_quantities,
            unit_rates=excluded.unit_rates,
            bulk_pricing_thresholds=excluded.bulk_pricing_thresholds,
            updated_at=excluded.updated_at
        """,
        (
            row["project_id"], row["name"], row["client_name"], row["address"],
            row["scope_type"], row["due_date"], row["scope_summary"],
            row["status"], row["win_loss_status"], est_qty, rates, bulk,
            actuals, row["source_message_id"], 1, now, now,
        ),
    )


def insert_email(conn: sqlite3.Connection, message_id: str, project_id: str,
                 folder: str, subject: str, sender: str, sent_at: str,
                 keyword: str, body: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO emails
           (message_id, project_id, folder, subject, sender, sent_at,
            matched_keyword, body)
           VALUES (?,?,?,?,?,?,?,?)""",
        (message_id, project_id, folder, subject, sender, sent_at, keyword, body),
    )


def insert_line_items(conn: sqlite3.Connection, project_id: str,
                      line_data: dict, source_pdf: str) -> None:
    for q in line_data.get("estimated_quantities", []):
        conn.execute(
            """INSERT INTO line_items
               (project_id, kind, description, scope_type, qty, unit, source_pdf)
               VALUES (?,?,?,?,?,?,?)""",
            (project_id, "quantity", q["item"], q["scope_type"], q["qty"],
             q["unit"], source_pdf),
        )
    for r in line_data.get("unit_rates", []):
        conn.execute(
            """INSERT INTO line_items
               (project_id, kind, description, scope_type, unit, unit_price, source_pdf)
               VALUES (?,?,?,?,?,?,?)""",
            (project_id, "unit_rate", r["item"], r["scope_type"], r["unit"],
             r["rate"], source_pdf),
        )
    for b in line_data.get("bulk_pricing_thresholds", []):
        conn.execute(
            """INSERT INTO line_items
               (project_id, kind, description, scope_type, unit, unit_price,
                min_qty, source_pdf)
               VALUES (?,?,?,?,?,?,?,?)""",
            (project_id, "bulk_threshold", b["item"], b["scope_type"], b["unit"],
             b["rate"], b["min_qty"], source_pdf),
        )


def insert_attachment(conn: sqlite3.Connection, project_id: str,
                      message_id: str, filename: str, path: str) -> None:
    conn.execute(
        """INSERT INTO attachments (project_id, message_id, filename, path)
           VALUES (?,?,?,?)""",
        (project_id, message_id, filename, path),
    )


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def slugify(value: str, maxlen: int = 60) -> str:
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    value = re.sub(r"[\s_-]+", "-", value)
    return (value or "project")[:maxlen].strip("-")


def matched_keyword(text: str) -> str:
    low = text.lower()
    for kw in KEYWORDS:
        if kw.lower() in low:
            return kw
    return ""


def infer_status_and_winloss(keyword: str) -> tuple[str, str]:
    status = KEYWORD_STATUS.get(keyword.lower(), "invited")
    win_loss = "won" if status == "won" else "pending"
    return status, win_loss


# --------------------------------------------------------------------------- #
# Core pipeline                                                                #
# --------------------------------------------------------------------------- #
def run(args: argparse.Namespace) -> int:
    creds = load_credentials(args.credentials_file)
    since = None
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").date()

    attach_root = Path(args.attachments_dir)
    attach_root.mkdir(parents=True, exist_ok=True)

    conn = db_init(args.db)
    if _PDF_BACKEND is None and not args.no_pdf_parse:
        log.warning(
            "No PDF backend installed (pdfplumber/pypdf). PDFs will be saved "
            "but NOT parsed. Install pdfplumber to enable line-item extraction."
        )

    imap = imap_connect(creds)

    # Resolve which folders to scan. An explicit --folders list wins; otherwise
    # we enumerate EVERY selectable mailbox (which includes [Gmail]/All Mail).
    # All Mail overlaps the per-label folders, so the same message can appear in
    # several folders -- that is fine: the DB upserts on message_id, so repeats
    # are deduplicated rather than duplicated.
    if args.folders:
        folders = args.folders
    else:
        folders = list_all_folders(imap)

    processed = 0
    seen_messages: set[str] = set()
    try:
        for folder in folders:
            if args.limit and processed >= args.limit:
                break
            uids = search_folder(imap, folder, since)
            for uid in uids:
                if args.limit and processed >= args.limit:
                    break
                msg = fetch_message(imap, uid)
                if msg is None:
                    continue

                message_id = _decode(msg.get("Message-ID")) or f"{folder}:{uid.decode()}"
                if message_id in seen_messages:
                    continue  # already handled in an earlier (overlapping) folder
                seen_messages.add(message_id)
                subject = _decode(msg.get("Subject"))
                sender = _decode(msg.get("From"))
                sent_at = ""
                try:
                    dt = email.utils.parsedate_to_datetime(msg.get("Date"))
                    sent_at = dt.isoformat() if dt else ""
                except Exception:
                    pass

                body, attachments = extract_body_and_attachments(msg)
                kw = matched_keyword(subject + "\n" + body)
                status, win_loss = infer_status_and_winloss(kw)

                project_name = extract_project_name(msg, body)
                pid = project_id_for(message_id, subject)

                scope_blob = subject + "\n" + body
                row = {
                    "project_id": pid,
                    "name": project_name,
                    "client_name": extract_client_name(msg, body),
                    "address": "",
                    "scope_type": classify_scope(scope_blob),
                    "due_date": extract_due_date(body),
                    "scope_summary": extract_scope_summary(body),
                    "status": status,
                    "win_loss_status": win_loss,
                    "source_message_id": message_id,
                }

                # Save PDFs into a per-project folder and parse them.
                proj_dir = attach_root / f"{slugify(project_name)}-{pid[-6:]}"
                merged_lines = {"estimated_quantities": [], "unit_rates": [],
                                "bulk_pricing_thresholds": []}
                if attachments:
                    proj_dir.mkdir(parents=True, exist_ok=True)
                for fname, payload in attachments:
                    safe = slugify(Path(fname).stem) + ".pdf"
                    dest = proj_dir / safe
                    dest.write_bytes(payload)
                    insert_attachment(conn, pid, message_id, fname, str(dest))
                    log.info("    saved attachment %s", dest)
                    if not args.no_pdf_parse and _PDF_BACKEND:
                        pdf_text = extract_pdf_text(dest)
                        if pdf_text:
                            parsed = parse_line_items(pdf_text)
                            for k in merged_lines:
                                merged_lines[k].extend(parsed[k])
                            insert_line_items(conn, pid, parsed, str(dest))
                            # Refine scope from PDF content if richer than email.
                            row["scope_type"] = classify_scope(pdf_text) \
                                if pdf_text.strip() else row["scope_type"]

                upsert_project(conn, row, merged_lines)
                insert_email(conn, message_id, pid, folder, subject, sender,
                             sent_at, kw, body)
                conn.commit()
                processed += 1
                log.info("  [%s] %s -> %s/%s", kw or "match", project_name,
                         row["scope_type"], row["win_loss_status"])
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    log.info("Done. Processed %d email(s) into %s", processed, args.db)

    if args.export_json:
        export_demex_json(args.db, args.export_json)
        log.info("Exported DEMEX import file -> %s", args.export_json)
    return 0


# --------------------------------------------------------------------------- #
# Optional: export to DEMEX (DEMEX.html) import JSON format                     #
# --------------------------------------------------------------------------- #
def export_demex_json(db_path: str, out_path: str) -> None:
    """Emit a {clients, projects, v:4} file consumable by DEMEX.html import."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM projects").fetchall()

    clients: dict[str, dict] = {}
    projects = []
    for r in rows:
        cname = r["client_name"] or "Unknown Client"
        if cname not in clients:
            cid = "cl_" + hashlib.sha1(cname.encode()).hexdigest()[:10]
            clients[cname] = {
                "id": cid, "name": cname, "type": "General Contractor",
                "contactName": "", "email": "", "phone": "", "address": "",
                "notes": "Imported by email_ingestor.py",
                "createdAt": (r["created_at"] or "")[:10],
            }
        projects.append({
            "id": r["project_id"],
            "name": r["name"],
            "client_id": clients[cname]["id"],
            "address": r["address"] or "",
            "type": "Open Shop",
            "scope": r["scope_summary"] or "Demolition",
            "scope_type": r["scope_type"],
            "status": r["status"] or "invited",
            "win_loss_status": r["win_loss_status"] or "pending",
            "bidNo": "", "bidAmount": 0, "alternates": [],
            "submittedAt": None, "dueAt": r["due_date"], "awardedAt": None,
            "source": "Email Ingestor", "notes": "", "sqft": 0,
            "estCosts": {"labor": 0, "disposal": 0, "equipment": 0,
                         "material": 0, "sub": 0},
            "estLaborHrs": 0,
            "estQty": json.loads(r["estimated_quantities"] or "[]"),
            "estimated_quantities": json.loads(r["estimated_quantities"] or "[]"),
            "unit_rates": json.loads(r["unit_rates"] or "[]"),
            "bulk_pricing_thresholds": json.loads(r["bulk_pricing_thresholds"] or "[]"),
            "actual_realized_costs": json.loads(r["actual_realized_costs"] or "{}"),
            "completed": False,
            "createdAt": r["created_at"] or "",
        })
    payload = {"v": 4, "clients": list(clients.values()), "projects": projects}
    Path(out_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    conn.close()


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Ingest DEMEX bid emails (read-only) into demex_records.db.")
    p.add_argument("--since", help="Only scan email on/after this ISO date "
                                   "(YYYY-MM-DD). Strongly recommended.")
    p.add_argument("--folders", nargs="+", default=None,
                   help="IMAP folders/labels to scan. Omit to scan ALL "
                        "selectable folders (including [Gmail]/All Mail), "
                        "which is the default.")
    p.add_argument("--db", default="demex_records.db",
                   help="SQLite database path (created if absent).")
    p.add_argument("--attachments-dir", default="projects",
                   help="Root folder for downloaded PDFs (per-project subdirs).")
    p.add_argument("--credentials-file",
                   help="JSON cred file OUTSIDE the repo (chmod 600). "
                        "Falls back to DEMEX_GMAIL_USER / "
                        "DEMEX_GMAIL_APP_PASSWORD env vars.")
    p.add_argument("--no-pdf-parse", action="store_true",
                   help="Download PDFs but skip line-item extraction.")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after N emails (0 = no limit). Useful for a "
                        "first dry test.")
    p.add_argument("--export-json", metavar="PATH",
                   help="After ingest, write a DEMEX.html-importable JSON file.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
