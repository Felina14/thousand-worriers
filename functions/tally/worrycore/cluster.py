"""Themes by meaning: weighted average-linkage (UPGMA) agglomerative clustering on cosine distance.

Each kept worry enters with its dedup weight, so a phrase that 40 worriers repeated pulls as hard as 40.
The cut is chosen where the merge distance jumps the most (the elbow), within a sensible range of theme counts.
Implemented in numpy: ~1-3 s for 1,000 points, no scikit-learn.
"""
import numpy as np

from .vec import normalize


def _linkage(X, w):
    """Return merges [(a, b, dist, new_id)] for weighted average linkage. Ids >= n are merged clusters."""
    n = len(X)
    D = 1.0 - np.clip(X @ X.T, -1.0, 1.0)
    D = D.astype(np.float64)
    np.fill_diagonal(D, np.inf)
    size = np.asarray(w, dtype=np.float64).copy()
    ids = list(range(n))
    active = np.ones(n, bool)
    merges = []
    nxt = n
    for _ in range(n - 1):
        flat = np.argmin(D)
        a, b = divmod(int(flat), n)
        d = D[a, b]
        if not np.isfinite(d):
            break
        sa, sb = size[a], size[b]
        # Lance–Williams update for average linkage: new row = weighted mean of the two rows.
        new = (sa * D[a] + sb * D[b]) / (sa + sb)
        D[a, :] = new
        D[:, a] = new
        D[a, a] = np.inf
        D[b, :] = np.inf
        D[:, b] = np.inf
        active[b] = False
        size[a] = sa + sb
        merges.append((ids[a], ids[b], float(d), nxt))
        ids[a] = nxt
        nxt += 1
    return merges


def _cut(n, merges, k):
    """Labels 0..k-1 after replaying merges until k clusters remain (union-find)."""
    parent = list(range(n + len(merges)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    steps = max(0, n - k)
    for a, b, _d, new in merges[:steps]:
        parent[find(a)] = new
        parent[find(b)] = new
    roots = {}
    labels = np.empty(n, int)
    for i in range(n):
        r = find(i)
        labels[i] = roots.setdefault(r, len(roots))
    return labels


def cluster(vecs, weights, k_min=6, k_max=14, min_share=0.01):
    """Cluster kept worries.

    vecs: (M, D) embeddings of the kept (unique) worries. weights: their dedup weights (len M).
    Returns dict with labels (len M; -1 = 'other', clusters smaller than min_share of total weight),
    k, cut_distance, centroids {label: unit vector list}, sizes {label: weighted size}.
    """
    X = normalize(vecs)
    w = np.asarray(weights, dtype=np.float64)
    M = len(X)
    if M == 0:
        return {"labels": [], "k": 0, "cut_distance": None, "centroids": {}, "sizes": {}}
    if M <= k_min:
        labels = np.arange(M)
        k, cut_d = M, None
    else:
        merges = _linkage(X, w)
        heights = np.array([m[2] for m in merges])
        # number of clusters remaining after merge j is M-1-j; the gap before merge j decides the cut.
        best_k, best_gap = None, -1.0
        for k in range(max(2, k_min), min(k_max, M - 1) + 1):
            j = M - k  # merge that would reduce k -> k-1
            if j <= 0 or j >= len(heights):
                continue
            gap = heights[j] - heights[j - 1]
            if gap > best_gap:
                best_k, best_gap = k, gap
        k = best_k or min(k_max, M)
        labels = _cut(M, merges, k)
        cut_d = float(heights[M - k - 1]) if 0 <= M - k - 1 < len(heights) else None

    total = w.sum()
    sizes = {}
    for lab in np.unique(labels):
        sizes[int(lab)] = float(w[labels == lab].sum())
    small = {lab for lab, s in sizes.items() if s < max(2.0, min_share * total)}
    labels = np.array([-1 if l in small else int(l) for l in labels])
    # Renumber big clusters by size (0 = largest).
    big = sorted({l for l in labels if l >= 0}, key=lambda l: -sizes[l])
    remap = {old: new for new, old in enumerate(big)}
    labels = np.array([remap.get(l, -1) for l in labels])

    centroids, out_sizes = {}, {}
    for lab in sorted(set(labels.tolist())):
        m = labels == lab
        c = (X[m] * w[m, None]).sum(axis=0)
        nrm = np.linalg.norm(c) or 1.0
        centroids[int(lab)] = (c / nrm).tolist()
        out_sizes[int(lab)] = float(w[m].sum())
    return {"labels": labels.tolist(), "k": len(big), "cut_distance": cut_d,
            "centroids": centroids, "sizes": out_sizes}


def representatives(vecs, labels, centroids, m=5):
    """Indices (into vecs) of the m worries closest to each cluster centroid. Used for naming and judging."""
    X = normalize(vecs)
    labels = np.asarray(labels)
    out = {}
    for lab, c in centroids.items():
        if lab == -1:
            continue
        idx = np.where(labels == lab)[0]
        if len(idx) == 0:
            continue
        sims = X[idx] @ np.asarray(c)
        out[int(lab)] = [int(i) for i in idx[np.argsort(-sims)][:m]]
    return out
