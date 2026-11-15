"""Measure, don't assume.

assign_knn: map re-test worries onto the ORIGINAL themes (same scale before and after); far ones are 'new'.
compare:    before/after per theme, normalised per 1,000 screened worriers, with bootstrap intervals.
noise_check:how much the same plan varies run to run; a change counts as real only if it beats that.
stability:  how many top-5 themes reappear in an independent run (centroid matching).
swarm_health: diversity, near-duplicates, grounded share.
"""
import numpy as np

from .vec import normalize


def assign_knn(new_vecs, ref_vecs, ref_labels, k=5, min_sim=None):
    """Label each new vector by majority vote of its k nearest reference worries (cosine).
    If the mean similarity of those neighbours is below min_sim, label it -2 ('new worry').
    min_sim defaults to the 10th percentile of each reference point's similarity to its own k neighbours.
    """
    A, R = normalize(new_vecs), normalize(ref_vecs)
    ref_labels = np.asarray(ref_labels)
    k = max(1, min(k, len(R)))
    if min_sim is None:
        RR = R @ R.T
        np.fill_diagonal(RR, -1)
        kth = np.sort(RR, axis=1)[:, -k:].mean(axis=1)
        min_sim = float(np.percentile(kth, 10))
    S = A @ R.T
    nn = np.argsort(-S, axis=1)[:, :k]
    out = []
    for i in range(len(A)):
        sims = S[i, nn[i]]
        if sims.mean() < min_sim:
            out.append(-2)
            continue
        labs, counts = np.unique(ref_labels[nn[i]], return_counts=True)
        out.append(int(labs[np.argmax(counts)]))
    return out, min_sim


def _totals(labels, sev, weights, top):
    labels, sev, weights = np.asarray(labels), np.asarray(sev, float), np.asarray(weights, float)
    n = weights.sum() or 1.0
    per = {}
    for l in top:
        m = labels == l
        per[int(l)] = {"count": float(weights[m].sum()), "per1000": float(weights[m].sum() * 1000 / n),
                       "sev_weighted": float((sev[m] * weights[m]).sum() * 1000 / n)}
    m_new = labels == -2
    return per, {"total_sev_per1000": float((sev * weights).sum() * 1000 / n),
                 "new_count": float(weights[m_new].sum()), "n": float(n)}


def compare(before, after, top, B=1000, seed=7):
    """before/after: dicts {labels, severities, weights} (after labels from assign_knn). top: top-5 labels.
    Returns per-theme before/after per 1,000 with 90% bootstrap intervals for the after-minus-before change,
    plus totals and the count of new worries the fixes created."""
    pb, tb = _totals(before["labels"], before["severities"], before["weights"], top)
    pa, ta = _totals(after["labels"], after["severities"], after["weights"], top)
    rng = np.random.default_rng(seed)

    def boot(d):
        lab, sev, w = (np.asarray(d["labels"]), np.asarray(d["severities"], float), np.asarray(d["weights"], float))
        p = w / w.sum()
        out = np.zeros((B, len(top) + 1))
        for b in range(B):
            s = rng.choice(len(lab), size=len(lab), p=p)
            for j, l in enumerate(top):
                out[b, j] = (lab[s] == l).mean() * 1000
            out[b, -1] = sev[s].mean() * 1000
        return out

    db, da = boot(before), boot(after)
    diff = da - db
    themes = {}
    for j, l in enumerate(top):
        themes[int(l)] = {"before": round(pb[l]["per1000"], 1), "after": round(pa[l]["per1000"], 1),
                          "change": round(pa[l]["per1000"] - pb[l]["per1000"], 1),
                          "ci90": [round(float(np.percentile(diff[:, j], 5)), 1),
                                   round(float(np.percentile(diff[:, j], 95)), 1)]}
    total = {"before": round(tb["total_sev_per1000"], 1), "after": round(ta["total_sev_per1000"], 1),
             "change": round(ta["total_sev_per1000"] - tb["total_sev_per1000"], 1),
             "ci90": [round(float(np.percentile(diff[:, -1], 5)), 1), round(float(np.percentile(diff[:, -1], 95)), 1)],
             "new_worries": int(round(ta["new_count"]))}
    return {"themes": themes, "total": total}


def noise_check(cmp, noise_cmp=None):
    """Mark each change real / within noise.
    cmp: output of compare(original, retest). noise_cmp: optional compare(original, same-plan-again).
    Real = the 90% interval excludes 0 AND |change| exceeds the run-to-run change for that theme (if known)."""
    def verdict(c, n):
        lo, hi = c["ci90"]
        excl = lo > 0 or hi < 0
        beats = n is None or abs(c["change"]) > abs(n["change"])
        return "real" if excl and beats else "within noise"
    out = {"themes": {}, "total": None}
    for l, c in cmp["themes"].items():
        n = noise_cmp["themes"].get(l) if noise_cmp else None
        out["themes"][l] = verdict(c, n)
    out["total"] = verdict(cmp["total"], noise_cmp["total"] if noise_cmp else None)
    return out


def stability(centroids_a, top_a, centroids_b, top_b, min_sim=0.8):
    """How many of run A's top-5 themes reappear in run B's top 5 (greedy centroid matching)."""
    if not top_a or not top_b:
        return {"matched": 0, "of": len(top_a), "pairs": []}
    A = normalize([centroids_a[l] for l in top_a])
    Bm = normalize([centroids_b[l] for l in top_b])
    S = A @ Bm.T
    pairs, ua, ub = [], set(), set()
    for flat in np.argsort(-S, axis=None):
        i, j = divmod(int(flat), S.shape[1])
        if i in ua or j in ub or S[i, j] < min_sim:
            continue
        ua.add(i); ub.add(j)
        pairs.append((int(top_a[i]), int(top_b[j]), round(float(S[i, j]), 3)))
    return {"matched": len(pairs), "of": len(top_a), "pairs": pairs}


def swarm_health(vecs, dup_cos=0.92, sample=400, seed=7):
    """Diversity = mean pairwise cosine distance on a sample; near-duplicate share."""
    X = normalize(vecs)
    if len(X) < 2:
        return {"diversity": 0.0, "near_duplicate_pct": 0.0}
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), size=min(sample, len(X)), replace=False)
    S = X[idx] @ X[idx].T
    iu = np.triu_indices(len(idx), 1)
    div = float((1 - S[iu]).mean())
    np.fill_diagonal(S, -1)
    dup = float((S.max(axis=1) >= dup_cos).mean() * 100)
    return {"diversity": round(div, 3), "near_duplicate_pct": round(dup, 1)}
