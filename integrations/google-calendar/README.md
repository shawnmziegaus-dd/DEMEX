# Google Calendar

**Key** `google-calendar` · **Category** scheduling · **Status** `planned` · **Direction** outbound · **Last verified** 2026-08-11

Bid due dates, site visits, pre-bid meetings and public bid openings as real calendar events so a 2:00 PM sealed-bid deadline cannot be missed.

## Accounts

- shawn@disassembledynamics.com

## Access

- **Auth method** — Claude Google Calendar connector / .ics export
- **Notes** — An .ics export path already exists - see demex_bid_deadlines_2026-07-08.ics.
- **Protocol** — CalDAV / MCP list_events + create_event / .ics

## What moves

| Direction | Entities |
|---|---|
| DEMEX reads | existing events (conflict check) |
| DEMEX writes | bid deadline event, site visit event, pre-bid meeting |

## Field mapping

| Source | DEMEX target |
|---|---|
| projects[].dueAt | `event start (with a working-back reminder chain)` |
| projects[].name | `event summary` |
| projects[].address | `event location` |

## Wiring

- **Code** — not built yet
- **Trigger** — event: on project import and on due-date change

## Known limits

- Currently a one-way .ics dump rather than a live two-way sync - revised due dates do not update existing events.
- Time zone matters: bid times are quoted Eastern and must not be written as floating times.

## Needs a decision from Shawn

- [ ] Decide whether deadlines should be pushed automatically or staged for approval first.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | raw payloads pulled from this app (emails, exports, downloads) |
| `outbound/` | anything DEMEX pushes back, staged for approval |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, committed, used as a parser fixture |

