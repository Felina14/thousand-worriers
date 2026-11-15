"""Rank themes the way a risk register does: Risk = Likelihood × Impact, with a bootstrap confidence.

Likelihood: Laplace-smoothed share of screened worriers in the theme, (k+1)/(n+2).
Impact: weighted mean severity (1–5).
Confidence: resample the screened worriers with replacement B times; report a 90% interval for each theme's
risk and how often it stays in the top 5. Deterministic (seeded).
"""
import numpy as np

from .vec import normalize


def _expand(labels, sev, weights):
    """One entry per screened worrier (dedup weights expanded), so resampling is over people-equivalents."""
    lab, s = [], []
    for l, v, w in zip(labels, sev, weights):
        n = int(round(w))
        lab += [l] * n
        s += [v] * n
    return np.asarray(lab), np.asarray(s, dtype=np.float64)


def score_clusters(labels, severities, weights):
    """Return {label: {count, likelihood, impact, risk}} for labels >= 0 ('other' excluded from ranking)."""
    lab, sev = _expand(labels, severities, weights)
    n = len(lab)
    out = {}
    for l in sorted(set(lab.tolist())):
        if l < 0:
            continue
        m = lab == l
        k = int(m.sum())
        L = (k + 1) / (n + 2)
        I = float(sev[m].mean())
        out[int(l)] = {"count": k, "likelihood": round(L, 4), "impact": round(I, 2), "risk": round(L * I, 4)}
    return out


def bootstrap(labels, severities, weights, B=1000, top=5, seed=7):
    """Return {label: {risk_lo, risk_hi, top5_share}}: 90% interval and share of resamples in the top `top`."""
    lab, sev = _expand(labels, severities, weights)
    n = len(lab)
    ids = sorted({int(l) for l in lab if l >= 0})
    if n == 0 or not ids:
        return {}
    pos = {l: j for j, l in enumerate(ids)}
    li = np.array([pos.get(int(l), -1) for l in lab])
    rng = np.random.default_rng(seed)
    K = len(ids)
    risks = np.zeros((B, K))
    for b in range(B):
        s = rng.integers(0, n, n)
        L_lab = li[s]
        S = sev[s]
        m = L_lab >= 0
        cnt = np.bincount(L_lab[m], minlength=K).astype(np.float64)
        ssum = np.bincount(L_lab[m], weights=S[m], minlength=K)
        imp = np.divide(ssum, cnt, out=np.zeros(K), where=cnt > 0)
        risks[b] = (cnt + 1) / (n + 2) * imp
    order = np.argsort(-risks, axis=1)[:, :top]
    in_top = np.zeros(K)
    for j in range(K):
        in_top[j] = (order == j).any(axis=1).mean()
    out = {}
    for l, j in pos.items():
        out[l] = {"risk_lo": round(float(np.percentile(risks[:, j], 5)), 4),
                  "risk_hi": round(float(np.percentile(risks[:, j], 95)), 4),
                  "top5_share": round(float(in_top[j]), 3)}
    return out


def find_quiet(worries, vecs, labels, weights, top_labels, centroids, want_angle=None,
               min_severity=4, want_bonus=1.25):
    """The quiet worry: severe, and far from every top theme. The majority missed it; it may matter most.

    worries/vecs/labels/weights describe the kept (unique) worries, aligned by position.
    top_labels: the top-5 theme labels. centroids: {label: vector}.
    Score = severity × isolation (1 - max cosine to a top-5 centroid), × want_bonus for the
    'what the people affected actually want' angle. Candidates exclude worries inside a top-5 theme.
    Returns {pos, score, isolation, label, count} or None. count = weighted size of its own theme
    (or its own dedup weight if it sits in 'other').
    """
    X = normalize(vecs)
    C = np.array([centroids[l] for l in top_labels]) if top_labels else np.zeros((0, X.shape[1]))
    labels = np.asarray(labels)
    best = None
    for i, w in enumerate(worries):
        if int(labels[i]) in top_labels:
            continue
        sev = int(w["severity"])
        if sev < min_severity:
            continue
        iso = 1.0 - (float((C @ X[i]).max()) if len(C) else 0.0)
        score = sev * iso * (want_bonus if want_angle and w.get("angle") == want_angle else 1.0)
        if best is None or score > best["score"]:
            lab = int(labels[i])
            cnt = float(np.asarray(weights)[labels == lab].sum()) if lab >= 0 else float(weights[i])
            best = {"pos": i, "score": round(score, 4), "isolation": round(iso, 4), "label": lab,
                    "count": int(round(cnt))}
    return best


def affected_voice(worries, vecs, weights, want_angle, k_max=6, min_severity=1):
    """The voice of the people affected: among the worriers who were asked to think about what the people
    affected by the plan actually want (the always-present angle), find their most common concern.

    Clusters ONLY that subset, picks the cluster with the highest risk (share x mean severity), and returns
    its most central worry. Honest framing for the UI: "Of M worriers asked what <they> actually want,
    K raised: ...". Returns None if the angle is absent.
    """
    from .cluster import cluster, representatives
    idx = [i for i, w in enumerate(worries) if w.get("angle") == want_angle and int(w["severity"]) >= min_severity]
    if len(idx) < 3:
        return None
    X = normalize(np.asarray(vecs)[idx])
    wts = [weights[i] for i in idx]
    cl = cluster(X, wts, k_min=2, k_max=min(k_max, max(2, len(idx) - 1)), min_share=0.05)
    labels = np.asarray(cl["labels"])
    m_tot = float(sum(wts))
    best, best_score = None, -1.0
    for lab, size in cl["sizes"].items():
        if lab < 0:
            continue
        mem = [j for j in range(len(idx)) if labels[j] == lab]
        sev = np.average([int(worries[idx[j]]["severity"]) for j in mem], weights=[wts[j] for j in mem])
        score = (size / m_tot) * sev
        if score > best_score:
            best, best_score = (lab, mem, size, sev), score
    if best is None:
        return None
    lab, mem, size, sev = best
    reps = representatives(X, labels, {lab: cl["centroids"][lab]}, m=3)[lab]
    rep = worries[idx[reps[0]]]
    return {"worry": rep["failure"], "count": int(round(size)), "of": int(round(m_tot)),
            "avg_severity": round(float(sev), 2), "examples": [worries[idx[j]]["failure"] for j in reps],
            "character": rep.get("character"), "moment": rep.get("moment")}
