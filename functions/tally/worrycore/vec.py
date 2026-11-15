import numpy as np


def normalize(X):
    """Rows to unit length (cosine similarity becomes a dot product). Zero rows stay zero."""
    X = np.asarray(X, dtype=np.float32)
    if X.ndim == 1:
        X = X[None, :]
    n = np.linalg.norm(X, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return X / n


def cos_matrix(A, B):
    """Cosine similarity between every row of A and every row of B (inputs need not be normalized)."""
    return normalize(A) @ normalize(B).T


def robust_z(x):
    """Robust z-score using the median and MAD, so thresholds don't depend on the embedding model's scale."""
    x = np.asarray(x, dtype=np.float64)
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826
    if mad < 1e-9:
        mad = np.std(x) or 1.0
    return (x - med) / mad
