#!/usr/bin/env python3
"""DEMEX learning loop -- historical win/loss markup adjustment.

Reads bid history (a DEMEX export file, or a raw list of project objects) and
computes how the *target markup* on the NEXT estimate should move based on the
recent win/loss record.

v1 rule (matches the office policy):
    If the last N bids were LOST in a row (default N = 3), shave M percentage
    points (default M = 3) off the target markup for the next estimate.

The result is consumed by pricing_engine.py (via demex_bridge.py) and is also
runnable on its own from the terminal:

    python learning_loop.py --input demex_full_history_import.json \
        --export-json markup_adjustment.json

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger("demex.learning")

# --- policy constants (centralised for easy tuning) ------------------------- #
STREAK_THRESHOLD = 3      # consecutive losses that trigger a markup cut
MARKUP_SHAVE_PCT = 3.0    # percentage points removed from target markup
# A "decided" bid is one we know the outcome of.
WON = "won"
LOST = "lost"


def _projects_from(data) -> list[dict]:
    """Accept either a full DEMEX file ({projects:[...]}) or a bare list."""
    if isinstance(data, dict):
        return data.get("projects") or []
    if isinstance(data, list):
        return data
    return []


def _decided_chronological(projects: list[dict]) -> list[dict]:
    """Return only won/lost projects, oldest -> newest.

    Ordering key prefers an explicit decision/submission date, then creation
    date, then falls back to original order for anything undated.
    """
    decided = [p for p in projects
               if (p.get("status") or p.get("win_loss_status")) in (WON, LOST)]

    def keyf(p):
        return (p.get("awardedAt") or p.get("submittedAt")
                or p.get("createdAt") or p.get("dueAt") or "")

    # Stable sort keeps undated items in their incoming order.
    return sorted(decided, key=keyf)


def _outcome(p: dict) -> str:
    st = p.get("status") or p.get("win_loss_status")
    return WON if st == WON else LOST


def compute_adjustment(projects: list[dict],
                       threshold: int = STREAK_THRESHOLD,
                       shave: float = MARKUP_SHAVE_PCT,
                       base_markup: Optional[float] = None) -> dict:
    """Compute the current losing streak and the resulting markup delta."""
    decided = _decided_chronological(projects)

    streak = 0
    for p in reversed(decided):           # newest first
        if _outcome(p) == LOST:
            streak += 1
        else:
            break

    triggered = streak >= threshold
    markup_delta_pct = -float(shave) if triggered else 0.0

    if triggered:
        rationale = (f"Last {streak} decided bids were lost in a row "
                     f"(>= {threshold}); shaving {shave:g} pts off target markup "
                     f"to sharpen the next estimate.")
    elif decided:
        last = _outcome(decided[-1])
        rationale = (f"Current losing streak is {streak} "
                     f"(threshold {threshold}); last decided bid was {last}. "
                     f"No markup adjustment.")
    else:
        rationale = "No decided (won/lost) bids in history; no adjustment."

    out = {
        "losing_streak": streak,
        "threshold": threshold,
        "shave_pct": float(shave),
        "markup_delta_pct": markup_delta_pct,
        "decided_bids": len(decided),
        "rationale": rationale,
    }
    if base_markup is not None:
        out["base_markup_pct"] = float(base_markup)
        out["suggested_markup_pct"] = max(0.0, float(base_markup) + markup_delta_pct)
    return out


def _pctile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in 0..1). Small-n safe."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac)


def _amount(p: dict):
    a = p.get("bidAmount")
    try:
        a = float(a)
    except (TypeError, ValueError):
        return None
    return a if a > 0 else None


# --- segmentation: risk bands scale by project size and trade -------------- #
# Size tiers are by bid $ (the only size signal present for every project --
# history sqft is mostly 0). Tune the cut points here.
SIZE_TIERS = [("small", 0.0, 10000.0),
              ("mid", 10000.0, 25000.0),
              ("large", 25000.0, float("inf"))]
# A tier needs at least this many decided (won+lost) bids before we trust its
# OWN bands; below that it falls back to the global bands (and says so).
MIN_TIER_N = 4


def size_tier_name(amount: float) -> str:
    """Name of the size tier a $ amount falls in."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return SIZE_TIERS[0][0]
    for name, lo, hi in SIZE_TIERS:
        if lo <= amount < hi:
            return name
    return SIZE_TIERS[-1][0]


def _trade(p: dict):
    """Best-available trade/scope label for a project (None if unknown)."""
    for k in ("trade", "category", "scope_type", "scope"):
        v = p.get(k)
        if v:
            return str(v)
    return None


def price_bands(projects: list[dict], lo: float = 0.0,
                hi: float = float("inf"), trade: Optional[str] = None) -> dict:
    """Win/loss price clustering -> a competitive band for the next bid.

    Builds the $-clusters of past wins vs losses so the cockpit can flag a
    candidate bid that is drifting into the 'priced ourselves out' zone.

    Optionally restrict to a bid-size window [lo, hi) and/or a trade/scope whose
    label contains `trade` (case-insensitive) -- this is what lets the risk
    metrics scale by Project Size and Trade Category instead of one global band.

    Anchored on MEDIANS, not tail percentiles: won/lost $-distributions overlap
    heavily (a few high-$ wins would otherwise drag a p75 ceiling above the loss
    cluster and mislabel losing bids as safe). Medians keep the bands monotonic.

    competitive_ceiling = WON median  (at/below here we reliably win)
    resistance_line     = midpoint(won median, lost median) (market pushes back)
    danger_line         = LOST median (at/above here bids reliably lose)
    """
    def keep(p):
        a = _amount(p)
        if a is None or not (lo <= a < hi):
            return None
        if trade:
            t = _trade(p)
            if not t or trade.lower() not in t.lower():
                return None
        return a

    won = sorted(a for p in projects
                 if _outcome_status(p) == WON and (a := keep(p)) is not None)
    lost = sorted(a for p in projects
                  if _outcome_status(p) == LOST and (a := keep(p)) is not None)

    def stats(v):
        if not v:
            return None
        return {
            "n": len(v),
            "min": v[0], "p25": round(_pctile(v, .25), 2),
            "median": round(_pctile(v, .5), 2),
            "p75": round(_pctile(v, .75), 2), "max": v[-1],
        }

    bands = {"won": stats(won), "lost": stats(lost),
             "n_decided": len(won) + len(lost)}
    if won and lost:
        wm, lm = _pctile(won, .5), _pctile(lost, .5)
        bands["competitive_ceiling"] = round(wm, 2)
        bands["resistance_line"] = round((wm + lm) / 2, 2)
        bands["danger_line"] = round(lm, 2)
        bands["win_loss_separation"] = round(lm - wm, 2)
    return bands


def tiered_bands(projects: list[dict], trade: Optional[str] = None) -> dict:
    """Global bands + per-size-tier bands (the dynamic, size-scaled risk model).

    A tier with fewer than MIN_TIER_N decided bids falls back to the global
    bands so the cockpit never shows a confident colour off 1-2 data points.
    """
    glob = price_bands(projects, trade=trade)
    by_size = {}
    for name, lo, hi in SIZE_TIERS:
        seg = price_bands(projects, lo, hi, trade=trade)
        rng = [lo, None if hi == float("inf") else hi]
        n = seg.get("n_decided", 0)
        sep = seg.get("win_loss_separation")
        # A tier is trustworthy only with enough decided bids AND a real
        # win<loss separation (small samples can invert, e.g. a few pricey wins
        # above the loss median -> meaningless colour). Otherwise fall back.
        if n < MIN_TIER_N or "competitive_ceiling" not in seg:
            reason = "thin"
        elif sep is None or sep <= 0:
            reason = "inverted"
        else:
            reason = None
        if reason:
            fb = dict(glob)
            fb.update({"basis": "fallback-global", "fallback_reason": reason,
                       "tier_n": n, "range": rng})
            by_size[name] = fb
        else:
            seg.update({"basis": "segment", "tier_n": n, "range": rng})
            by_size[name] = seg
    return {
        "global": glob,
        "by_size": by_size,
        "min_tier_n": MIN_TIER_N,
        "tiers": [[n, lo, (None if hi == float("inf") else hi)]
                  for n, lo, hi in SIZE_TIERS],
        "trade": trade,
    }


def _outcome_status(p: dict) -> str:
    return p.get("status") or p.get("win_loss_status") or ""


def assess_candidate(amount: float, bands: dict) -> dict:
    """Classify a candidate bid $ against the win/loss bands."""
    ceil = bands.get("competitive_ceiling")
    resist = bands.get("resistance_line")
    danger = bands.get("danger_line")
    if ceil is None:
        return {"candidate_amount": amount, "zone": "unknown",
                "flag": "Not enough decided history to judge competitiveness."}
    if not (ceil < danger):           # bands must increase; guard against inversion
        return {"candidate_amount": round(float(amount), 2), "zone": "unknown",
                "competitive_ceiling": ceil, "resistance_line": resist,
                "danger_line": danger,
                "flag": "Win/loss bands lack clean separation here; too little "
                        "signal to colour this bid."}
    if amount <= ceil:
        zone, flag = "competitive", "At or below where we typically win. Hold."
    elif amount <= resist:
        zone, flag = "watch", ("Above our usual winning range but below market "
                               "resistance. Sharpen if the job is a priority.")
    elif amount <= danger:
        zone, flag = "stretch", ("Past market-resistance midpoint. Win odds drop "
                                 "sharply here.")
    else:
        zone, flag = "priced-out", ("In the zone where bids consistently LOSE. "
                                    "Re-scope or cut markup before submitting.")
    return {"candidate_amount": round(float(amount), 2), "zone": zone,
            "competitive_ceiling": ceil, "resistance_line": resist,
            "danger_line": danger, "flag": flag}


def assess_in_context(amount: float, projects: list[dict],
                      size_hint: Optional[float] = None,
                      tier: Optional[str] = None,
                      trade: Optional[str] = None) -> dict:
    """Score a candidate bid against the bands for its size tier + trade.

    The tier is chosen by (in order): an explicit `tier` name, a `size_hint`
    ($ size of the job being estimated, independent of the amount being tested),
    else the candidate amount itself.
    """
    tb = tiered_bands(projects, trade=trade)
    pick = tier or size_tier_name(size_hint if size_hint is not None else amount)
    bands = tb["by_size"].get(pick) or tb["global"]
    res = assess_candidate(amount, bands)
    res["size_tier"] = pick
    res["band_basis"] = bands.get("basis", "global")
    res["tier_n"] = bands.get("tier_n", bands.get("n_decided"))
    res["trade"] = trade
    return res


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Compute win/loss markup adjustment from DEMEX bid history.")
    p.add_argument("--input", required=True,
                   help="DEMEX export JSON ({projects:[...]}) or a raw list of "
                        "project objects.")
    p.add_argument("--export-json", metavar="PATH",
                   help="Write the adjustment result to this JSON file "
                        "(also printed to stdout).")
    p.add_argument("--project",
                   help="Optional id of the project being estimated (recorded "
                        "in the output for traceability; does not change math).")
    p.add_argument("--threshold", type=int, default=STREAK_THRESHOLD,
                   help=f"Consecutive losses that trigger a cut (default "
                        f"{STREAK_THRESHOLD}).")
    p.add_argument("--shave", type=float, default=MARKUP_SHAVE_PCT,
                   help=f"Markup points to remove when triggered (default "
                        f"{MARKUP_SHAVE_PCT}).")
    p.add_argument("--base-markup", type=float, default=None,
                   help="Current target markup %% (to compute suggested markup).")
    p.add_argument("--candidate-amount", type=float, default=None,
                   help="A proposed bid $ to score against the win/loss bands.")
    p.add_argument("--size-hint", type=float, default=None,
                   help="Expected $ size of the job, used to pick the size tier "
                        "for scoring (defaults to the candidate amount).")
    p.add_argument("--size-tier", default=None,
                   help="Force a size tier by name (small/mid/large) instead of "
                        "inferring it.")
    p.add_argument("--trade", default=None,
                   help="Restrict bands to a trade/scope label (substring match).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def run(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    projects = _projects_from(data)
    result = compute_adjustment(projects, args.threshold, args.shave,
                                args.base_markup)
    if args.project:
        result["project"] = args.project

    # Win/loss price clustering -> competitive bands (for cockpit accuracy checks)
    # price_bands = global (back-compat); tiered_bands = size/trade-scaled model.
    result["price_bands"] = price_bands(projects, trade=args.trade)
    result["tiered_bands"] = tiered_bands(projects, trade=args.trade)
    if args.candidate_amount is not None:
        result["candidate_assessment"] = assess_in_context(
            args.candidate_amount, projects, size_hint=args.size_hint,
            tier=args.size_tier, trade=args.trade)

    log.info("Streak=%d  markup_delta=%+.1f pts  (%d decided bids)",
             result["losing_streak"], result["markup_delta_pct"],
             result["decided_bids"])

    text = json.dumps(result, indent=2)
    if args.export_json:
        Path(args.export_json).write_text(text, encoding="utf-8")
        log.info("Wrote adjustment -> %s", args.export_json)
    # Always echo to stdout so callers can pipe it.
    print(text)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
