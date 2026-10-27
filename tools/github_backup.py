#!/usr/bin/env python3
"""Back up the live DEMEX / Estimatio / Qualora data to a private GitHub repo.

What goes up (one commit per run, so the repo history is the backup history):
    meta.json                      when / where / what was backed up
    state/<app>.json               shared app state (pipeline, clients, rates, settings) per app
    db/<table>.json                every table in demex_records.db (corrections, imports, ...)
    demex_rate_card.json           live rate card
    learning_state.json            self-learning pricing state (if present)
    itb_imports/<file>.json        daily inbox-sweep drop files
    projects/index.json            every project folder and file (name, size, modified)
    projects/<proj>/<file>         plan PDFs and markups up to GITHUB_BACKUP_MAX_MB (default 20) each

Needs two settings (env or .env):
    GITHUB_TOKEN          a fine-grained personal access token with Contents: read/write on the repo
    GITHUB_BACKUP_REPO    owner/repo, e.g. shawnmziegaus-dd/DEMEX-data   (create it private, empty)
Optional: GITHUB_BACKUP_BRANCH (main), GITHUB_BACKUP_MAX_MB (20), GITHUB_BACKUP_PDFS=0 to skip PDFs.

Usage:  python tools/github_backup.py [--json] [--dry-run]
Runs from the server (nightly + POST /api/backup) and from the desktop app alike.
Only stdlib -- nothing to install.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"


def load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class GH:
    def __init__(self, token: str, repo: str):
        self.token, self.repo = token, repo

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "demex-backup",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}")
            except Exception:
                msg = {}
            return e.code, msg

    def blob(self, raw: bytes) -> str:
        st, r = self.call("POST", f"/repos/{self.repo}/git/blobs",
                          {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        if st not in (200, 201):
            raise RuntimeError(f"blob upload failed ({st}): {r.get('message')}")
        return r["sha"]


def collect(max_mb: float, pdfs: bool) -> dict[str, bytes]:
    """Gather everything worth keeping into {path: bytes}."""
    files: dict[str, bytes] = {}
    db_path = ROOT / "demex_records.db"
    counts: dict[str, int] = {}
    if db_path.is_file():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for t in tables:
                rows = [dict(r) for r in conn.execute(f'SELECT * FROM "{t}"')]
                counts[t] = len(rows)
                if t == "app_state":
                    for r in rows:
                        try:
                            files[f"state/{r['app']}.json"] = json.dumps(
                                {"version": r.get("version"), "updated_at": r.get("updated_at"),
                                 "data": json.loads(r["data"])}, indent=1).encode()
                        except Exception:
                            files[f"state/{r['app']}.json"] = str(r.get("data", "")).encode()
                else:
                    files[f"db/{t}.json"] = json.dumps(rows, indent=1, default=str).encode()
        finally:
            conn.close()
    for name in ("demex_rate_card.json", "learning_state.json", "demex_rate_reference.json"):
        p = ROOT / name
        if p.is_file():
            files[name] = p.read_bytes()
    imports_dir = Path(os.environ.get("DEMEX_IMPORTS_DIR") or ROOT)
    for p in sorted(imports_dir.glob("demex_itb_import_*.json")):
        files[f"itb_imports/{p.name}"] = p.read_bytes()
    projects = ROOT / "projects"
    index = []
    limit = int(max_mb * 1024 * 1024)
    if projects.is_dir():
        for p in sorted(projects.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(projects).as_posix()
            stt = p.stat()
            index.append({"path": rel, "size": stt.st_size,
                          "modified": datetime.fromtimestamp(stt.st_mtime, timezone.utc).isoformat()})
            small = stt.st_size <= limit
            if small and (pdfs or p.suffix.lower() != ".pdf"):
                files[f"projects/{rel}"] = p.read_bytes()
    files["projects/index.json"] = json.dumps(index, indent=1).encode()
    files["meta.json"] = json.dumps({
        "backed_up_at": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "source": str(ROOT),
        "db_tables": counts,
        "files": len(files) + 1,
    }, indent=1).encode()
    return files


def push(files: dict[str, bytes], token: str, repo: str, branch: str) -> dict:
    gh = GH(token, repo)
    st, r = gh.call("GET", f"/repos/{repo}")
    if st == 404:
        raise RuntimeError(f"repo {repo} not found or the token cannot see it")
    if st != 200:
        raise RuntimeError(f"GitHub says {st}: {r.get('message')}")
    if not r.get("private"):
        raise RuntimeError(f"refusing to back up business data to a PUBLIC repo ({repo}); make it private")
    st, ref = gh.call("GET", f"/repos/{repo}/git/ref/heads/{branch}")
    if st == 404 or (st == 409):               # empty repo: make the first commit through the contents API
        st2, r2 = gh.call("PUT", f"/repos/{repo}/contents/README.md", {
            "message": "DEMEX data backups (automated)", "branch": branch,
            "content": base64.b64encode(b"# DEMEX data backups\n\nAutomated backups from the DEMEX server. One commit per run.\n").decode()})
        if st2 not in (200, 201):
            raise RuntimeError(f"could not initialise {repo}: {r2.get('message')}")
        st, ref = gh.call("GET", f"/repos/{repo}/git/ref/heads/{branch}")
    if st != 200:
        raise RuntimeError(f"branch {branch} not readable: {ref.get('message')}")
    parent = ref["object"]["sha"]
    st, pc = gh.call("GET", f"/repos/{repo}/git/commits/{parent}")
    base_tree = pc["tree"]["sha"]
    # Skip blobs GitHub already has (same content => same git sha), so nightly runs stay small.
    st, cur = gh.call("GET", f"/repos/{repo}/git/trees/{base_tree}?recursive=1")
    have = {t["path"]: t["sha"] for t in cur.get("tree", []) if t.get("type") == "blob"} if st == 200 and not cur.get("truncated") else {}
    tree, uploaded, reused = [], 0, 0
    for path, raw in sorted(files.items()):
        git_sha = hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()
        if have.get(path) == git_sha:
            reused += 1
            continue
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": gh.blob(raw)})
        uploaded += 1
    if not tree:
        return {"ok": True, "repo": repo, "commit": parent, "uploaded": 0, "unchanged": reused, "note": "nothing changed"}
    st, t = gh.call("POST", f"/repos/{repo}/git/trees", {"base_tree": base_tree, "tree": tree})
    if st not in (200, 201):
        raise RuntimeError(f"tree failed: {t.get('message')}")
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    st, c = gh.call("POST", f"/repos/{repo}/git/commits",
                    {"message": f"Backup {when} ({uploaded} changed, {reused} unchanged)", "tree": t["sha"], "parents": [parent]})
    if st not in (200, 201):
        raise RuntimeError(f"commit failed: {c.get('message')}")
    st, u = gh.call("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", {"sha": c["sha"]})
    if st != 200:
        raise RuntimeError(f"ref update failed: {u.get('message')}")
    return {"ok": True, "repo": repo, "commit": c["sha"], "uploaded": uploaded, "unchanged": reused,
            "url": f"https://github.com/{repo}/commit/{c['sha']}"}


def main(argv: list[str]) -> int:
    load_dotenv()
    as_json = "--json" in argv
    dry = "--dry-run" in argv
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    repo = (os.environ.get("GITHUB_BACKUP_REPO") or "").strip()
    branch = (os.environ.get("GITHUB_BACKUP_BRANCH") or "main").strip()
    max_mb = float(os.environ.get("GITHUB_BACKUP_MAX_MB") or 20)
    pdfs = (os.environ.get("GITHUB_BACKUP_PDFS") or "1") != "0"
    receipt = ROOT / "backup_last.json"
    out: dict
    try:
        files = collect(max_mb, pdfs)
        if dry:
            out = {"ok": True, "dry_run": True, "files": len(files),
                   "bytes": sum(len(v) for v in files.values()), "paths": sorted(files)[:40]}
        else:
            if not token or not repo:
                raise RuntimeError("GITHUB_TOKEN and GITHUB_BACKUP_REPO are not set (see .env.example)")
            out = push(files, token, repo, branch)
            out["files"] = len(files)
    except Exception as e:
        out = {"ok": False, "error": str(e)}
    out["last"] = datetime.now(timezone.utc).isoformat()
    if not dry:
        try:
            receipt.write_text(json.dumps(out, indent=1), encoding="utf-8")
        except Exception:
            pass
    if as_json:
        print(json.dumps(out))
    else:
        print("GitHub backup:", "OK" if out.get("ok") else "FAILED")
        for k, v in out.items():
            if k != "paths":
                print(f"  {k}: {v}")
        if "paths" in out:
            print("  " + "\n  ".join(out["paths"]))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
