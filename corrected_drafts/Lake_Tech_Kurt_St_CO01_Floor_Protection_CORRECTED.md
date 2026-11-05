# Disassemble Dynamics — Lake Tech Kurt St CO-01 — CORRECTED DRAFT

> **CORRECTION LOG (2026-11-03):** AI first-pass estimate corrected by the estimator in four
> places before issue. Each correction below is a real default that was wrong — this file exists
> so the engine learns the corrected values, not the first pass.
>
> 1. **Material unit cost — Ram Board.** AI assumed **$150/roll** from memory. Home Depot listing
>    showed **$65.98** for the 38" x 100' roll; estimator's own store price is **$60.00** for the
>    **4' x 100'** roll. Error was **+150%** on the single largest material line.
>    **→ Rule: never price material from memory. Look it up, then accept the estimator's store price over the listing.**
> 2. **Product width.** AI priced the **38" (3'-2")** Ram Board roll and then "corrected" the proposal
>    from 4'-0" down to 3'-2" — a correction in the wrong direction. Ram Board also ships **48" (4'-0")**,
>    which is what DDD buys. **→ Rule: confirm which SKU of a product line the estimator stocks before deriving coverage from roll width.**
> 3. **Crew size.** AI assumed a **2-man** crew. Actual is **6 men**. Man-hours barely moved (the taping
>    quantity is fixed) but elapsed time collapsed from 1.63 crew-days to ~0.5.
>    **→ Rule: ask crew size. On fixed-quantity tasks it changes the schedule, not the hours.**
> 4. **Profit.** The standard $1,000/crew-day rule produced **$2,400**. Estimator set **$3,000**.
>    **→ See "Pricing rule finding" below — this is a rule defect, not a one-off override.**
>
> Protected path width was also changed by the estimator from 4'-0" to **6'-0"**, which is scope, not a
> correction: it doubled the board and added a full center seam of tape.

3325 US-441 #101, Lake City, Florida 32025 · EIN 99-1771029

**Date:** November 3, 2026
**Subject:** Lake Tech Kurt St Building 1 Restroom Renovations — CO-01, Floor Protection & Dust Containment
**Location:** 2001 Kurt Street, Eustis, Florida 32726
**To:** Ana Palm — D.E. Scorpio Corporation · cc Nick Panzica, Jerry Morisma
**Parent project:** Awarded subcontract, executed 10/20/2026
**Status:** ISSUED 11/3/2026 · awaiting revised contract

---

## Why this scope was added

DDD's standard proposal **excludes** "barricade / temporary enclosures / temporary protection / dust
barriers." Scorpio did not dispute the exclusion. Ana Palm requested pricing and offered to add the
scope and the cost to the subcontract ahead of the work.

Her stated drivers, in her words:
- Protection "securely taped down to prevent students from tripping"
- Dust containment to "keep dust from spreading into the building and potentially triggering the fire alarm"

**Precedent: when a GC asks for protection or containment that our boilerplate excludes, price it as
added scope rather than defending the exclusion.** The same scope is coming on SP Livingston ES
(Scorpio, Duval County Public Schools, bid 11/20) — carry it IN the base there.

---

## Scope of Work

1. Temporary floor protection along one side of each of two hallways, approximately 185 LF each
   (370 LF of path), 6'-0" wide, 46 mil board, fully taped at both edges, at the center seam and at
   all butt seams.
2. Plastic sheeting dust barriers at four (4) restroom doorways, 6 mil poly with zippered access,
   sealed against dust migration.
3. Removal and disposal of all protection and barriers at completion.

---

## Quantity takeoff — traceable

| Derived quantity | Basis |
|---|---|
| 370 LF of protected path | 185 LF per hallway (Ana Palm, 11/3) x 2 hallways |
| 740 LF of board | 6'-0" path / 4'-0" roll width = 2 rows x 370 LF |
| **8 rolls board** | 740 LF / 100 LF per roll = 7.4, rounded up. ~60 LF leftover = repair stock |
| 1,126 LF of tape | 740 LF outer edges + 370 LF center seam + 16 LF butt seams (4 @ 4 ft) |
| **7 rolls tape** | 1,126 LF / 180 LF per roll = 6.3, rounded up |
| 4 dust barriers | Restroom doorway count — **ASSUMED, confirmation requested from Ana** |

**The center seam is the hidden cost of width.** A 4'-0" path needs one row and ~750 LF of tape.
A 6'-0" path needs two rows and 1,126 LF — a 50% tape increase for a 50% width increase.

---

## Material — $795 (Home Depot retail, verified 11/3/2026)

| Item | Qty | Unit | Extension |
|---|---:|---:|---:|
| Ram Board 4' x 100', 46 mil (400 SF/roll) | 8 | $60.00 | $480 |
| Stucco tape | 7 | $12.00 | $84 |
| 6-mil poly sheeting, 20' x 100' | 1 | $139.00 | $139 |
| ZipWall HDAZ2 adhesive zippers, 2-pack | 2 | $20.94 | $42 |
| Staples, misc fastening | lot | — | $50 |
| **Total** | | | **$795** |

**Buy the zippers, not the ZipDoor kits.** ZipWall ZipDoor kits run $41.75 each and include their own
plastic, which is wasted when a poly roll is already on the list. The HDAZ2 zipper 2-pack is $20.94
for two. On four doorways that is **$42 instead of $167** — a $125 saving on an $795 material buy.

**Substitution warning:** do not substitute rosin or builder's paper (~10 mil). It tears and curls, and
in a corridor with students that recreates the exact trip hazard the GC is paying to prevent.

---

## Labor — $825

| Task | Crew x hrs | MH | Rate | Extension |
|---|---|---:|---:|---:|
| Install board and dust barriers | 6 x 4 | 24 | $25 | $600 |
| Removal and disposal | 3 x 3 | 9 | $25 | $225 |
| **Total** | | **33** | | **$825** |

Production: ~47 LF of finished, fully taped 6-foot corridor per crew-hour.

**The taping is the job, not the board.** Rolling out 740 LF takes under an hour. Pulling and seating
1,126 LF of tape is the rest of it. A tape applicator roughly doubles that rate.

No mobilization and no maintenance allowance — the crew is on site for the demolition anyway.

---

## Pricing

| Line | Amount |
|---|---:|
| Material | $795 |
| Labor | $825 |
| **Direct cost** | **$1,620** |
| Contingency @ 5% of direct | $81 |
| Subtotal | $1,701 |
| Profit | $1,299 |
| **ISSUED** | **$3,000** |

Markup on direct cost: **76%**.

### Pricing rule finding — flagged for partner review

The standard **$1,000 per crew-day** profit rule produced **$2,400**. The estimator issued **$3,000**.

The rule is built for demolition, where more men means more production and more days means more
profit. On a **fixed-quantity** task the quantity does not move: 370 LF of corridor is 370 LF whether
two men or six install it. Staffing it properly therefore *cut* the rule's profit by roughly
two-thirds for identical work, identical cost and identical liability.

Same job, same scope, priced at a 2-man crew: **$3,150**. At a 6-man crew: **$2,400**.

**Proposed rule change: for protection, containment and other fixed-quantity add-on scopes, price
profit as a markup on direct cost, not per crew-day.** 76% here is the estimator's judgment and a
reasonable anchor for the next one.

---

## Assumptions stated to the GC

- Protection 6'-0" wide, one side of each hallway. Wider or both sides is a revision.
- Four restroom doorways. Count to be confirmed.
- **Foot traffic only.** Debris carts or other rolling loads need hardboard in the wheel path at added cost.
- Protection stays down through the phased work. **$600 per additional install/removal cycle.**
- Sales tax not included (~$55 on $795 of material).

## Open questions issued to the GC

1. Confirm the number of restroom doorways in the first set.
2. Confirm whether protection stays down between phases or comes up each time.

## Schedule

Installed **Thursday 11/5**, ahead of demolition, so none of the Friday 11/6 – Sunday 11/8 production
window is spent on setup. Hard stop Monday 11/9 — no demolition during school hours.

---

## Outcome

**Pending.** Issued 11/3/2026. Update this file when Scorpio returns the revised contract, and record
actual material cost and actual man-hours against the estimate above — that is the estimated-vs-actual
pair this file exists to produce.

| | Estimated | Actual |
|---|---:|---:|
| Material | $795 | *pending* |
| Labor MH | 33 | *pending* |
| Labor $ | $825 | *pending* |
| Direct cost | $1,620 | *pending* |
| Contract value | $3,000 | *pending* |
