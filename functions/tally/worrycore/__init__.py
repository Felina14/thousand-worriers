"""worrycore: the deterministic half of 1,000 Worriers. "AI generates, maths decides."

Pure Python + numpy. No AWS calls, no network, no randomness without a seed.
The Lambda (app.py) does all Bedrock I/O and passes plain lists/arrays in.
See INTERFACE.md for the call sequence.
"""
from .vec import normalize, cos_matrix
from .screen import screen, GENERIC_WORRIES, TFIDF_PARAMS
from .text import vectorize, tokens
from .cluster import cluster, representatives
from .risk import score_clusters, bootstrap, find_quiet
from .plan import response_for, select_fixes, build_timeline
from .retest import assign_knn, compare, noise_check, stability, swarm_health
from .pipeline import analyse, rank, plan_card, retest

__all__ = [
    "normalize", "cos_matrix", "screen", "GENERIC_WORRIES", "TFIDF_PARAMS", "vectorize", "tokens", "cluster", "representatives",
    "score_clusters", "bootstrap", "find_quiet", "response_for", "select_fixes", "build_timeline",
    "assign_knn", "compare", "noise_check", "stability", "swarm_health",
    "analyse", "rank", "plan_card", "retest",
]
