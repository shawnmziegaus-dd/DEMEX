# QuickBooks

**Key** `quickbooks` · **Category** accounting · **Status** `planned` · **Direction** bidirectional · **Last verified** 2026-08-11

Closes the learning loop. Actual labour, disposal, equipment, subcontractor and material costs per job flow back in so estimate accuracy improves against reality instead of against memory.

## Access

- **Auth method** — oauth2 (Intuit) / Claude QuickBooks connector
- **Secrets** — `QB_CLIENT_ID`, `QB_CLIENT_SECRET`, `QB_REALM_ID` (never committed; see `.env.example`)
- **Notes** — The Claude-side QuickBooks connector currently reports as requiring authorisation.
- **Protocol** — REST
- **Base** — https://quickbooks.api.intuit.com

## What moves

| Direction | Entities |
|---|---|
| DEMEX reads | invoice, bill, vendor payment, job/customer, P&L by job |
| DEMEX writes | invoice draft from awarded contract amount |

## Field mapping

| Source | DEMEX target |
|---|---|
| job actuals | `projects[].actual_realized_costs` |
| invoice total | `projects[].bidAmount reconciliation` |
| vendor bill | `estCosts.sub / estCosts.disposal` |

## Wiring

- **Code** — `learning_loop.py`, `pricing_engine.py`
- **Trigger** — scheduled: proposed weekly job-cost sync

## Known limits

- Not yet authorised - see owner_action.
- Job costing only works if every cost is coded to a job in QuickBooks; uncoded costs silently break the feedback loop.

## Needs a decision from Shawn

- [ ] Authorise the QuickBooks connector in Claude connector settings before this can be built.
- [ ] Confirm that jobs are tracked as Customers:Jobs (or Classes) so costs can be attributed per project.

## Folders

| Folder | Holds |
|---|---|
| `inbound/` | raw payloads pulled from this app (emails, exports, downloads) |
| `outbound/` | anything DEMEX pushes back, staged for approval |
| `logs/` | run logs, one file per sync, `YYYY-MM-DD.log` |
| `samples/` | one representative payload, committed, used as a parser fixture |

