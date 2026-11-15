"""Synthetic tests for worrycore. Run: cd functions/tally && python3 -m pytest -q tests  (or python3 tests/test_worrycore.py)"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import worrycore as wc  # noqa: E402

D = 64
WANT = "what the people affected actually want"


def unit(v):
    return v / np.linalg.norm(v)


def make_world(seed=1, sizes=(300, 200, 150, 100, 60, 40), sevs=(3, 4, 3, 5, 2, 4), dup=30, quiet=3,
               offplan=50, generic=40, cluster_scale=0.6):
    world = np.random.default_rng(100)          # the plan itself is fixed across runs
    facts = [unit(world.normal(size=D)) for _ in range(9)]
    G = [unit(world.normal(size=D)) for _ in range(12)]
    rng = np.random.default_rng(seed)           # each run's worriers vary
    worries, vecs = [], []

    def add(v, sev, angle="timing", text=None):
        worries.append({"seq": len(worries), "failure": text or f"worry number {len(worries)} about the plan details",
                        "severity": int(sev), "angle": angle, "character": "c", "moment": "m",
                        "early_sign": "a sign to watch"})
        vecs.append(unit(v))

    for c, (n, s) in enumerate(zip(sizes, sevs)):
        for _ in range(n):
            add(facts[c] + cluster_scale * rng.normal(size=D) / np.sqrt(D),
                int(np.clip(round(rng.normal(s, 0.6)), 1, 5)))
    base = facts[0] + cluster_scale * rng.normal(size=D) / np.sqrt(D)
    for _ in range(dup):
        add(base + 0.01 * rng.normal(size=D), 3)
    for _ in range(quiet):
        add(facts[6] + 0.2 * rng.normal(size=D) / np.sqrt(D), 5, angle=WANT)
    for _ in range(offplan):
        add(rng.normal(size=D), 4)
    for _ in range(generic):
        add(G[rng.integers(0, 12)] + 0.2 * rng.normal(size=D) / np.sqrt(D), 3)
    add(facts[1], 3, text="short")  # invalid: too short
    return worries, np.array(vecs), np.array(facts[:7]), np.array(G), facts


def judge_all(state, plausible=lambda l: True):
    return {j["label"]: {"name": f"T{j['label']}", "definition": "", "plausible": plausible(j["label"]),
                         "controllability": ["controllable", "partial", "none"][j["label"] % 3]}
            for j in state["ask"]["judge"]}


def test_funnel_filters_offplan_generic_invalid_and_merges_dupes():
    worries, V, F, G, _ = make_world()
    st = wc.analyse(worries, V, F, G)
    f = st["funnel"]
    assert f["generated"] == len(worries)
    assert f["valid"] == len(worries) - 1
    kept_seqs = {worries[i]["seq"] for i in st["keep"]}
    n_core = 300 + 200 + 150 + 100 + 60 + 40 + 30 + 3
    off = set(range(n_core, n_core + 50))
    gen = set(range(n_core + 50, n_core + 90))
    assert not (kept_seqs & off), "off-plan worries leaked"
    assert not (kept_seqs & gen), "generic worries leaked"
    assert f["unique"] < f["specific"], "duplicates were not merged"
    assert sum(st["weights"]) == f["specific"]
    assert f["specific"] >= 0.9 * n_core


def test_clusters_recover_themes_and_rank_by_risk():
    worries, V, F, G, facts = make_world()
    st = wc.analyse(worries, V, F, G)
    assert 6 <= st["k"] <= 14
    rk = wc.rank(st, worries, V, judge_all(st), want_angle=WANT)
    assert len(rk["top"]) == 5
    # Theme 0 (330 incl. dupes, sev 3) and theme 3 (100, sev 5) must both be in the top 5.
    cents = {int(k): np.array(v) for k, v in st["centroids"].items()}
    def label_of(fact):
        return max(cents, key=lambda l: cents[l] @ fact if l >= 0 else -9)
    assert label_of(facts[0]) in rk["top"] and label_of(facts[3]) in rk["top"]
    t0 = next(t for t in rk["themes"] if t["label"] == rk["top"][0])
    assert t0["top5_share"] >= 0.95 and t0["risk_lo"] <= t0["risk"] <= t0["risk_hi"]
    risks = [t["risk"] for t in rk["themes"]]
    assert risks == sorted(risks, reverse=True)


def test_quiet_worry_is_the_isolated_severe_want_angle():
    worries, V, F, G, _ = make_world()
    st = wc.analyse(worries, V, F, G)
    rk = wc.rank(st, worries, V, judge_all(st), want_angle=WANT)
    assert rk["quiet"] is not None
    assert rk["quiet"]["angle"] == WANT and rk["quiet"]["severity"] == 5
    assert rk["quiet"]["count"] <= 5


def test_plausibility_judge_removes_a_cluster():
    worries, V, F, G, _ = make_world()
    st = wc.analyse(worries, V, F, G)
    drop = st["ask"]["judge"][0]["label"]
    rk = wc.rank(st, worries, V, judge_all(st, plausible=lambda l: l != drop), want_angle=WANT)
    assert drop not in rk["top"] and drop in rk["funnel"]["judged_out_clusters"]
    assert rk["funnel"]["plausible"] < st["funnel"]["specific"]


def test_fix_selection_covers_themes_within_budget():
    worries, V, F, G, _ = make_world()
    st = wc.analyse(worries, V, F, G)
    rk = wc.rank(st, worries, V, judge_all(st), want_angle=WANT)
    cands = []
    for t in rk["top"]:
        cands.append({"id": f"{t}a", "themes": [t], "action": f"prevent {t}", "owner": "planner",
                      "moment": "the hour before", "effort": "low", "response": "prevent"})
        cands.append({"id": f"{t}b", "themes": [t], "action": f"big fix {t}", "owner": "planner",
                      "moment": "a week before", "effort": "high", "response": "reduce"})
    cands.append({"id": "q", "themes": ["quiet"], "action": "ask her", "owner": "best friend",
                  "moment": "a week before", "effort": "low", "response": "prevent"})
    card = wc.plan_card(rk, cands, ["a week before", "the hour before", "the main moment"], budget=6)
    sel = card["selection"]
    assert sel["cost"] <= 6 and not sel["uncovered"]
    assert [c["moment"] for c in card["chosen"]] == sorted([c["moment"] for c in card["chosen"]],
                                                           key=["a week before", "the hour before"].index)
    assert any(c["id"] == "q" for c in card["chosen"]), "quiet worry fix should be chosen (cheap, matters)"


def test_retest_detects_real_drop_and_new_worries():
    worries, V, F, G, facts = make_world(seed=1)
    st = wc.analyse(worries, V, F, G)
    rk = wc.rank(st, worries, V, judge_all(st), want_angle=WANT)
    # Same plan again → noise baseline
    w2, V2, _, _, _ = make_world(seed=2)
    noise = wc.retest(st, rk, V, w2, V2, F, G)
    # Revised plan: theme 0 mostly fixed (300 → 80), plus 60 new worries near a new fact the fix introduced.
    w3, V3, _, _, _ = make_world(seed=3, sizes=(80, 200, 150, 100, 60, 40), dup=0)
    rng = np.random.default_rng(9)
    newfact = facts[8]
    for _ in range(60):
        w3.append({"seq": len(w3), "failure": "a new worry the fix created for everyone", "severity": 4,
                   "angle": "people", "character": "c", "moment": "m"})
        V3 = np.vstack([V3, unit(newfact + 0.6 * rng.normal(size=D) / np.sqrt(D))])
    F3 = np.vstack([F, newfact])
    rt = wc.retest(st, rk, V, w3, V3, F3, G, noise=noise)
    cents = {int(k): np.array(v) for k, v in st["centroids"].items()}
    l0 = max((l for l in cents if l >= 0), key=lambda l: cents[l] @ facts[0])
    assert rt["compare"]["themes"][l0]["change"] < -100
    assert rt["verdicts"]["themes"][l0] == "real"
    assert rt["compare"]["total"]["new_worries"] >= 40
    # Unchanged plan: most themes within noise
    within = sum(v == "within noise" for v in noise["verdicts"]["themes"].values())
    assert within >= 3


def test_stability_across_independent_runs():
    a = make_world(seed=11)
    b = make_world(seed=12)
    sa = wc.analyse(*a[:4]); ra = wc.rank(sa, a[0], a[1], judge_all(sa), want_angle=WANT)
    sb = wc.analyse(*b[:4]); rb = wc.rank(sb, b[0], b[1], judge_all(sb), want_angle=WANT)
    ca = {int(k): v for k, v in sa["centroids"].items()}
    cb = {int(k): v for k, v in sb["centroids"].items()}
    s = wc.stability(ca, ra["top"], cb, rb["top"])
    assert s["matched"] >= 4


def test_speed_1000_worriers_under_10s():
    worries, V, F, G, _ = make_world()
    t = time.time()
    st = wc.analyse(worries, V, F, G)
    wc.rank(st, worries, V, judge_all(st), want_angle=WANT)
    assert time.time() - t < 10


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            t = time.time()
            try:
                fn(); print(f"PASS {name} ({time.time()-t:.1f}s)")
            except AssertionError as e:
                fails += 1; print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
