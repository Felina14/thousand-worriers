"""From risks to a plan, the way risk management does it (ISO 31000 / PMI-style risk responses).

1. Each top theme is tagged by the LLM with how much the planner controls it: controllable | partial | none.
   That fixes the response type:   controllable → PREVENT · partial → REDUCE · none → PREPARE (backup + early sign).
2. The LLM writes 2 structured candidate fixes per theme (action, owner, moment, effort, response, themes).
3. Selection is a small budget problem: cover every top theme first (best value fix each), then add more fixes
   by expected risk removed per unit of effort until the effort budget is spent. Near-identical fixes are dropped.
4. The re-test later measures what the chosen fixes actually did. The efficacy weights below are a prior for
   choosing, not a claim; the claim comes from the re-test.
"""
import numpy as np

from .vec import normalize

RESPONSE = {"controllable": "prevent", "partial": "reduce", "none": "prepare"}
EFFICACY = {"prevent": 0.7, "reduce": 0.4, "prepare": 0.25}   # prior only; the re-test measures reality
EFFORT = {"low": 1, "medium": 2, "high": 3}


def response_for(controllability):
    return RESPONSE.get((controllability or "").strip().lower(), "reduce")


def select_fixes(candidates, risks, budget=8, fix_vecs=None, dup_cos=0.9):
    """Pick fixes.

    candidates: list of dicts {id, themes: [label,...], action, owner, moment, effort, response}.
    risks: {label: risk} for the top themes. budget: total effort points (low=1, medium=2, high=3).
    fix_vecs: optional embeddings of each candidate's action text (to drop near-duplicates).
    Returns {chosen: [ids], value: {id: expected risk removed}, cost, budget, uncovered: [labels]}.
    """
    val, cost = {}, {}
    for c in candidates:
        eff = EFFICACY.get(c.get("response"), 0.3)
        val[c["id"]] = sum(risks.get(int(t), 0.0) for t in c.get("themes", [])) * eff
        cost[c["id"]] = EFFORT.get((c.get("effort") or "medium").lower(), 2)

    dupes = set()
    if fix_vecs is not None and len(candidates) > 1:
        X = normalize(fix_vecs)
        S = X @ X.T
        order = sorted(range(len(candidates)), key=lambda i: -val[candidates[i]["id"]])
        kept = []
        for i in order:
            if any(S[i, j] >= dup_cos for j in kept):
                dupes.add(candidates[i]["id"])
            else:
                kept.append(i)

    pool = [c for c in candidates if c["id"] not in dupes]
    chosen, spent, covered = [], 0, set()
    # Pass 1: one best-value fix per top theme, most risky theme first.
    for lab in sorted(risks, key=lambda l: -risks[l]):
        opts = [c for c in pool if int(lab) in [int(t) for t in c.get("themes", [])] and c["id"] not in chosen]
        if not opts:
            continue
        best = max(opts, key=lambda c: val[c["id"]] / cost[c["id"]])
        if spent + cost[best["id"]] <= budget:
            chosen.append(best["id"])
            spent += cost[best["id"]]
            covered.update(int(t) for t in best.get("themes", []))
    # Pass 2: greedy by value per effort.
    for c in sorted(pool, key=lambda c: -val[c["id"]] / cost[c["id"]]):
        if c["id"] in chosen or spent + cost[c["id"]] > budget:
            continue
        chosen.append(c["id"])
        spent += cost[c["id"]]
        covered.update(int(t) for t in c.get("themes", []))
    return {"chosen": chosen, "value": {k: round(v, 4) for k, v in val.items()}, "cost": spent,
            "budget": budget, "dropped_duplicates": sorted(dupes),
            "uncovered": [int(l) for l in risks if int(l) not in covered]}


def build_timeline(fixes, moments):
    """Order fixes by when they must happen, using the plan's own moments (grid order = time order)."""
    rank = {m: i for i, m in enumerate(moments)}
    return sorted(fixes, key=lambda f: (rank.get(f.get("moment"), len(moments)), f.get("id", "")))
