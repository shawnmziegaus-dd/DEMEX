# PlanHub

**Key** `planhub` · **Category** bid_platform · **Status** `live` · **Direction** inbound · **Last verified** 2026-08-11

Highest-volume lead source. Pushes ITB notifications and bid-deadline reminders by email; project detail, GC identity and plan files sit behind the paid tier.

## Accounts

- admin@disassembledynamics.com
- Shawn@disassembledynamics.com

## Access

- **Auth method** — email_ingest (no API)
- **Notes** — Portal login is manual at planhub.com. No public API for subcontractor accounts.
- **Protocol** — email
- **Senders** — projectnotification@planhubprojects.com, projectupdate@planhub.com
- **Base** — https://www.planhub.com

## What moves

| Direction | Entities |
|---|---|
| DEMEX reads | project name, city/state/zip, bid due date + time, construction type, square footage, building use, scope narrative |
| DEMEX writes | bid intention (Bidding / Not Bidding) - manual click only |

## Field mapping

| Source | DEMEX target |
|---|---|
| project title | `projects[].name` |
| city line | `projects[].address` |
| due line | `projects[].dueAt` |
| About This Project | `projects[].scope` |
| Square Footage | `projects[].sqft` |
| Construction Type/Building Use | `projects[].notes` |

## Wiring

- **Code** — `email_ingestor.py`
- **Trigger** — scheduled: daily ITB intake

## Known limits

- GC/owner contact is hidden on the free tier - projects land against the 'PlanHub - GC Not Disclosed' placeholder client and must be re-pointed after preview.
- Notification volume is high and untargeted; pad-ready new construction with no demolition scope is the dominant false positive.
- Reminder emails ('only N days left') are not new invitations - dedupe against existing projects by name.

## Needs a decision from Shawn

- [ ] Decide whether a paid PlanHub tier is worth it - it would unlock GC contact + plan files and remove the placeholder-client step.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | raw payloads pulled from this app (emails, exports, downloads) |
| `outbound/` | anything DEMEX pushes back, staged for approval |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, committed, used as a parser fixture |

