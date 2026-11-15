"""End-to-end on realistic sentences with TF-IDF/LSA vectors and swarm cause tags."""
import os, sys, random
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import worrycore as wc

THEMES = {
    "surprise leak": ["{who} lets slip the surprise in the group chat {when}", "{who} posts about the proposal online {when} and she sees it",
                      "{who} accidentally mentions the ring to her {when}", "the surprise leaks because {who} talks about it {when}"],
    "bad weather": ["rain at Elliot's Beach ruins the candles {when}", "strong wind on the beach blows out the candles {when}",
                    "a storm forces everyone off the beach {when}", "the sunset is hidden by heavy clouds {when}"],
    "recording failure": ["{who} recording runs out of phone storage {when}", "the phone recording misses her face {when}",
                          "{who} forgets to press record {when}", "the video is too dark to see anything {when}"],
    "dinner booking": ["the restaurant loses the dinner booking {when}", "the restaurant cannot seat fourteen people {when}",
                       "dinner booking at 8 PM is cancelled by the restaurant {when}"],
    "ring problem": ["the ring falls into the sand {when}", "the ring is left at home {when}", "the ring box is spotted in my pocket {when}"],
}
WHO = ["her best friend", "one of the twelve friends", "the friend recording", "a cousin"]
WHEN = ["the night before", "an hour before", "during the walk to the beach", "at the main moment"]
PLAN = ("I'm proposing to my girlfriend on Saturday 6:30 PM at Elliot's Beach at sunset. Her best friend brings her pretending "
        "it is a surprise birthday dinner. Twelve friends hide behind the food stalls with candles. A friend records on his phone. "
        "The ring is with me. Dinner booked at a restaurant nearby for 8 PM. Her parents are told on Sunday.")
FACTS = ["proposal at Elliot's Beach at sunset", "best friend brings her for a surprise birthday dinner", "twelve friends hide with candles",
         "a friend records on his phone", "the ring is with me", "dinner booked at a restaurant for 8 PM", "parents told on Sunday", PLAN]


def build(seed=0, sizes=None):
    rnd = random.Random(seed)
    sizes = sizes or {"surprise leak": 330, "bad weather": 220, "recording failure": 160, "dinner booking": 110, "ring problem": 90}
    ws = []
    for cause, n in sizes.items():
        for _ in range(n):
            t = rnd.choice(THEMES[cause]).format(who=rnd.choice(WHO), when=rnd.choice(WHEN))
            ws.append({"failure": t[0].upper() + t[1:], "cause": cause, "severity": rnd.randint(2, 5), "angle": "x",
                       "character": "c", "moment": "m", "seq": len(ws)})
    for _ in range(4):
        ws.append({"failure": "She would have preferred a private moment instead of twelve friends and a camera watching",
                   "cause": "her wishes", "severity": 5, "angle": "want", "character": "c", "moment": "m", "seq": len(ws)})
    for _ in range(40):   # the 'what she wants' angle: most say public pressure, a few say other things
        ws.append({"failure": rnd.choice(["She may not want a public proposal in front of twelve friends and a camera",
                   "She might feel pressured to say yes with everyone watching and recording"]),
                   "cause": "public pressure", "severity": rnd.randint(4, 5), "angle": "want", "character": "c", "moment": "m", "seq": len(ws)})
    for _ in range(10):
        ws.append({"failure": "She would rather her parents knew before the proposal, not on Sunday after", "cause": "parents first",
                   "severity": 3, "angle": "want", "character": "c", "moment": "m", "seq": len(ws)})
    ws.append({"failure": "Have you confirmed the dinner booking details?", "cause": "booking", "severity": 4, "angle": "want",
               "character": "c", "moment": "m", "seq": len(ws)})
    for _ in range(25):
        ws.append({"failure": "Visa fees for the international flight are much higher than expected", "cause": "visa cost",
                   "severity": 4, "angle": "x", "character": "c", "moment": "m", "seq": len(ws)})
    for _ in range(20):
        ws.append({"failure": rnd.choice(["Something might go wrong on the day", "Things may not go as planned at all"]),
                   "cause": "general", "severity": 3, "angle": "x", "character": "c", "moment": "m", "seq": len(ws)})
    return ws


def run(ws):
    texts = [f"{w['failure']} || {w['cause']} {w['cause']}" for w in ws]
    (W,) = wc.vectorize([texts])                                   # clustering view: LSA, with cause tags
    S, F, G = wc.vectorize([[w["failure"] for w in ws], FACTS, wc.GENERIC_WORRIES], dims=0)  # screening view: raw TF-IDF
    st = wc.analyse(ws, W, F, G, params=wc.TFIDF_PARAMS, screen_vecs=S)
    # Simulated plausibility judge (the real one is Nova Lite on 5 central examples): visa fees aren't in this plan.
    judge = {j["label"]: {"name": str(j["label"]), "controllability": "partial",
                          "plausible": not any("Visa" in e for e in j["examples"])} for j in st["ask"]["judge"]}
    rk = wc.rank(st, ws, W, judge, want_angle="want")
    return st, rk


def test_realistic_sentences():
    ws = build()
    st, rk = run(ws)
    f = rk["funnel"]
    labels = np.array(rk["labels_after_judge"])
    final = {ws[st["keep"][i]]["cause"] for i in range(len(st["keep"])) if labels[i] != -3}
    assert "visa cost" not in final, f"invented visa worries leaked {f}"
    assert "general" not in {ws[i]["cause"] for i in st["keep"]}, f"generic worries leaked {f}"
    # each top theme should be dominated by one cause
    labels = np.array(rk["labels_after_judge"])
    for l in rk["top"]:
        causes = [ws[st["keep"][i]]["cause"] for i in np.where(labels == l)[0]]
        top_share = max(causes.count(c) for c in set(causes)) / len(causes)
        assert top_share > 0.8, (l, top_share)
    assert rk["quiet"] and "private" in rk["quiet"]["worry"]
    real = sum(1 for w in ws if w["cause"] not in ("visa cost", "general", "booking"))
    assert rk["affected"] and ("public" in rk["affected"]["worry"] or "pressured" in rk["affected"]["worry"]), rk["affected"]
    assert not any(ws[i]["failure"].endswith("?") for i in st["keep"]), "questions should fail validity"
    assert f["plausible"] >= 0.9 * real, f"too many real worries dropped: {f}"
    print("funnel", f, "k", st["k"], "quiet", rk["quiet"]["worry"][:40], rk["quiet"]["count"])


if __name__ == "__main__":
    test_realistic_sentences(); print("PASS test_realistic_sentences")
