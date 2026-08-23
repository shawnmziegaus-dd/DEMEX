# DEMEX Integration Layer

Every outside application DEMEX touches gets one folder here. The folder is the
whole contract: what the app is, how we get in, what data moves, where it lands
in the DEMEX schema, and what it cannot do.

The rule is that **nothing about an integration lives only in someone's head.**
If a parser keys on a sender address, that address is in `connector.json`. If a
feed silently drops the GC contact, that is written down under `known_limits`,
because an unstated limit eventually becomes a wrong bid.

---

## Layout

```
integrations/
  README.md              this file
  registry.json          generated index of every connector - do not hand-edit
  _scaffold.py           validates connectors and regenerates the registry
  _template/             copy this to add a new app
  <connector-key>/
    connector.json       the spec (machine-readable, demex_connector/v1)
    README.md            the human page
    inbound/             raw payloads pulled from the app
    outbound/            anything DEMEX pushes back, staged for approval
    logs/                one log per sync run, YYYY-MM-DD.log
    samples/             one representative payload, kept as a parser fixture
```

After adding or editing a connector:

```
python integrations/_scaffold.py
```

It creates missing folders, validates each spec, and rewrites `registry.json`.
It never overwrites a `connector.json` or `README.md` you have edited.

---

## Current connectors

| Key | App | Category | Status | Direction |
|---|---|---|---|---|
| `gmail` | Gmail (estimating inboxes) | email | **live** | inbound |
| `planhub` | PlanHub | bid platform | **live** | inbound |
| `buildingconnected` | BuildingConnected (Autodesk) | bid platform | **live** | inbound |
| `bluebook` | The Blue Book Network | bid platform | **live** | inbound |
| `reproconnect` | ReproConnect | plan room | **live** | inbound |
| `bonfire` | Bonfire (public procurement) | public procurement | **live** | inbound |
| `bluebeam` | Bluebeam Revu | takeoff / markup | **live** | bidirectional |
| `google-calendar` | Google Calendar | scheduling | planned | outbound |
| `google-drive` | Google Drive | document storage | partial | bidirectional |
| `dropbox` | Dropbox (GC plan folders) | file transfer | planned | inbound |
| `quickbooks` | QuickBooks | accounting | planned | bidirectional |
| `docusign` | DocuSign | contracts | planned | bidirectional |
| `qualora` | Qualora | **unverified** | unverified | unknown |

`registry.json` is the machine-readable version of this table and is regenerated
from the connector specs, so the two cannot drift apart.

---

## Status vocabulary

Status is a promise, so it is kept narrow:

| Status | Means |
|---|---|
| `live` | Runs today, against a named code file. Data is flowing. |
| `partial` | Some of it works. The gap is written down in `known_limits`. |
| `planned` | The folder and the spec exist. Nothing else does. |
| `unverified` | We do not yet know enough about the product to describe it. Fields are left blank rather than guessed. |
| `blocked` | Understood and wanted, but waiting on something external (usually authorisation). |

`qualora` is `unverified` on purpose. It was named as an application DEMEX must
tie into, but it does not appear anywhere in the codebase, the mail history or a
web search, so every field in that spec is blank rather than invented. Fill in
`integrations/qualora/connector.json` and it becomes a real connector.

---

## Where the data lands

Everything inbound converges on the same two shapes, which is what makes the
pipeline mergeable no matter which platform a lead arrived through:

- **`demex_itb_import_YYYY-MM-DD.json`** — `{clients[], projects[]}`, loaded via
  Pipeline → Import → Merge in the cockpit.
- **`demex_records.db`** — `projects`, `emails`, `line_items`, `attachments`,
  plus the takeoff-standard tables described in `standards/takeoff/`.

The one field that matters most for attribution is `projects[].source`. Write it
as the platform the invitation actually arrived through — `PlanHub`,
`BuildingConnected`, `Direct GC (<name>)` — because win rate by source is what
tells us which of these feeds is worth paying for.

---

## Two things worth deciding

- **PlanHub paid tier.** The free tier hides the GC contact and the plan files,
  which is why PlanHub leads land against a placeholder client and need a manual
  re-point. That is the single largest source of hand labour left in the intake.
- **Bonfire bid tabulations.** Public bid tabs are the only unfiltered look at
  what competitors actually bid. They arrive today and get discarded as noise;
  routed into the learning loop they would price future public work directly.
