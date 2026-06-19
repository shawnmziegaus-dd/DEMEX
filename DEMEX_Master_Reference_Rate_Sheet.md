# DEMEX — Master Reference Rate Sheet (v1)

**Company:** Disassemble Dynamics, Inc. (subsidiary of Ryan's Flooring of North FL) · Open-shop selective demolition, Florida
**Built:** 2026-06-19 · **Sources reconciled:** 92-project history JSON (outcomes) · 27 Drive bid sheets (lump-sum pricing) · 1 takeoff doc (quantities) · 1 AIA G703 pay-app (line values) · Gmail ITBs + ITB import (requested SOW / GC layer)
**Author:** automated multi-source reconciliation (Drive + Gmail connectors, account `Admin@disassembledynamics.com`)

---

## ⚠️ Read this first — what this corpus can and cannot give

The bid sheets are **lump-sum** (Base Bid + lump-sum Alternates). They contain a narrative scope, **but no line-item quantities, unit rates, labor hours, material costs, burden, or markup.** The history JSON's `estCosts` are all **zero**. Quantities exist in only **one** takeoff doc.

**Therefore:**
- ✅ **Recoverable:** scope-level price benchmarks, a handful of true unit rates (where a quantity is stated), standard charges, win/loss margin signals, and pricing-consistency errors.
- ❌ **NOT recoverable from this data:** per-unit **labor burden** and **material markup** decomposition. That requires the internal cost build-up / estimating worksheets, which are **not** in Drive. **Confidence on any "labor burden / markup" figure = NONE until those sheets are provided.** This is the single biggest data gap.

**Confidence key:** **High** = ≥4 independent points or a consistently stated rate · **Med** = 2–3 points or one clean qty÷price · **Low** = 1 point or derived from a lump sum that bundles other scope.

---

## A. Standard / general charges

| Item | Unit | Rate (variance) | Confidence | Notes |
|---|---|---|---|---|
| Additional mobilization | per mob | **$3,400** (range $1,950–$3,450, n=19) | **High** | Scorpio standard $3,400; drifts to $3,420/$3,450; Auld & White change-orders use **$1,950** (low outlier). Mode = $3,400. |
| Re-mobilization / trip+haul charge | per trip | bundled into mob | Low | Stated in SCFS#14 CO#2; not separately priced. |
| Dumpster distance allowance | allowance | trigger > 150 yd from building | n/a | Policy term, not priced. |
| Single-dumpster / "live load" surcharge | allowance | change-order | n/a | Policy term. |

## B. Concrete — cutting / sawing / trenching

| Scope | Unit | Rate (variance) | Confidence | Source |
|---|---|---|---|---|
| Wall penetration sawcut, 2'×2' opening | per EA | **$360/EA** | **Low** | Kurt St: 5 penetrations / $1,800 |
| Slab trench sawcut for underground pipe/conduit | per LF | **~$50/LF** | **Low** | Kurt St CO#1: ~50 LF / $2,500 (lump also covers 2 drywells + removal → likely overstated) |
| Concrete cutting / sawcut slab (lump, conduit/penetration) | lump | $1,800 – **$2,430** – $2,500 (n=4) | **Med** | Visionworks $2,360; Buck&Ryder trench $2,500; Kurt St $1,800/$2,500 |
| Core cutting / grinding window seals / removal for garage doors | lump | $2,250 | **Med** | Lake Tech IPS pay-app line + history note (consistent across 2 sources) |

## C. Concrete — pour-back / patching  *(true unit rate — small sample)*

| Scope | Unit | Rate (variance) | Confidence | Source |
|---|---|---|---|---|
| Slab pour-back w/ rebar dowels @30" | **per SF** | **$17.65 – $26.68 – $35.71 / SF** | **Med** | SCFS#22 CO: 170 SF @6" = **$17.65/SF**; SCFS#14 CO#3: 35 SF @4–5" = **$35.71/SF** |

> **Erratic-margin flag:** the same scope is priced **2× higher per SF on the small job** (35 SF) than the large one (170 SF). A documented small-quantity premium would explain it, but it's currently undefined — standardize a minimum-lot price + $/SF curve.

## D. Concrete — floor grinding / surface prep

| Scope | Unit | Rate (variance) | Confidence | Source |
|---|---|---|---|---|
| Grind/prep existing concrete floor for reseal/coating | lump (area-dependent) | $1,884 – **$2,629** – $8,800 (n=3) | **Med** | Alloy $1,884; Visionworks $2,629; Kurt St lab floors $8,800 (much larger area) |

> Spread is area-driven, not erratic — but no SF was recorded, so it can't be normalized to $/SF. **Capture grind SF on the next 2–3 bids to lock a $/SF rate.**

## E. Selective demolition — openings (doors / windows / overhead doors)  *(quantities from takeoff; rates blended)*

| Scope | Unit | Qty basis | Confidence | Notes |
|---|---|---|---|---|
| Door + frame + hardware — remove entire | EA | 16 EA (Kurt St D1.1) | **Blended only** | Bid is one $28,125 lump over ~13 item types → per-EA rate underdetermined |
| Door — remove, frame remains | EA | 2 EA | Blended only | — |
| Window assembly — remove + prep RO | EA | 4 EA | Blended only | — |
| Overhead/roll-up door — remove entire | EA | 12 EA | Blended only | High-frequency item; worth a dedicated unit price |
| Louvers — remove | EA | 4 EA | Blended only | — |
| **Blended job density** | per keynote callout | **$360/callout** (28,125 ÷ 78 tags) | **Low** | Useful only as a sanity ratio for similar interior jobs |

## F. Selective demolition — walls / partitions

| Scope | Unit | Qty basis | Confidence | Notes |
|---|---|---|---|---|
| Interior wall/partition — remove + patch | LF | ~107 LF (Kurt St) | Blended only | Add resilient base LF (~190 LF) per takeoff note |
| Wall cut for new door opening | per opening | 2 openings (~3 LF ea) | Blended only | Related to B: $360/EA penetration |

## G. Flooring removal

| Scope | Unit | Qty basis | Confidence | Notes |
|---|---|---|---|---|
| Resilient floor + base — remove to substrate | SF | ~836 SF measured (Kurt St) | Blended only | 3 rooms measured; 2 areas need manual trace |
| Tile floor — remove + prep substrate | SF | ~378 SF measured | Blended only | — |
| Mud-bed removal under floor tile (bathroom) | lump | $5,325 (per restroom) | Med | Lake Tech IPS CO#1/#2 (identical Men's/Women's) |
| **Flooring removal job total** | — | ~1,215 SF | Med | Only fully measured SF figure in the corpus |

## H. Ceilings

| Scope | Unit | Qty basis | Confidence | Notes |
|---|---|---|---|---|
| ACT (acoustical tile) + grid — remove | SF | ~9,500 SF (SDOC Paths, lump-embedded) | Low | Largest stated SF; inside $30,350 base |
| Gypsum/hard-lid ceiling — remove | lump | bundled | Low | Frequent base-bid item |

## I. Fixtures / accessories / millwork

| Scope | Unit | Qty basis | Confidence | Notes |
|---|---|---|---|---|
| Plumbing fixtures — remove + cap (WC, lav, shower, eyewash) | EA | 11 EA (Kurt St) | Blended only | Very common; candidate for unit price |
| Toilet partitions / accessories (dispensers, mirrors, grab bars) | room set | 1 set | Blended only | — |
| Millwork / lockers / casework — remove + grind anchors | lump | — | Blended only | Recurs on nearly every fire-station/retail bid |
| Metal removal (metal studs, roof deck) from site | lump | $900 | Med | Lake Tech IPS pay-app line |
| Pipe cut + grind flush to slab | lump | $200 | Med | Lake Tech IPS pay-app line |

## J. Sitework / exterior

| Scope | Unit | Qty basis | Confidence | Notes |
|---|---|---|---|---|
| Concrete sidewalk removal, 4" (sawcut+haul) | SF | ~1,300 SF (5 W Forsyth) + ~2,500 SF (SDOC) | Low | Both lump-embedded; not isolable |
| Chain-link fence + post foundations — remove | per location | 2 locations (Kurt St) | Blended only | — |
| Exterior canopy — remove | lump | bundled (5 W Forsyth) | Low | Lincoln Park canopy job = $24,775 (scope undocumented) |
| Bollards / corner guards — remove | EA | 9 EA | Blended only | — |
| Brick — remove + salvage for reuse | lump | $3,500 | Low | 5 W Forsyth (n=1) |
| Flagpole — remove | EA | bundled (Wildwood) | Low | — |

## K. Finish replacement (Furnish & Install ALTERNATES — *not demolition*)

| Scope | Unit | Rate (variance) | Confidence | Notes |
|---|---|---|---|---|
| Epoxy floor coating (clean/prep + coat) | lump (area-dep) | $6,300 – **$10,568** – $20,722 (n=4) | Med | Bell HS alternates; **flag the dup below** |
| ACT + LED light replacement | lump | $3,000 | Low | Bell HS Alt#4 |
| Casework / countertops / cabinets — supply+install | lump | $300 each (small rooms) | Low | Bell HS Alts #3/#5/#6 |

## L. Whole-project blended benchmarks

| Metric | Value | Source |
|---|---|---|
| Base bid — full demolition project | **$5,750 – $15,245 – $48,900** (n=19) | Drive bid sheets (non-CO) |
| Change order | $500 – $2,500 – $6,250 (n=9) | Drive bid sheets (CO) |
| **Won** bids — median | **$8,600** (8 won) | History JSON |
| **Lost** bids — median | **$16,119** (18 lost) | History JSON |
| **Submitted/open** — median | $17,685 (66) | History JSON |

> **Win/loss margin signal (high value):** wins cluster at **~$8.6k**, losses at **~$16k**, open bids at **~$17.7k**. DD's hit-rate is strongest on **smaller (<$10k) jobs**; larger bids lose roughly 2× as often. This supports the learning-loop premise — but note the current data shows the **most recent streak = mixed, not 3 losses**, so the −3% markup rule would *not* fire today.

---

## M. Flagged human errors & erratic pricing (20 flags total)

**Pricing inconsistencies (revenue risk):**
1. **Bell HS Alt #9 vs #10** — *identical description* ("clean/prep machine storage floors, flake epoxy") priced **$8,662 vs $6,300** (37% gap). Copy-paste/typo.
2. **Concrete pour-back** — $17.65/SF vs $35.71/SF for the same scope (see C). No documented small-lot premium.
3. **Mobilization drift** — $3,400 / $3,420 / $3,450 / $3,250 across sheets; **$1,950** on Auld & White COs. Not standardized.
4. **FHP Troop B Alt #1 = $13,550** with a description identical to base-bid items, yet >2× the entire $6,050 base bid — scope/qty mismatch or mispriced.
5. **Bell HS base bid** dropped **$8,600 → $5,750** (−33%) between original and "Updated" sheet — confirm which is live.

**Document-integrity errors (could send the wrong bid):**
6. **File mislabel** — Drive file titled *"Five Below, Jacksonville – MRS"* actually contains the **SCFS#14 / Scorpio** bid. High risk of sending the wrong scope.
7. **100 N. Laura St.** — carries **5 W. Forsyth "sidewalk" boilerplate** in Special Notes (wrong-project text in an interior veneer/tile scope).
8. **Visionworks** — numeric **$2,360** vs amount-in-words **"Two Thousand Three Hundred"** (legal/contract mismatch).
9. **Lake Tech Lincoln Park** — **empty inclusions** section under a $24,775 base bid (scope undocumented).
10. **Kurt St "Bathrooms"** — a furnish-&-install flooring scope filed as a "Demolition" bid sheet.

**Date / data hygiene:**
11. SCFS#22 CO and SCFS#14 CO#1 **dated before** their base bids.
12. Lincoln Park ZIP "3471" (missing digit); "Sepercenter" typo on Walmart; duplicated "Scope of Work:" headers; double signature blocks (Shawn Ziegaus / Zachery Powell) on several sheets.
13. Inconsistent "night work included" vs "not included" boilerplate across bids.

---

## N. To raise confidence to "High" (next data to capture)

1. **Internal cost build-up sheets** (labor hrs, crew rate, equipment, disposal, material cost, markup %) — *unlocks the labor-burden & markup analysis that this corpus cannot provide.*
2. **Record SF/LF/EA on every alternate** (grind SF, sidewalk SF, ceiling SF) → converts lump benchmarks into true $/unit rates.
3. **Standardize the mobilization charge** and a **small-lot pour-back premium curve**.
4. Run a few more change orders that isolate a single scope (like the pour-backs) → each one yields a clean unit rate.

*This sheet is v1 and improves automatically as more priced jobs with quantities are added.*
