"""The call sequence app.py follows. Every return value is JSON-serialisable (store it in S3 as state).

    1. analyse(worries, vecs, fact_vecs, generic_vecs)            → state  (+ state["ask"]["judge"])
       LLM: for each cluster in ask.judge → {name, definition, plausible, controllability}
    2. rank(state, worries, vecs, judgements, want_angle)          → ranking (+ ranking["ask"]["fixes"])
       LLM: for each theme in ask.fixes → 2 candidate fixes (schema in INTERFACE.md); optional fix embeddings
    3. plan_card(ranking, candidates, moments, budget, fix_vecs)   → card skeleton (+ card["ask"]["check"])
       LLM: fix checker on chosen fixes; Nova Pro writes the revised plan text from card["chosen"]
    4. retest(parent_state, parent_ranking, parent_vecs, new_worries, new_vecs, fact_vecs, generic_vecs, noise=None)
       → before/after per theme + verdicts. Run it once with the SAME plan to get `noise`, then with the revised plan.
"""
import numpy as np

from .screen import screen
from .cluster import cluster, representatives
from .risk import score_clusters, bootstrap, find_quiet, affected_voice
from .plan import response_for, select_fixes, build_timeline
from .retest import assign_knn, compare, noise_check, swarm_health

JUDGED_OUT = -3   # members of clusters the plausibility judge rejected
NEW = -2          # re-test worries far from every original theme


def analyse(worries, vecs, fact_vecs, generic_vecs, params=None, screen_vecs=None):
    """vecs: vectors used for clustering/quiet/re-test (e.g. failure + cause tag).
    screen_vecs: optional vectors of the failure text alone, used for grounding/genericness (recommended with
    TF-IDF, so a cause tag like 'bad weather' doesn't make a specific worry look generic)."""
    V = np.asarray(vecs, dtype=np.float32)
    SV = np.asarray(screen_vecs, dtype=np.float32) if screen_vecs is not None else V
    scr = screen(worries, SV, fact_vecs, generic_vecs, params, dedup_vecs=V)
    keep = scr["keep"]
    weights = [scr["weight"][i] for i in keep]
    cl = cluster(V[keep], weights) if keep else {"labels": [], "k": 0, "centroids": {}, "sizes": {}, "cut_distance": None}
    reps = representatives(V[keep], cl["labels"], cl["centroids"]) if keep else {}
    valid_idx = [r["i"] for r in scr["rows"] if r["valid"]]
    health = swarm_health(V[valid_idx]) if valid_idx else {}
    health["grounded_pct"] = round(100 * scr["funnel"]["grounded"] / max(1, scr["funnel"]["valid"]), 1)
    judge = [{"label": lab, "size": cl["sizes"][lab],
              "examples": [worries[keep[p]]["failure"] for p in pos]}
             for lab, pos in reps.items()]
    return {
        "funnel": scr["funnel"], "rows": scr["rows"], "keep": keep, "weights": weights,
        "labels": cl["labels"], "centroids": {str(k): v for k, v in cl["centroids"].items()},
        "sizes": {str(k): v for k, v in cl["sizes"].items()}, "k": cl["k"], "cut_distance": cl["cut_distance"],
        "health": health, "ask": {"judge": judge},
    }


def _centroids(state):
    return {int(k): v for k, v in state["centroids"].items()}


def rank(state, worries, vecs, judgements, want_angle=None, top_n=5):
    """judgements: {label: {name, definition, plausible: bool, controllability: controllable|partial|none}}."""
    V = np.asarray(vecs, dtype=np.float32)
    keep, w = state["keep"], state["weights"]
    judgements = {int(k): v for k, v in judgements.items()}
    labels = []
    for l in state["labels"]:
        j = judgements.get(int(l))
        labels.append(JUDGED_OUT if (l >= 0 and j is not None and not j.get("plausible", True)) else int(l))
    labels = np.asarray(labels)
    m = labels != JUDGED_OUT
    kw = [worries[keep[i]] for i in range(len(keep)) if m[i]]
    kl = labels[m].tolist()
    kwt = [w[i] for i in range(len(keep)) if m[i]]
    kv = V[[keep[i] for i in range(len(keep)) if m[i]]]
    sev = [int(x["severity"]) for x in kw]

    scores = score_clusters(kl, sev, kwt)
    conf = bootstrap(kl, sev, kwt, top=top_n)
    order = sorted(scores, key=lambda l: -scores[l]["risk"])
    top = order[:top_n]
    cents = _centroids(state)
    quiet = find_quiet(kw, kv, kl, kwt, top, cents, want_angle=want_angle)
    affected = affected_voice(kw, kv, kwt, want_angle) if want_angle else None

    funnel = dict(state["funnel"])
    funnel["plausible"] = int(round(sum(kwt)))
    funnel["judged_out_clusters"] = sorted({int(l) for l in state["labels"]
                                            if l >= 0 and not judgements.get(int(l), {}).get("plausible", True)})

    themes = []
    for rank_i, l in enumerate(order):
        j = judgements.get(l, {})
        members = [x for x, lab in zip(kw, kl) if lab == l]
        members.sort(key=lambda x: -int(x["severity"]))
        themes.append({
            "label": l, "rank": rank_i + 1, "name": j.get("name", f"Theme {l}"), "definition": j.get("definition", ""),
            "controllability": j.get("controllability", "partial"), "response": response_for(j.get("controllability")),
            **scores[l], **conf.get(l, {}), "examples": [x["failure"] for x in members[:3]],
            "early_signs": [x.get("early_sign", "") for x in members[:3] if x.get("early_sign")],
        })
    q = None
    if quiet:
        qw = kw[quiet["pos"]]
        q = {"worry": qw["failure"], "character": qw.get("character"), "angle": qw.get("angle"),
             "moment": qw.get("moment"), "severity": int(qw["severity"]), "count": quiet["count"],
             "isolation": quiet["isolation"], "label": quiet["label"], "early_sign": qw.get("early_sign", "")}
    ask = [{"label": t["label"], "name": t["name"], "response": t["response"], "examples": t["examples"]}
           for t in themes[:top_n]]
    if affected:
        ask.append({"label": "affected", "name": affected["worry"], "response": "prevent", "examples": affected["examples"]})
    if q:
        ask.append({"label": "quiet", "name": q["worry"], "response": "prevent", "examples": [q["worry"]]})
    return {"themes": themes, "top": top, "quiet": q, "affected": affected, "funnel": funnel,
            "labels_after_judge": labels.tolist(),
            "severities_kept": [int(worries[keep[i]]["severity"]) for i in range(len(keep))],
            "ask": {"fixes": ask}}


def plan_card(ranking, candidates, moments, budget=8, fix_vecs=None, include_quiet=True):
    """candidates: [{id, themes:[label or 'quiet'], action, owner, moment, effort, response, backup?, early_sign?}].
    The quiet worry is treated as a theme with the risk of the #1 theme × 0.5 (it is chosen to matter, not to be loud)."""
    risks = {t["label"]: t["risk"] for t in ranking["themes"] if t["label"] in ranking["top"]}
    if include_quiet and ranking.get("quiet") and ranking["themes"]:
        risks[-99] = ranking["themes"][0]["risk"] * 0.5
    if ranking.get("affected") and ranking["themes"]:
        risks[-98] = ranking["themes"][0]["risk"] * 0.6   # what the people affected want: always worth a fix
    cands = []
    for c in candidates:
        c = dict(c)
        c["themes"] = [-99 if t == "quiet" else -98 if t == "affected" else int(t) for t in c.get("themes", [])]
        cands.append(c)
    sel = select_fixes(cands, risks, budget=budget, fix_vecs=fix_vecs)
    chosen = [c for c in cands if c["id"] in sel["chosen"]]
    for c in chosen:
        c["themes"] = ["quiet" if t == -99 else "affected" if t == -98 else t for t in c["themes"]]
    timeline = build_timeline(chosen, moments)
    return {"chosen": timeline, "selection": sel,
            "ask": {"check": [{"id": c["id"], "action": c["action"]} for c in timeline]}}


def retest(parent_state, parent_ranking, parent_vecs, new_worries, new_vecs, fact_vecs, generic_vecs,
           noise=None, params=None, new_screen_vecs=None):
    """Score a re-run on the PARENT's themes. Pass noise=<retest(...) on the unchanged plan> to get verdicts."""
    PV = np.asarray(parent_vecs, dtype=np.float32)
    pkeep = parent_state["keep"]
    plabels = np.asarray(parent_ranking["labels_after_judge"])
    pm = plabels != JUDGED_OUT
    ref_vecs = PV[[pkeep[i] for i in range(len(pkeep)) if pm[i]]]
    ref_labels = plabels[pm]
    NV = np.asarray(new_vecs, dtype=np.float32)
    NSV = np.asarray(new_screen_vecs, dtype=np.float32) if new_screen_vecs is not None else NV
    scr = screen(new_worries, NSV, fact_vecs, generic_vecs, params, dedup_vecs=NV)
    nkeep = scr["keep"]
    nw = [scr["weight"][i] for i in nkeep]
    nlab, min_sim = assign_knn(NV[nkeep], ref_vecs, ref_labels) if nkeep else ([], None)
    top = parent_ranking["top"]
    before = {"labels": ref_labels.tolist(), "severities": [parent_ranking["severities_kept"][i] for i in range(len(pkeep)) if pm[i]],
              "weights": [parent_state["weights"][i] for i in range(len(pkeep)) if pm[i]]}
    after = {"labels": nlab, "severities": [int(new_worries[i]["severity"]) for i in nkeep], "weights": nw}
    cmp = compare(before, after, top)
    verdicts = noise_check(cmp, noise["compare"] if noise else None)
    new_items = [new_worries[nkeep[i]]["failure"] for i, l in enumerate(nlab) if l == NEW]
    return {"funnel": scr["funnel"], "compare": cmp, "verdicts": verdicts, "knn_min_sim": min_sim,
            "new_worry_examples": new_items[:8], "labels": nlab, "keep": nkeep, "weights": nw}
