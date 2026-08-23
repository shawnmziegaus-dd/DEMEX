# BuildingConnected

**Key** `buildingconnected` · **Category** bid_platform · **Status** `live` · **Direction** inbound · **Last verified** 2026-08-11

Invitation and addendum channel for GCs who run their bid lists on BuildingConnected (e.g. SA Casey Construction). Addenda arrive here, not by direct email.

## Accounts

- estimates@disassembledynamics.com

## Access

- **Auth method** — email_ingest (no API today)
- **Notes** — Autodesk Platform Services exposes a BuildingConnected API but it requires an Autodesk app + partner-level access. Not provisioned.
- **Protocol** — email
- **Senders** — team@buildingconnected.com
- **Base** — https://app.buildingconnected.com

## What moves

| Direction | Entities |
|---|---|
| DEMEX reads | GC name and sender contact, project name, message body (addendum notices), attachments |
| DEMEX writes | bid response - manual in portal |

## Field mapping

| Source | DEMEX target |
|---|---|
| 'X of Y sent your company a message about Z' | `clients[].contactName / clients[].name / projects[].name` |
| message body | `projects[].notes` |
| attachment | `attachments table` |

## Wiring

- **Code** — `email_ingestor.py`
- **Trigger** — scheduled: daily ITB intake

## Known limits

- Subject lines are truncated with an ellipsis ('Willow Run Park Renov...') - never key dedupe on subject alone.
- Addendum notices look like new invitations to a keyword filter; they must merge into the existing project record.
- Attachments are frequently referenced as 'uploaded' with no file attached - the plan must be pulled from the portal.

## Needs a decision from Shawn

- [ ] Confirm whether the Autodesk/BuildingConnected account tier permits API access; if so this moves from email scraping to a real feed.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | raw payloads pulled from this app (emails, exports, downloads) |
| `outbound/` | anything DEMEX pushes back, staged for approval |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, committed, used as a parser fixture |

