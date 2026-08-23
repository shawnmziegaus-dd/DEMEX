# ReproConnect

**Key** `reproconnect` · **Category** plan_room · **Status** `live` · **Direction** inbound · **Last verified** 2026-08-11

Plan-room distribution used by several Florida GCs. Carries the actual drawing sets rather than the invitation text.

## Accounts

- admin@disassembledynamics.com

## Access

- **Auth method** — email_ingest + public download link
- **Protocol** — email + https download

## What moves

| Direction | Entities |
|---|---|
| DEMEX reads | plan set download link, project name, bid date |
| DEMEX writes | — |

## Field mapping

| Source | DEMEX target |
|---|---|
| download link | `plans/<project>/` |
| project title | `projects[].name` |

## Wiring

- **Code** — `email_ingestor.py`
- **Trigger** — scheduled: daily ITB intake

## Known limits

- Download links expire; plans must be pulled promptly and cached locally under plans/.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | raw payloads pulled from this app (emails, exports, downloads) |
| `outbound/` | anything DEMEX pushes back, staged for approval |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, committed, used as a parser fixture |

