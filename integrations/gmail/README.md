# Gmail (estimating inboxes)

**Key** `gmail` · **Category** email · **Status** `live` · **Direction** inbound · **Last verified** 2026-08-11

Primary intake pipe. Every ITB, addendum, award notice and GC question arrives here first. Feeds the daily ITB intake job and the project auto-create flow.

## Accounts

- shawn@disassembledynamics.com
- estimates@disassembledynamics.com
- admin@disassembledynamics.com
- Admin@disassembledynamics.com

## Access

- **Auth method** — app_password + Claude Gmail connector
- **Secrets** — `DEMEX_GMAIL_USER`, `DEMEX_GMAIL_APP_PASSWORD` (never committed; see `.env.example`)
- **Notes** — App password lives in .env (git-ignored). See .env.example. The Claude-side Gmail MCP connector is authorised separately in connector settings.
- **Protocol** — IMAP + Gmail MCP search_threads/get_thread
- **Search Query** — ("invitation to bid" OR "invite to bid" OR "bid invitation" OR ITB OR "request for proposal" OR "request to bid" OR "bids due" OR demolition) newer_than:2d -in:sent -in:draft

## What moves

| Direction | Entities |
|---|---|
| DEMEX reads | thread, message, attachment, sender, due date text |
| DEMEX writes | — |

## Field mapping

| Source | DEMEX target |
|---|---|
| sender domain | `clients[].email / clients[].name` |
| subject | `projects[].name` |
| body due date | `projects[].dueAt` |
| platform of origin | `projects[].source` |
| attachments | `attachments table / plans folder` |

## Wiring

- **Code** — `email_ingestor.py`, `demex_bridge.py`
- **Trigger** — scheduled: demex-itb-intake, daily ~06:15 local

## Known limits

- App passwords require 2-Step Verification on the Google account.
- IMAP body parsing is HTML-heavy for platform blasts; plaintext fallback is lossy on tables.
- Gmail thread search returns whole threads when any one message matches, so already-submitted bids surface and must be filtered.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | raw payloads pulled from this app (emails, exports, downloads) |
| `outbound/` | anything DEMEX pushes back, staged for approval |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, committed, used as a parser fixture |

