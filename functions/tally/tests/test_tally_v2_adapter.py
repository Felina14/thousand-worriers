"""Adapter test for tally_v2 (Bedrock + S3 stubbed, synthetic worries). Run: cd functions/tally && python3 tests/test_tally_v2_adapter.py"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.update(TABLE_NAME="t", LITE_MODEL="lite", PRO_MODEL="pro", BUCKET_NAME="b", AWS_DEFAULT_REGION="us-east-1")
import app  # noqa: E402
import tally_v2  # noqa: E402

THEMES = {
    "surprise leak": ["Her best friend lets the surprise slip at lunch.", "A hiding friend posts a story and she sees it.", "Someone texts her about the candles."],
    "bad weather": ["Heavy rain floods the beach an hour before.", "Wind blows out every candle at sunset.", "A storm warning closes the beach."],
    "lost ring": ["The ring falls into the sand while kneeling.", "The ring box is left at home.", "The ring slips out of a jacket pocket."],
    "phone battery": ["The filming friend's phone dies before the moment.", "The phone records sideways and blurry.", "He forgets to press record."],
}


def synthetic(n=300, seed=1):
    rnd = random.Random(seed)
    ws = []
    for i in range(n):
        cause = rnd.choice(list(THEMES))
        ws.append({"seq": i + 1, "character": f"c{i % 25}", "angle": "what the people affected actually want" if i % 7 == 0 else f"a{i % 8}",
                   "moment": f"m{i % 5}", "failure": rnd.choice(THEMES[cause]) + f" ({rnd.randint(1, 999)})",
                   "severity": rnd.randint(2, 5), "early_sign": "sign", "cause": cause})
    return ws


REPRESENTS = {"value": "yes"}
CRITIC = {"fail": False}
SPOT = {"person": "her", "worry": "W1"}


def fake(usage, model, system, prompt, max_tokens, check, temperature=0.2, tries=3):
    if "find what a plan overlooks" in system:   # spotlight cards
        return {"cards": [{"person": SPOT["person"], "plan_quote": "My proposal plan", "need": "a private moment", "unknown": "her wish",
                           "question": "Does she want a crowd?", "worry": SPOT["worry"]}]}
    if "For EACH fix, name the failure" in prompt:   # routing: no answer keeps the critic's verdict
        return {"answers": []}
    if "List every person, role" in prompt:   # invented-cause + representativeness
        return {"things": [], "question": "", "represents": REPRESENTS["value"]}
    if "extract facts" in system:
        return {"facts": ["proposal at the beach", "best friend brings her", "twelve friends hide", "friend records on phone"]}
    if "judge and name" in system:
        ex = prompt.split("grouped together by meaning:\n- ")[1].split("\n")[0] if "grouped together by meaning" in prompt else prompt
        return {"name": "Theme " + str(abs(hash(ex)) % 997), "definition": "d", "plausible": True, "controllability": "controllable"}
    if "candidates" in prompt:
        return {"question": "Does she want this?", "candidates": [
            {"action": "Ask her best friend.", "owner": "me", "moment": "m0", "effort": "low", "backup": "b", "early_sign": "s", "how_to_check": "Her best friend replies yes in writing", "when": "2 days before", "days_before": 2},
            {"action": "Keep the ring zipped.", "owner": "me", "moment": "m1", "effort": "low", "backup": "b", "early_sign": "s", "how_to_check": "Ring box found in the zipped pocket at 6 PM", "when": "", "days_before": 0}]}
    if "review advice" in system:
        return {"checks": [{"i": 0, "risk": "yes", "why": "telling more people"}]}
    if "find shared concerns" in system:
        return {"concerns": ["She would rather hear this in private."]}
    if "compare risks strictly" in system:
        n = prompt.count("\n") - prompt.split("Worries:\n")[1].count("\n\n") if "Worries:\n" in prompt else 0
        lines = [l for l in prompt.split("Worries:\n")[1].split("\n\n")[0].split("\n") if l[:1].isdigit()]
        return {"answers": [{"i": int(l.split(":")[0]), "a": "yes" if "her" in l.lower() else "no"} for l in lines]}
    if "planning advisor" in system:
        return {"bullets": [{"text": "merged", "changed": True}]}
    if "check risks against" in system:
        if "Proposed fix:" in prompt:
            if CRITIC["fail"]:
                raise RuntimeError("critic down")
            return {"breaks": "m", "targets": "yes", "concrete": "yes", "check_ok": "yes", "respects": "yes", "why": ""}
        if "matched none of the original" in prompt:
            return {"concerns": [{"concern": "c", "mechanism": "m", "related_change_quote": ""}]}
        if "Original risk" in prompt:   # re-test assessment
            return {"assessment": "addressed_in_plan", "basis_quote": "x", "reason": "r", "still_open": ""}
        if "matched none of the original" in prompt:
            return {"concerns": [{"concern": "c", "mechanism": "m", "related_change_quote": ""}]}
        return {"failure": "f", "mechanism": "m", "evidence": ["My proposal plan"], "assumptions": ["a"],
                "assessment": "supported", "control_quote": "", "contradiction_reason": "", "question": "Is it?"}
    raise RuntimeError(system)


def _setup():
    app.ask_retry = fake
    store = {}
    tally_v2.s3.put_object = lambda **k: store.update({k["Key"]: k["Body"]})

    class Body:
        def __init__(self, key):
            self.key = key

        def read(self):
            return store[self.key]

    def get_object(**k):
        if k["Key"] not in store:
            raise tally_v2.s3.exceptions.NoSuchKey({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": Body(k["Key"])}

    tally_v2.s3.get_object = get_object
    return store


def test_analyse_and_retest():
    _setup()
    REPRESENTS["value"] = "yes"
    ws = synthetic()
    grid = {"moments": ["m0", "m1", "m2", "m3", "m4"], "facts": []}
    res, labels = tally_v2.analyse_run(app, app.Usage(), "P", "My proposal plan.", ws, grid)
    json.dumps(res)
    assert res["tally"] == "v2" and len(res["top5"]) >= 3, res["top5"]
    assert set(labels) == {str(w["seq"]) for w in ws}
    assert all(t["fix"] for t in res["top5"]) and res["plan_card"], "every top theme gets a fix; plan card not empty"
    assert any(c.get("introduces_risk") for c in res["plan_card"]), "fix checker flag is carried"
    ids = {t["id"] for t in res["top5"]}
    assert any(v in ids for v in labels.values()), "huddle labels use the same ids as the top 5"
    # Phase 1: the number on a finding is the size of its TEXT CLUSTER and says so
    assert all(t["count_basis"] == "text_cluster" and t["cluster_count"] == t["count"] and t["cluster_name"] for t in res["top5"])
    assert res["quiet"] is None or res["quiet"]["count_basis"] == "text_cluster_of_quiet_worry"
    assert all(t["validation"] == "checked" for t in res["top5"])
    a = res["affected"]
    assert a and a["count"] is None and a["spotlight"] and a["spotlight"][0]["seq"] == ws[[w["angle"] for w in ws].index(app.WANT)]["seq"], a
    assert all(t["card"] and t["card"]["assessment"] == "supported" and t["card"]["evidence"] == ["My proposal plan"] for t in res["top5"])
    assert res["grounding"]["none"] == len(ws), "synthetic worries carry no quotes"
    rt, rl = tally_v2.retest_run(app, app.Usage(), "R", "P", "My proposal plan.\n\nChanges made to the plan:\n- Ask her best friend first",
                                 synthetic(seed=2), ws, res, reports=["Something else: the planner says this is handled"])
    json.dumps(rt)
    # "x" is not in the plan or the changes, so "addressed" must be downgraded by code
    assert rt["assessments"] and all(v["assessment"] == "unresolved" for v in rt["assessments"].values()), rt["assessments"]
    assert rt["new_concerns"] and rt["new_concerns"][0]["related_change"] == ""
    assert rt["per1000"] and rt["comparison"] and all(c["verdict"] in ("real", "within noise") for c in rt["comparison"])



def test_card_that_does_not_represent_its_group_keeps_the_group():
    """Phase 2 (run d9db470a: an SSL card stood for 112 worries about on-call mistakes and the group was set aside)."""
    _setup()
    REPRESENTS["value"] = "no"
    try:
        ws = synthetic()
        res, _ = tally_v2.analyse_run(app, app.Usage(), "P", "My proposal plan.", ws, {"moments": ["m0", "m1", "m2", "m3", "m4"], "facts": []})
    finally:
        REPRESENTS["value"] = "yes"
    assert res["top5"] and not res["set_aside"], "no group is removed because its summary drifted"
    assert all(t["needs_review"] and t["validation"] == "needs_review" and not t["fix"] for t in res["top5"])
    assert all(t["name"] == t["cluster_name"] for t in res["top5"]), "a drifting card does not rename its group"
    assert all("Review this group" in t["card"]["question"] for t in res["top5"])


def test_retest_reads_the_revision_ledger():
    """Phase 3: the re-test reads the same ledger the Plan Card shows; questions are passed apart from changes."""
    _setup()
    REPRESENTS["value"] = "yes"
    ws = synthetic()
    res, _ = tally_v2.analyse_run(app, app.Usage(), "P", "My proposal plan.", ws, {"moments": ["m0", "m1", "m2", "m3", "m4"], "facts": []})
    ledger = {"revision_id": "rev-1", "original_plan": "My proposal plan.",
              "plan_lines": [{"id": "P1", "text": "My proposal plan.", "status": "original"}],
              "accepted_changes": [{"id": "C1", "action": "Ask her best friend first", "status": "proposed"}],
              "open_questions": [{"id": "Q1", "question": "Does she want a crowd?"}], "planner_reports": [],
              "bullets": [{"text": "My proposal plan.", "changed": False}, {"text": "Ask her best friend first", "changed": True}],
              "effective_plan_text": "My proposal plan.\n\nProposed changes (accepted by the planner, planned, not done yet):\n- Ask her best friend first"}
    rt, _ = tally_v2.retest_run(app, app.Usage(), "R2", "P", ledger["effective_plan_text"], synthetic(seed=3), ws, res, revision=ledger)
    assert rt["revision_id"] == "rev-1" and rt["revised_plan"] == ledger["bullets"], "the shown plan is the ledger's, no second rewrite"
    assert rt["accepted_changes"] == ["Ask her best friend first"] and rt["open_questions"] == ["Does she want a crowd?"]


def test_failed_critic_leaves_fixes_unchecked():
    """Checkpoint 2: a quality check that does not run never counts as a pass."""
    _setup()
    CRITIC["fail"] = True
    try:
        res, _ = tally_v2.analyse_run(app, app.Usage(), "P", "My proposal plan.", synthetic(), {"moments": ["m0", "m1", "m2", "m3", "m4"], "facts": []})
    finally:
        CRITIC["fail"] = False
    shown = [t for t in res["top5"] if t["fix"]]
    assert shown and all(t["fix_unchecked"] for t in shown), [(t["fix"], t.get("fix_unchecked")) for t in res["top5"]]


def test_spotlight_card_needs_a_worry_about_its_person():
    """Checkpoint 2: a card about the best friend cannot cite a worry about the girlfriend."""
    _setup()
    SPOT.update(person="her best friend", worry="W1")
    try:
        ws = synthetic()
        res, _ = tally_v2.analyse_run(app, app.Usage(), "P", "My proposal plan.", ws, {"moments": ["m0", "m1", "m2", "m3", "m4"], "facts": []})
    finally:
        SPOT.update(person="her", worry="W1")
    a = res["affected"]
    for c in (a or {}).get("spotlight") or []:
        assert tally_v2._mentions(c["worry"], "friend"), c


def main():
    test_analyse_and_retest()
    test_card_that_does_not_represent_its_group_keeps_the_group()
    test_retest_reads_the_revision_ledger()
    test_failed_critic_leaves_fixes_unchecked()
    test_spotlight_card_needs_a_worry_about_its_person()
    print("PASS test_tally_v2_adapter")


if __name__ == "__main__":
    main()
