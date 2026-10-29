"""Server-side inbox sweep: reads company mailboxes over IMAP (read-only) on a timer and feeds the apps.

  ddd (Disassemble Dynamics, Gmail/Workspace) -> bid invitations become demex_itb_import_<date>_server.json
      drops in DEMEX_IMPORTS_DIR, the same {clients, projects} shape the daily sweep writes, so DEMEX's
      existing /api/itb-imports feed adds them. Jobs DEMEX already has (same name or address) are skipped.
  wcp (Wayne's Carpet Plus, Yahoo)          -> PAVORA state (app_state "pavora:wcp"): mail from a known
      customer is logged on that customer; a new flooring inquiry becomes a customer + New Lead project;
      every swept message is listed in state.inbox.

Accounts come from the DEMEX_MAIL_ACCOUNTS secret, a JSON list:
  [{"org": "ddd", "user": "shawn@disassembledynamics.com", "app_password": "..."},
   {"org": "wcp", "user": "someone@yahoo.com", "app_password": "...", "host": "imap.mail.yahoo.com"}]
App passwords only (never the normal password). Nothing is ever logged that contains one.
Classification uses demex_bridge.call_ai when ANTHROPIC_API_KEY is set; otherwise a keyword fallback.
"""
from __future__ import annotations

import email
import hashlib
import imaplib
import json
import os
import re
import sqlite3
import ssl
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html import unescape
from pathlib import Path

ITB_QUERY = ('("invitation to bid" OR "invite to bid" OR "bid invitation" OR ITB OR "request for proposal" '
             'OR "request to bid" OR "bids due" OR demolition OR "bid due")')
ITB_WORDS = re.compile(r"invitation to bid|invite to bid|bid invitation|\bITB\b|request for proposal|\bRFP\b|"
                       r"request to bid|bids? due|demolition|addendum", re.I)
FLOOR_WORDS = re.compile(r"floor|carpet|tile|vinyl|lvp|laminate|hardwood|quote|estimate|install|measure", re.I)
MAX_BODY = 12000


def log(msg: str) -> None:
    print("[mail] " + msg, file=sys.stderr, flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def uid(prefix: str, basis: str) -> str:
    return prefix + "_" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def accounts() -> list[dict]:
    raw = (os.environ.get("DEMEX_MAIL_ACCOUNTS") or "").strip()
    if not raw:
        return []
    try:
        out = []
        for a in json.loads(raw):
            if isinstance(a, dict) and a.get("user") and a.get("app_password") and a.get("org") in ("ddd", "wcp"):
                a = dict(a); a["app_password"] = str(a["app_password"]).replace(" ", "")
                a.setdefault("host", "imap.mail.yahoo.com" if "yahoo" in a["user"].lower() or a["org"] == "wcp" else "imap.gmail.com")
                out.append(a)
        return out
    except Exception as e:
        log("DEMEX_MAIL_ACCOUNTS is not valid JSON (%s); mail sweep off" % type(e).__name__)
        return []


# ---------------------------------------------------------------- IMAP
def fetch_new(acct: dict, conn_db: sqlite3.Connection, lookback_days: int) -> list[dict]:
    """New messages (not yet in mail_seen) from INBOX since lookback, read-only, oldest first."""
    ctx = ssl.create_default_context()
    m = imaplib.IMAP4_SSL(acct["host"], 993, ssl_context=ctx)
    try:
        m.login(acct["user"], acct["app_password"])
        m.select("INBOX", readonly=True)
        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
        uids = []
        if "gmail" in acct["host"] and acct["org"] == "ddd":
            try:
                typ, data = m.uid("SEARCH", "X-GM-RAW", '"%s newer_than:%dd"' % (ITB_QUERY.replace('"', '\\"'), lookback_days))
                if typ == "OK":
                    uids = data[0].split()
            except imaplib.IMAP4.error:
                uids = []
        if not uids:
            typ, data = m.uid("SEARCH", None, "SINCE", since)
            uids = data[0].split() if typ == "OK" else []
        out = []
        for u in uids[-200:]:
            typ, data = m.uid("FETCH", u, "(BODY.PEEK[])")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                continue
            msg = email.message_from_bytes(data[0][1], policy=policy.default)
            mid = (msg.get("Message-ID") or "").strip() or uid("m", acct["user"] + u.decode())
            if conn_db.execute("SELECT 1 FROM mail_seen WHERE account=? AND message_id=?", (acct["user"], mid)).fetchone():
                continue
            out.append(parse(msg, mid))
        return out
    finally:
        try:
            m.logout()
        except Exception:
            pass


def parse(msg, mid: str) -> dict:
    body = ""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is not None:
            body = part.get_content()
            if part.get_content_type() == "text/html":
                body = unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", body)))
    except Exception:
        body = ""
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n\n", body).strip()[:MAX_BODY]
    name, addr = parseaddr(msg.get("From") or "")
    try:
        sent = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        sent = now_iso()
    atts = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    return {"message_id": mid, "subject": (msg.get("Subject") or "").strip(), "from_name": name, "from_email": addr.lower(),
            "to": ", ".join(a for _, a in getaddresses(msg.get_all("To", []))), "date": sent, "body": body, "attachments": atts}


# ---------------------------------------------------------------- AI
def ask_json(system: str, prompt: str) -> dict | None:
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return None
    try:
        import demex_bridge
        text, _ = demex_bridge.call_ai(prompt, max_tokens=900, system=system)
        mt = re.search(r"\{.*\}", text, re.S)
        return json.loads(mt.group(0)) if mt else None
    except Exception as e:
        log("AI classify failed (%s); using keyword fallback" % type(e).__name__)
        return None


def email_text(m: dict) -> str:
    return ("From: %s <%s>\nTo: %s\nDate: %s\nSubject: %s\nAttachments: %s\n\n%s"
            % (m["from_name"], m["from_email"], m["to"], m["date"], m["subject"], ", ".join(m["attachments"]) or "none", m["body"]))


DDD_SYSTEM = ("You triage email for Disassemble Dynamics, a commercial demolition and abatement subcontractor in Florida. "
              "Reply with one JSON object only, no prose.")
DDD_PROMPT = """Is this email an invitation to bid, RFP, bid-date change or addendum for a construction project the company could bid demolition on?
Return JSON: {"itb": true|false,
 "project": {"name": "<Client/Store - Location (Scope)>", "address": "", "scope": "<2-4 sentence demolition scope summary>",
             "dueAt": "YYYY-MM-DD or null", "source": "<BuildingConnected|PlanHub|Procore|Bonfire|Email|...>"},
 "client": {"name": "<general contractor or owner>", "contactName": "", "email": "", "phone": "", "address": ""}}

EMAIL:
"""

WCP_SYSTEM = ("You triage email for Wayne's Carpet Plus, a flooring store and installer in Lake City, Florida. "
              "Reply with one JSON object only, no prose.")
WCP_PROMPT = """Classify this email. kind = "inquiry" if a person or business is asking about flooring work, a quote, an estimate,
a measurement or an install; "customer" if it is ongoing correspondence with a customer; otherwise "other" (vendors, ads, newsletters,
receipts, spam). Return JSON: {"kind": "inquiry"|"customer"|"other", "summary": "<one sentence>",
 "customer": {"name": "", "phone": "", "address": "", "type": "residential"|"commercial"},
 "project": {"name": "<short job name, e.g. 'Living room LVP'>", "notes": "<rooms, materials, sizes, timing mentioned>"}}

EMAIL:
"""


# ---------------------------------------------------------------- state helpers
def read_state(db: sqlite3.Connection, key: str):
    row = db.execute("SELECT data, version FROM app_state WHERE app=?", (key,)).fetchone()
    return (json.loads(row[0]) if row and row[0] else None), (row[1] if row else 0)


def write_state(db: sqlite3.Connection, key: str, data: dict, version: int) -> bool:
    """Optimistic write: only succeeds if nobody saved since we read (the apps reload on the bumped version)."""
    now = datetime.now(timezone.utc).isoformat()
    if version:
        cur = db.execute("UPDATE app_state SET data=?, version=version+1, updated_at=? WHERE app=? AND version=?",
                         (json.dumps(data), now, key, version))
    else:
        cur = db.execute("INSERT OR IGNORE INTO app_state(app,data,version,updated_at) VALUES(?,?,1,?)", (key, json.dumps(data), now))
    db.commit()
    return cur.rowcount == 1


# ---------------------------------------------------------------- ddd -> DEMEX ITB drop
def sweep_ddd(db: sqlite3.Connection, msgs: list[dict], imports_dir: Path) -> int:
    demex, _ = read_state(db, "demex")
    known_names, known_addr, clients_by_name = set(), set(), {}
    for p in (demex or {}).get("projects") or []:
        known_names.add(norm(p.get("name"))); a = norm(p.get("address"))
        if len(a) > 8: known_addr.add(a)
    for c in (demex or {}).get("clients") or []:
        clients_by_name[norm(c.get("name"))] = c.get("id")
    for f in imports_dir.glob("demex_itb_import_*.json"):
        try:
            for p in json.loads(f.read_text(encoding="utf-8")).get("projects") or []:
                known_names.add(norm(p.get("name")))
        except Exception:
            pass
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = imports_dir / ("demex_itb_import_%s_server.json" % today)
    drop = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"clients": [], "projects": []}
    added = 0
    for m in msgs:
        info = ask_json(DDD_SYSTEM, DDD_PROMPT + email_text(m))
        if info is None:   # no AI: keyword rule, subject as the job name
            if not ITB_WORDS.search(m["subject"] + " " + m["body"][:2000]):
                continue
            info = {"itb": True, "project": {"name": m["subject"], "scope": m["body"][:600], "source": "Email"},
                    "client": {"name": m["from_name"] or m["from_email"].split("@")[-1], "email": m["from_email"]}}
        if not info.get("itb"):
            continue
        p, c = info.get("project") or {}, info.get("client") or {}
        name = (p.get("name") or m["subject"]).strip()
        if norm(name) in known_names or (len(norm(p.get("address"))) > 8 and norm(p.get("address")) in known_addr):
            continue
        cid = clients_by_name.get(norm(c.get("name")))
        if not cid:
            cid = uid("cl_itb_srv", norm(c.get("name")) or m["from_email"])
            if not any(x["id"] == cid for x in drop["clients"]):
                drop["clients"].append({"id": cid, "name": c.get("name") or m["from_email"], "type": "GC",
                                        "contactName": c.get("contactName") or m["from_name"], "email": c.get("email") or m["from_email"],
                                        "phone": c.get("phone") or "", "address": c.get("address") or "",
                                        "notes": "Added by the server inbox sweep", "createdAt": now_iso()})
            clients_by_name[norm(c.get("name"))] = cid
        due = p.get("dueAt") if re.match(r"^\d{4}-\d{2}-\d{2}$", str(p.get("dueAt") or "")) else None
        drop["projects"].append({
            "id": uid("pj_itb_srv", m["message_id"]), "name": name, "client_id": cid, "address": p.get("address") or "",
            "type": "Open Shop", "scope": p.get("scope") or "", "status": "invited", "bidNo": "", "bidAmount": 0,
            "alternates": [], "submittedAt": None, "dueAt": due, "awardedAt": None, "probability": 0,
            "source": p.get("source") or "Email", "sqft": 0,
            "estCosts": {"labor": 0, "disposal": 0, "equipment": 0, "material": 0, "sub": 0}, "estLaborHrs": 0, "estQty": [],
            "completed": False, "createdAt": m["date"],
            "notes": "From email: %s <%s> - %s" % (m["from_name"], m["from_email"], m["subject"])})
        known_names.add(norm(name)); added += 1
    if added:
        tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(drop, indent=1), encoding="utf-8"); tmp.replace(path)
    return added


# ---------------------------------------------------------------- wcp -> PAVORA
def pv_id(prefix: str) -> str:
    return "%s_%s%s" % (prefix, format(int(time.time() * 1000), "x"), os.urandom(3).hex())


def sweep_wcp(db: sqlite3.Connection, msgs: list[dict], account: str) -> int:
    if not msgs:
        return 0
    results = []
    for m in msgs:   # classify outside the write loop so a version conflict never repeats AI calls
        info = ask_json(WCP_SYSTEM, WCP_PROMPT + email_text(m))
        if info is None:
            info = {"kind": "inquiry" if FLOOR_WORDS.search(m["subject"] + " " + m["body"][:1500]) else "other",
                    "summary": m["subject"], "customer": {"name": m["from_name"]}, "project": {"name": m["subject"]}}
        results.append((m, info))
    for _ in range(5):
        state, version = read_state(db, "pavora:wcp")
        if state is None:
            state = {"companyId": "wcp", "company": {"name": "Wayne's Carpet Plus"}, "meta": {"createdAt": now_iso(), "updatedAt": now_iso(), "sample": False}}
        for k in ("customers", "projects", "activity", "inbox"):
            state.setdefault(k, [])
        by_email = {(c.get("email") or "").lower(): c for c in state["customers"] if c.get("email")}
        changed = 0
        for m, info in results:
            if any(x.get("messageId") == m["message_id"] for x in state["inbox"]):
                continue
            kind = info.get("kind") or "other"
            cust = by_email.get(m["from_email"])
            entry = {"id": pv_id("ib"), "messageId": m["message_id"], "ts": m["date"], "account": account, "from": m["from_name"],
                     "fromEmail": m["from_email"], "subject": m["subject"], "summary": info.get("summary") or m["subject"],
                     "kind": "customer" if cust else kind, "customerId": cust["id"] if cust else None, "projectId": None}
            if cust:
                cust.setdefault("comms", []).insert(0, {"id": pv_id("cm"), "ts": m["date"], "channel": "email",
                                                        "summary": "%s - %s" % (m["subject"], entry["summary"]), "userId": None})
            elif kind == "inquiry":
                ci, pi = info.get("customer") or {}, info.get("project") or {}
                prop = {"id": pv_id("pr"), "label": "Primary", "address": ci.get("address") or ""}
                cust = {"id": pv_id("cu"), "companyId": "wcp", "name": ci.get("name") or m["from_name"] or m["from_email"],
                        "type": ci.get("type") if ci.get("type") in ("residential", "commercial") else "residential",
                        "phone": ci.get("phone") or "", "email": m["from_email"], "billing": {"address": ci.get("address") or ""},
                        "properties": [prop], "repId": None, "notes": [], "followUps": [], "docs": [], "createdAt": now_iso(),
                        "comms": [{"id": pv_id("cm"), "ts": m["date"], "channel": "email", "summary": "%s - %s" % (m["subject"], entry["summary"]), "userId": None}]}
                proj = {"id": pv_id("pj"), "companyId": "wcp", "customerId": cust["id"], "propertyId": prop["id"],
                        "name": pi.get("name") or m["subject"] or "Email inquiry", "stageId": "new_lead", "repId": None, "estimateId": None,
                        "source": "email", "contract": {"originalEstimate": 0, "approvedValue": 0, "changeOrders": []}, "materials": [],
                        "ledger": {"invoices": [], "payments": [], "expenses": []},
                        "actuals": {"materialCost": 0, "laborCost": 0, "laborHours": 0, "quantities": {}},
                        "installDate": None, "crewId": None, "timeline": [{"ts": now_iso(), "stageId": "new_lead"}],
                        "notes": "Created from email - please review.\n" + (pi.get("notes") or ""), "createdAt": now_iso(), "closedAt": None}
                state["customers"].append(cust); state["projects"].append(proj); by_email[m["from_email"]] = cust
                entry.update(customerId=cust["id"], projectId=proj["id"])
                state["activity"].insert(0, {"ts": now_iso(), "kind": "email", "text": "New lead from email: " + cust["name"], "ref": proj["id"]})
            state["inbox"].insert(0, entry); changed += 1
        if not changed:
            return 0
        state["inbox"] = state["inbox"][:300]; state["activity"] = state["activity"][:500]
        state.setdefault("meta", {})["updatedAt"] = now_iso()
        if write_state(db, "pavora:wcp", state, version):
            return changed
        time.sleep(1)   # someone saved in PAVORA meanwhile: re-read and re-apply
    log("PAVORA state kept changing; will retry next sweep")
    return -1


# ---------------------------------------------------------------- loop
def sweep_once(db_path: str, imports_dir: Path) -> None:
    db = sqlite3.connect(db_path, timeout=30)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS mail_seen (account TEXT, message_id TEXT, ts TEXT, PRIMARY KEY(account, message_id))")
        for a in accounts():
            first = not db.execute("SELECT 1 FROM mail_seen WHERE account=? LIMIT 1", (a["user"],)).fetchone()
            try:
                msgs = fetch_new(a, db, int(os.environ.get("DEMEX_MAIL_LOOKBACK_DAYS", "3" if first else "2")))
            except Exception as e:
                log("%s: could not read mailbox (%s: %s)" % (a["user"], type(e).__name__, str(e)[:120]))
                continue
            n = sweep_ddd(db, msgs, imports_dir) if a["org"] == "ddd" else sweep_wcp(db, msgs, a["user"])
            if n >= 0:
                db.executemany("INSERT OR IGNORE INTO mail_seen VALUES(?,?,?)", [(a["user"], m["message_id"], now_iso()) for m in msgs])
                db.commit()
            log("%s: %d new message(s), %d added" % (a["user"], len(msgs), max(n, 0)))
    finally:
        db.close()


def _loop(db_path: str, imports_dir: Path) -> None:
    every = max(5, int(os.environ.get("DEMEX_MAIL_INTERVAL_MIN", "15"))) * 60
    time.sleep(30)   # let the bridge finish starting
    while True:
        try:
            sweep_once(db_path, imports_dir)
        except Exception as e:   # the sweep must never take the server down
            log("sweep error: %s" % type(e).__name__)
        time.sleep(every)


def start(db_path: str, imports_dir: Path) -> bool:
    accts = accounts()
    if not accts:
        log("no DEMEX_MAIL_ACCOUNTS set; mail sweep off")
        return False
    imports_dir.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_loop, args=(db_path, imports_dir), name="mail-sweep", daemon=True).start()
    log("sweeping %d mailbox(es): %s" % (len(accts), ", ".join("%s (%s)" % (a["user"], a["org"]) for a in accts)))
    return True
