# DEMEX Email Ingestor

`email_ingestor.py` is a standalone pipeline that reads bid-related email from a
business Gmail account (read-only), extracts metadata and PDF attachments, runs a
rule-based line-item parser over the PDFs, and stores everything in a local
SQLite database (`demex_records.db`) whose columns mirror the DEMEX **v4** schema.

It can optionally emit a JSON file that imports directly into **DEMEX.html**, so
inbound bids flow into the estimating app without manual re-entry.

---

## What it does

1. **Connect** to Gmail over IMAP-SSL (`imaplib`), mailbox opened **read-only**.
2. **Scan every selectable folder** (all labels **plus `[Gmail]/All Mail`**) for
   the keywords: *Invitation to Bid, ITB, Contract Award, Change Order, Proposal*.
   The same message can live in several folders; it's de-duplicated by Message-ID.
3. **Parse** each email body for **due date, client name, scope summary, project
   name**, and classify the scope as **demolition** vs. **hazardous-abatement
   containment**.
4. **Download** PDF attachments into per-project folders under `projects/`.
5. **Read the PDFs** and reverse-engineer **line items** — unit prices, volumes,
   and bulk-pricing thresholds, split by demolition vs. containment.
6. **Store** projects, emails, line items, and attachments in `demex_records.db`.
7. *(optional)* **Export** a `DEMEX.html`-importable JSON snapshot.

---

## Two honest caveats

- **PDF reading is not standard-library.** Email ingestion, metadata parsing, the
  SQLite database, and JSON export are pure stdlib. Reading text out of PDFs is
  not — install `pdfplumber` (see below). Without it the script still ingests
  emails and saves PDFs; it just **skips** line-item extraction and logs a warning.
- **The "NLP" is a deterministic rule-based heuristic**, not a trained model. PDF
  bid layouts vary wildly, so extracted line items are written with
  `needs_review = 1`. Treat them as a **first draft for an estimator**, not as
  bid-ready numbers.

---

## Install

PDF parsing requires one (preferably both) third-party packages:

```bash
pip install -r requirements.txt
```

`requirements.txt` pins `pdfplumber` (preferred) and `pypdf` (automatic fallback).

---

## Secure credential setup (do this before the first run)

**Never use your normal Gmail password, and never put credentials in the code.**

1. **Enable 2-Step Verification** on the Google account:
   Google Account → Security → 2-Step Verification.
2. **Generate an App Password:** Google Account → Security → App passwords →
   app = "Mail", device = "DEMEX Ingestor". Google returns a 16-character code.
3. **Store it.** Two supported options:

   **Option A — environment variables (recommended).** Nothing touches disk in the repo.
   ```powershell
   # Windows PowerShell (persists for your user; open a NEW terminal after):
   setx DEMEX_GMAIL_USER "you@yourcompany.com"
   setx DEMEX_GMAIL_APP_PASSWORD "abcd efgh ijkl mnop"
   ```
   ```bash
   # macOS / Linux (add to ~/.bashrc or ~/.zshrc to persist):
   export DEMEX_GMAIL_USER="you@yourcompany.com"
   export DEMEX_GMAIL_APP_PASSWORD="abcd efgh ijkl mnop"
   ```
   The script strips the spaces from the app password automatically.

   **Option B — a credentials file kept OUTSIDE the repo.**
   ```json
   { "user": "you@yourcompany.com", "app_password": "abcdefghijklmnop" }
   ```
   Pass it with `--credentials-file C:\path\outside\repo\demex_credentials.json`.
   On macOS/Linux the script **refuses** the file unless it is `chmod 600`.

4. **Secrets are git-ignored.** `.gitignore` blocks `*.cred`, `.env`,
   `credentials.json`, `demex_records.db`, and `projects/`. The password is never
   logged or written to the database, and the mailbox is opened read-only.

---

## Usage

```bash
# Safe smoke test: 5 emails, recent window, skip PDF parsing
python email_ingestor.py --since 2026-06-01 --limit 5 --no-pdf-parse -v

# Full run across ALL folders + All Mail, then export for the app
python email_ingestor.py --since 2026-01-01 --export-json demex_email_import.json

# Narrow to specific folders instead of scanning everything
python email_ingestor.py --folders INBOX "[Gmail]/All Mail"
```

Then in **DEMEX.html**, import `demex_email_import.json` to load the new projects.

### Command-line options

| Flag | Default | Purpose |
|---|---|---|
| `--since YYYY-MM-DD` | none | Only scan email on/after this date. **Strongly recommended.** |
| `--folders A B ...` | *all folders* | Limit to specific folders/labels. Omit to scan every selectable folder incl. All Mail. |
| `--db PATH` | `demex_records.db` | SQLite database (created if absent). |
| `--attachments-dir DIR` | `projects` | Root for downloaded PDFs (per-project subfolders). |
| `--credentials-file PATH` | env vars | JSON cred file outside the repo; falls back to env vars. |
| `--no-pdf-parse` | off | Download PDFs but skip line-item extraction. |
| `--limit N` | `0` (no limit) | Stop after N emails — useful for a first dry test. |
| `--export-json PATH` | none | Write a DEMEX.html-importable JSON file after ingest. |
| `-v`, `--verbose` | off | Debug logging. |

---

## Database

`demex_records.db` (SQLite) contains:

- **`projects`** — v4 columns: `project_id`, `name`, `client_name`, `address`,
  `scope_type`, `due_date`, `scope_summary`, `status`, `win_loss_status`,
  `estimated_quantities`, `unit_rates`, `bulk_pricing_thresholds`,
  `actual_realized_costs`, `source_message_id`, `needs_review`, timestamps.
- **`emails`** — one row per ingested message (subject, sender, dates, folder).
- **`line_items`** — parsed PDF rows (qty, unit, unit price, scope class, review flag).
- **`attachments`** — saved PDF paths linked to their project.

---

## First-run checklist

- [ ] `pip install -r requirements.txt`
- [ ] App Password generated and stored (env vars or `--credentials-file`)
- [ ] Confirm `.gitignore` is present (it is) so secrets/DB/PDFs stay uncommitted
- [ ] Smoke test with `--since` + `--limit 5 --no-pdf-parse -v`
- [ ] Review the smoke-test output, then do a full run
- [ ] Import the `--export-json` file into DEMEX.html
