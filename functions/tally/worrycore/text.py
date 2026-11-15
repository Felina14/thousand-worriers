"""Vectors without an embedding API: TF-IDF (unigrams + bigrams) + LSA, in numpy.

Why: Nova embeddings allow 20 requests/minute in this account (not raisable), so 1,000 worries cannot be
embedded during a live run. Classic NLP does the job for short sentences, costs nothing, runs in ~1 s and
is fully deterministic. LSA (truncated SVD) folds co-occurring words together, so "leak" and "spoil the
surprise" land close when worriers use them in the same contexts.

Usage (two views):
    (W,) = vectorize([worry_texts_with_cause_tags])                                  # clustering: LSA 128-d
    S, F, G = vectorize([failure_texts, fact_texts, GENERIC_WORRIES], dims=0)        # screening: raw TF-IDF
    state = analyse(worries, W, F, G, params=TFIDF_PARAMS, screen_vecs=S)
Tip: append each worry's short root-cause tag (if the swarm returns one) to its text: "failure || cause".
"""
import re

import numpy as np

STOP = set("""a an the and or but if then than so to of in on at by for with from into onto about over under
again further once here there when where why how all any both each few more most other some such no nor not only
own same too very can will just don should now is are was were be been being have has had having do does did doing
i me my myself we our ours you your yours he him his she her hers it its they them their what which who whom this
that these those am would could might may must shall also get gets got even still yet up down out off ever every
someone something anyone anything everyone everything one ones""".split())

_SUFFIX = ("ingly", "edly", "ing", "ed", "ies", "es", "s", "ly", "ment", "ness")


def _stem(w):
    for s in _SUFFIX:
        if len(w) > len(s) + 3 and w.endswith(s):
            return w[: -len(s)] + ("y" if s == "ies" else "")
    return w


def tokens(text):
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    toks = [_stem(w.strip("'")) for w in words if w not in STOP and len(w) > 2]
    return toks + [a + "_" + b for a, b in zip(toks, toks[1:])]


def vectorize(groups, dims=128, min_df=2, seed=0):
    """groups: list of lists of strings. Returns one (n_i, d) float32 array per group (rows L2-normalised).
    The vocabulary and IDF are fitted on all groups together so every group shares one space."""
    docs = [tokens(t) for g in groups for t in g]
    df = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    vocab = {t: i for i, t in enumerate(sorted(t for t, c in df.items() if c >= min_df or "_" not in t))}
    n = len(docs)
    if not vocab or n == 0:
        return [np.zeros((len(g), 1), np.float32) for g in groups]
    X = np.zeros((n, len(vocab)), np.float32)
    for r, d in enumerate(docs):
        for t in d:
            j = vocab.get(t)
            if j is not None:
                X[r, j] += 1.0
    X = np.log1p(X)                                            # sublinear tf
    idf = np.log((1 + n) / (1 + np.array([df[t] for t in vocab], np.float32))) + 1.0
    X *= idf
    X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
    if dims and dims < min(X.shape):
        U, S, _ = np.linalg.svd(X, full_matrices=False)       # LSA
        X = U[:, :dims] * S[:dims]
        X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
    out, i = [], 0
    for g in groups:
        out.append(X[i:i + len(g)].astype(np.float32))
        i += len(g)
    return out
