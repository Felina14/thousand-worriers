"""Gate 1–4 of the quality funnel. Gate 5 (plausibility) is an LLM judgement applied later, per cluster.

    generated → valid → grounded → specific → unique

Every worry keeps its row; gates only set flags, so the UI can show exactly what was dropped and why.
"""
import numpy as np

from .vec import normalize, robust_z

# Embedded by the caller (same model as the worries) and passed in as generic_vecs.
GENERIC_WORRIES = [
    "Something might go wrong.",
    "Things may not go as planned.",
    "There could be unexpected problems.",
    "Someone might be late.",
    "The weather could be bad.",
    "People might not like it.",
    "It could cost more than expected.",
    "Technical issues could happen.",
    "Communication could break down.",
    "Plans can change at the last minute.",
    "Someone might forget something.",
    "It might not be perfect.",
]

DEFAULTS = {
    "min_words": 5,
    "max_words": 60,
    "ground_z": -2.0,       # robust z below which a worry is "off-plan" (relative to this run)
    "ground_floor": 0.15,   # ...and absolute cosine below which it is off-plan regardless
    "generic_margin": 0.04, # generic if similarity to generic exemplars beats plan similarity by this margin
    "generic_floor": 0.80,  # ...or if it is this close to a generic exemplar outright
    "dup_cos": 0.92,        # near-duplicates merge (they still count as agreement)
    "generic_ground_low": 1.0,  # margin rule only applies when plan-grounding is below this (1.0 = always)
}


# Use these gate settings when vectors come from worrycore.text (TF-IDF/LSA) instead of a neural embedding.
# Lexical vectors can't judge grounding per worry ("the video is too dark" shares no word with "a friend records on
# his phone"), so in TF-IDF mode per-worry grounding is off and invented premises are removed at cluster level by the
# plausibility judge (LLM, on representatives). Generic filler is still caught per worry.
TFIDF_PARAMS = {"ground_floor": -1.0, "ground_z": -99.0, "generic_margin": 0.25, "generic_floor": 0.60,
                "generic_ground_low": 0.12, "dup_cos": 0.85}


def _valid(w, p):
    t = (w.get("failure") or "").strip()
    n = len(t.split())
    try:
        s = int(w.get("severity"))
    except (TypeError, ValueError):
        return False, "no severity"
    if not t:
        return False, "empty"
    if t.endswith("?"):
        return False, "a question, not a failure"
    if n < p["min_words"]:
        return False, "too short"
    if n > p["max_words"]:
        return False, "too long"
    if not 1 <= s <= 5:
        return False, "severity out of range"
    return True, ""


def screen(worries, vecs, fact_vecs, generic_vecs, params=None, dedup_vecs=None):
    """Run gates 1–4.

    worries: list of dicts with at least failure, severity (and seq, character, angle, moment).
    vecs: (N, D) vectors of each worry's failure text (used for grounding and genericness).
    dedup_vecs: optional (N, D) vectors used for duplicate merging (e.g. failure + cause tag); default vecs.
    fact_vecs: (F, D) embeddings of the plan facts.
    generic_vecs: (G, D) embeddings of GENERIC_WORRIES.

    Returns dict: rows (per-worry flags and scores), funnel (counts per gate), keep (indices that passed),
    weight (for each kept index: how many worries it stands for after dedup), dup_of (index → representative).
    """
    p = dict(DEFAULTS, **(params or {}))
    N = len(worries)
    V = normalize(vecs)
    Fv = normalize(fact_vecs)
    Gv = normalize(generic_vecs)

    rows = [{"i": i, "valid": False, "grounded": False, "specific": False, "unique": False, "reason": ""}
            for i in range(N)]

    valid = np.zeros(N, bool)
    for i, w in enumerate(worries):
        ok, why = _valid(w, p)
        valid[i] = ok
        rows[i]["valid"] = ok
        if not ok:
            rows[i]["reason"] = why

    ground = (V @ Fv.T).max(axis=1) if len(Fv) else np.ones(N)
    gen = (V @ Gv.T).max(axis=1) if len(Gv) else np.zeros(N)
    z = robust_z(ground[valid]) if valid.any() else np.array([])
    zfull = np.full(N, -99.0)
    zfull[valid] = z

    grounded = valid & ~((zfull < p["ground_z"]) | (ground < p["ground_floor"]))
    generic = (gen > p["generic_floor"]) | (((gen - ground) > p["generic_margin"]) & (ground < p["generic_ground_low"]))
    specific = grounded & ~generic

    for i in range(N):
        rows[i]["ground"] = round(float(ground[i]), 4)
        rows[i]["generic"] = round(float(gen[i]), 4)
        rows[i]["grounded"] = bool(grounded[i])
        rows[i]["specific"] = bool(specific[i])
        if valid[i] and not grounded[i]:
            rows[i]["reason"] = "off-plan"
        elif grounded[i] and not specific[i]:
            rows[i]["reason"] = "too generic"

    # Gate 4: greedy near-duplicate merge, highest severity first so the representative is the sharpest wording.
    idx = [i for i in range(N) if specific[i]]
    idx.sort(key=lambda i: (-int(worries[i]["severity"]), i))
    dup_of = {}
    reps = []
    DV = normalize(dedup_vecs) if dedup_vecs is not None else V
    if idx:
        S = DV[idx] @ DV[idx].T
        taken = np.zeros(len(idx), bool)
        for a in range(len(idx)):
            if taken[a]:
                continue
            twins = np.where((S[a] >= p["dup_cos"]) & ~taken)[0]
            taken[twins] = True
            reps.append(idx[a])
            for b in twins:
                dup_of[idx[b]] = idx[a]
    weight = {r: 0 for r in reps}
    for i, r in dup_of.items():
        weight[r] += 1
    for r in reps:
        rows[r]["unique"] = True
    for i, r in dup_of.items():
        if i != r:
            rows[i]["reason"] = "duplicate"

    funnel = {
        "generated": N,
        "valid": int(valid.sum()),
        "grounded": int(grounded.sum()),
        "specific": int(specific.sum()),
        "unique": len(reps),
    }
    return {"rows": rows, "funnel": funnel, "keep": reps, "weight": weight, "dup_of": dup_of}
