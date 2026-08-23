# Bonfire

**Key** `bonfire` · **Category** public_procurement · **Status** `live` · **Direction** inbound · **Last verified** 2026-08-11

Public-owner solicitation portal. Carries municipal/county RFPs, addenda, Q&A responses and bid tabulations.

## Accounts

- shawn@disassembledynamics.com

## Access

- **Auth method** — portal login (manual) + email notices
- **Notes** — Vendor portal account per issuing agency.
- **Protocol** — email
- **Senders** — No-Reply@gobonfire.com
- **Base** — https://gobonfire.com

## What moves

| Direction | Entities |
|---|---|
| DEMEX reads | solicitation number, title, public notices, bid tabulation |
| DEMEX writes | bid submission - manual in portal |

## Field mapping

| Source | DEMEX target |
|---|---|
| RFP-xx-xxxxx | `projects[].bidNo` |
| title | `projects[].name` |
| notice type | `projects[].status` |

## Wiring

- **Code** — `email_ingestor.py`
- **Trigger** — scheduled: daily ITB intake

## Known limits

- Emits notice types that are NOT invitations - 'Preliminary Bid Tabulation', award notices, Q&A postings. These must be classified, not imported as leads.
- Bid tabulations are a competitive-intelligence goldmine and should be routed to the learning loop, not the pipeline.

## Needs a decision from Shawn

- [ ] Bid tabulations from Bonfire are the single best source of competitor pricing - worth a dedicated parser.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | raw payloads pulled from this app (emails, exports, downloads) |
| `outbound/` | anything DEMEX pushes back, staged for approval |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, committed, used as a parser fixture |

