# DEMEX — Internal Operations Cockpit
## Architecture, Routing & UI Overhaul Plan

**Status:** Planning deliverable — review before final code is generated.
**Audience:** Internal operators only (not an external product).
**Locked decisions:** Single double-click `.html` file · React 18 + Tailwind via CDN (no build step) · Incremental cockpit shell that wraps the existing proven engine.

---

## 1. Current-State Deconstruction (Lead Systems Contractor view)

**What exists today.** DEMEX is a single 8,000-line HTML file: one `<script>` containing all logic, plus the DEMEX OS operations module I appended (Projects, Bid Board, CRM, Job Cost, Profit, Pricing IQ, Agent). AI runs through `callAI()` → Anthropic (direct key in `localStorage`, or a `localhost:3000` proxy). Operational data lives in `localStorage` under `demex_os_db_v3`.

**Data flow today.** UI event → global function → mutates an in-memory object → `localStorage.setItem` → re-render via `innerHTML`. There is no schema, no validation gate, no referential-integrity check, and no audit trail. Writes are trusted blindly.

**Friction & risk points identified.**

| Area | Problem | Impact |
|---|---|---|
| Navigation | 13 flat, equal-weight tabs; no "home" | Operator must scan tabs to find work; high cognitive load |
| Priority surfacing | Nothing tells the operator what needs action *today* | Bids go cold, due dates missed |
| Validation | No type/required/enum/numeric/reference checks on writes | Bad data corrupts Pricing IQ and Profit math |
| Persistence | Single-browser `localStorage` only; manual export is the only backup | Data loss risk; no multi-device |
| Schema | No versioning/migration beyond a key bump | Fragile upgrades |
| Integrity | (Now fixed) previously seeded fabricated jobs | Learning engine must only ever see real data |

---

## 2. Target Architecture (single-file, production-grade for internal use)

**Runtime.** One `index.html`: React 18 (UMD) + Babel Standalone for in-browser JSX + Tailwind Play CDN. No install, no build — double-click and run from `file://`. (Trade-off noted: Babel compiles on load, ~200–500ms one-time cost; acceptable internally. Path to precompile later if desired.)

**App shell.** `Boot → (optional PIN gate) → Hash Router → Workspace`. Hash routing (`#/dashboard`, `#/estimating`, …) is used deliberately so deep links and back/forward work from a local file with no server.

**State & the validation "API" layer.** A single store (the existing DB object) is exposed through a React context with one **choke-point** writer: `db.commit(action)`. Every mutation goes through it and is validated before it persists:

- **Type & required checks** per entity field.
- **Enum checks** (e.g., `status ∈ {invited, estimating, submitted, won, lost}`).
- **Numeric invariants** (amounts ≥ 0, probability 0–100, hours ≥ 0).
- **Referential integrity** (`client_id`, `project_id` must resolve).
- **Audit trail** — each accepted commit appends `{ts, action, entity, before, after}` to an audit log.
- On failure: reject, surface a clear inline error, never persist partial state.

This reproduces server-side API validation discipline without a server.

**Formalized data schema (entities).**

- `Company` — name, contact, address, phone, email, bidPrefix, overheadPct, profitPct.
- `Client` — id, name, type, contactName, email, phone, address, notes.
- `Project` (bid/job) — id, name, client_id→Client, address, type, scope, status(enum), bidNo, bidAmount, alternates[], submittedAt, dueAt, awardedAt, probability(0–100), source, sqft, estCosts{labor,disposal,equipment,material,sub}, estLaborHrs, estQty[], completed(bool).
- `Actual` — id, project_id→Project, date, category(enum), desc, amount, hours.
- `Followup` — id, client_id, project_id, due, note, done.
- `RateOverride` — service→adjustment%.
- `PricingSignal` — derived, never stored as truth.
- `AuditEntry` — ts, action, entity, before, after.

**Persistence & resilience.** `localStorage` for the working set; **IndexedDB** for history/larger sets; schema `version` field + migration functions on load; **JSON snapshot export** (already built) plus a scheduled "back up your data" nudge; import with the same validation gate (replace or merge).

**Security (internal posture).** Optional 4-digit PIN gate on boot; API key entry masked and kept only in `localStorage`; all rendered strings escaped (XSS-safe); CSP `<meta>` restricting sources to the CDNs used; no secrets committed to the file.

**Data integrity (non-negotiable).** Seed contains only verifiable records (the real Sunglass Hut bid). No awarded jobs, costs, quantities, or actuals are ever invented. Pricing IQ and Profit compute only from data you enter or import.

---

## 3. Routing & Information-Architecture Optimization

**From 13 flat tabs → 5 workspaces + a global command bar.** Grouping by operator *job-to-be-done*, not by feature.

- **Dashboard** (`#/`) — the cockpit / home. Default landing.
- **Estimating** (`#/estimating`) — Estimator, Rates, Buildouts, Adjustments, Plan Takeoff, Proposals (sub-tabs within the workspace).
- **Pipeline** (`#/pipeline`) — Projects table + Bid Board kanban.
- **Clients** (`#/clients`) — CRM + follow-ups.
- **Performance** (`#/performance`) — Job Cost, Profit, Pricing IQ.
- **Agent** — *not a tab.* A global command bar (`⌘K` / `Ctrl-K`) available on every screen; executes actions and navigates.

**Click-path optimization (before → after).**

| Operator task | Today | Optimized |
|---|---|---|
| "What needs me right now?" | Scan multiple tabs | It *is* the home screen — 0 clicks |
| Price a new ITB | Find estimator tab, set up | Dashboard → **New Bid** (1 click) → Estimating opens pre-seeded |
| Chase a cold bid | Open CRM, scan list | Dashboard triage shows it → **Draft email** (1 click) |
| Check if a job made money | Open Job Cost, then Profit | Performance workspace, one screen |
| Run any action | Navigate menus | `⌘K` → type intent (0 navigation) |

**Routing guards.** Boot completes → store hydrated + schema-validated before any route renders; unknown hash → redirect to Dashboard; the command bar can deep-link into any workspace/sub-view.

---

## 4. Boot Loader Specification

Custom animated boot, on-brand cockpit aesthetic (true black, single red accent, monospace status lines):

- **Motion concept:** a wireframe structural grid that "deconstructs" (demolition motif) and resolves into the DEMEX wordmark, then wipes into the cockpit.
- **Honest progress:** stages tied to real init — `Loading records…` → `Validating schema…` → `Warming agent…` → `Ready`.
- **Constraints:** ~1.2–2.0s, **Skip** control, full `prefers-reduced-motion` fallback (instant, static wordmark). Never blocks on network.

---

## 5. Landing / Home (Cockpit) Specification

Purpose: vector operators into primary workflows with minimum friction. **Action-first, not report-first.**

Core zones (exact layout depends on the concept you pick in §6):

- **Needs You Now** — due/overdue bids, open follow-ups, items awaiting your approval.
- **Pipeline snapshot** — open bids, weighted value, out-for-award, win rate.
- **Performance pulse** — realized margin / portfolio health (shows empty until real jobs are logged).
- **Command bar / Agent** — type a command or jump anywhere.
- **Quick actions** — New Bid · Import History · Upload Plan Set.

---

## 6. Three Central-Dashboard-Grid Concepts

Rendered as live mockups in chat. All three share the cockpit palette and use representative data (the real Sunglass Hut bid where shown). Pick one and I generate the codebase against it.

**Concept A — "Command Rail / Terminal."** A fixed left command rail (icon + label), an asymmetric content field dominated by a single large "active work" focus zone, monospace data readouts, terminal-style status line. Maximum density and speed for power users.

**Concept B — "Asymmetric Bento Cockpit."** A deliberately uneven bento grid: one oversized pipeline tile, a tall full-height Agent column, a wide profit/margin strip, and small KPI chips. Bold typographic hierarchy, spatial rhythm, feels modular and modern without being a generic card grid.

**Concept C — "Triage Operations Queue."** Operator-first: a left **action queue** ("what needs you now") as the hero, a compact live pipeline in the center, and right-side KPI gauges. Optimized for "clear the board" daily workflows — the screen tells you what to do next.

---

## 7. Build Sequence (after concept selection)

1. **Shell** — single-file React+Tailwind scaffold, hash router, boot loader, PIN gate, store + `db.commit` validation layer + audit log.
2. **Home** — chosen dashboard concept wired to live DEMEX data.
3. **Wrap** — mount the existing estimator/takeoff/proposal/OS logic inside the new workspaces (preserve proven code).
4. **Migrate** — convert panels to React components workspace-by-workspace.
5. **Harden** — IndexedDB history, schema migrations, snapshot reminders.

Each phase is independently shippable; the app stays usable throughout.

---

## 8. Open Risks / Notes

- In-browser Babel compile adds a one-time load cost; fine internally, precompilable later.
- Single-file size will grow; mitigated by moving history to IndexedDB.
- `localStorage` is per-browser/per-machine; export/import + IndexedDB are the resilience story until/unless you want a real internal server (separate decision).
