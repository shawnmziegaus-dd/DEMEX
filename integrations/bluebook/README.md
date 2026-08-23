# The Blue Book Network

**Key** `bluebook` · **Category** bid_platform · **Status** `live` · **Direction** inbound · **Last verified** 2026-08-11

Secondary ITB lead source, mostly regional GCs.

## Accounts

- shawn@disassembledynamics.com

## Access

- **Auth method** — email_ingest (no API)
- **Protocol** — email
- **Base** — https://www.thebluebook.com

## What moves

| Direction | Entities |
|---|---|
| DEMEX reads | project name, location, bid due date, GC contact |
| DEMEX writes | — |

## Field mapping

| Source | DEMEX target |
|---|---|
| project title | `projects[].name` |
| due date | `projects[].dueAt` |

## Wiring

- **Code** — `email_ingestor.py`
- **Trigger** — scheduled: daily ITB intake

## Known limits

- Low volume relative to PlanHub; sender addresses vary by GC so keyword matching carries the load.

## Needs a decision from Shawn

- [ ] Confirm the exact sending domain(s) currently in use so the ingestor can key on sender rather than keywords.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | raw payloads pulled from this app (emails, exports, downloads) |
| `outbound/` | anything DEMEX pushes back, staged for approval |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, committed, used as a parser fixture |

