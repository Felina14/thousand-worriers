"""Behaviour tests for grounding.py (pure rules; no Bedrock). Run: cd functions/tally && python3 tests/test_grounding.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import grounding as gr  # noqa: E402

PLAN = ("We're cutting over our production PostgreSQL database to a new Aurora cluster this Saturday, 2:00-2:40 AM IST. "
        "Writes are paused via a feature flag; we have a rollback plan and a snapshot from Friday night. "
        "Customer support starts at 7 AM. The mobile app caches tokens for 24 hours.")


def test_quote_check():
    assert gr.find_quote("customer support starts at 7 am", PLAN) == "Customer support starts at 7 AM"
    assert gr.find_quote("Customer   support starts at 7 AM.", PLAN) == "Customer support starts at 7 AM"
    assert gr.find_quote("snapshot from Friday night", PLAN) == "snapshot from Friday night"
    assert gr.find_quote("support was briefed on Friday", PLAN) is None, "fabricated quote"
    assert gr.find_quote("Aurora", PLAN) is None, "one word is not evidence"
    assert gr.find_quote("2:00-2:40 AM IST", PLAN) == "2:00-2:40 AM IST"
    assert gr.find_quote("rollback plan — and a snapshot", PLAN) == "rollback plan and a snapshot", "dash ignored"
    assert gr.check_quotes(["snapshot from Friday night", "SNAPSHOT from friday night", "made up words here", "the mobile app caches tokens"], PLAN) == \
        ["snapshot from Friday night", "The mobile app caches tokens"], "dedup, two max, fake dropped"


def test_ground_worries():
    ws = [{"quotes": ["Customer support starts at 7 AM"]}, {"quotes": ["support got an email"]},
          {"quotes": [], "missing": "whether support was briefed"}, {"quotes": []}]
    c = gr.ground_worries(ws, PLAN)
    assert [w["ground"] for w in ws] == ["quoted", "ungrounded", "omission", "none"], [w["ground"] for w in ws]
    assert ws[1]["evidence"] == [] and c == {"quoted": 1, "ungrounded": 1, "omission": 1, "none": 1}
    ev = gr.member_evidence(ws)
    assert ev["n"] == 4 and ev["n_quoted"] == 1 and ev["quotes"] == ["Customer support starts at 7 AM"]


def test_contradicted_needs_defeating_control():
    # model claims contradicted but gives no checked safeguard quote -> unverified
    c = gr.enforce_card({"assessment": "contradicted", "evidence": ["rollback plan"], "control_quote": "we tested the restore yesterday",
                         "contradiction_reason": "restore was tested", "question": "q"}, PLAN)
    assert c["assessment"] != "contradicted" and c["control_quote"] == ""
    # a real quoted safeguard with a reason stays contradicted
    c = gr.enforce_card({"assessment": "contradicted", "control_quote": "Writes are paused via a feature flag",
                         "contradiction_reason": "the flag stops writes during the switch", "question": "q"}, PLAN)
    assert c["assessment"] == "contradicted" and c["control_quote"] == "Writes are paused via a feature flag"
    # supported needs a checked quote
    c = gr.enforce_card({"assessment": "supported", "evidence": ["support team was not told"], "question": "q"}, PLAN)
    assert c["assessment"] == "unverified" and c["evidence"] == []
    c = gr.enforce_card({"assessment": "nonsense", "question": "q"}, PLAN)
    assert c["assessment"] == "unverified"
    # status is decided by code: a checked quote makes it grounded, whatever the model said
    c = gr.enforce_card({"assessment": "unverified", "evidence": ["Customer support starts at 7 AM"], "question": "q"}, PLAN)
    assert c["assessment"] == "supported"
    assert gr.vague_check("Confirm understanding in a meeting") and gr.vague_check("Ensure it works")
    assert not gr.vague_check("Restore log shows the Friday snapshot restored to a test cluster")


def test_false_reassurance_and_unsupported_completion():
    reports = ["Rollback: the planner says the rollback is tested and ready"]
    # the model calls it addressed, quoting only the planner's statement -> reported, not addressed
    r = gr.enforce_retest({"assessment": "addressed_in_plan", "basis_quote": "the rollback is tested and ready"}, PLAN, [], reports)
    assert r["assessment"] == "reported_covered_unverified", r
    # "rollback plan" exists in the plan, but the model says unresolved: code never upgrades it to addressed;
    # with this finding's own statement it becomes "reported", without one it stays unresolved
    r = gr.enforce_retest({"assessment": "unresolved", "basis_quote": "rollback plan"}, PLAN, [], reports)
    assert r["assessment"] == "reported_covered_unverified"
    r = gr.enforce_retest({"assessment": "unresolved", "basis_quote": "rollback plan"}, PLAN, [], reports, own_reports=[])
    assert r["assessment"] == "unresolved"
    # a statement about another finding does not count for this one
    r = gr.enforce_retest({"assessment": "reported_covered_unverified"}, PLAN, [], reports, own_reports=[])
    assert r["assessment"] == "unresolved", r
    # this finding's own statement and no change: reported, decided in code
    r = gr.enforce_retest({"assessment": "unresolved"}, PLAN, [], reports, own_reports=reports)
    assert r["assessment"] == "reported_covered_unverified", r
    # a bad basis quote falls back to the related change, checked word for word
    r = gr.enforce_retest({"assessment": "partially_addressed", "basis_quote": "C1. test the flag", "related_change": "Test the feature flag on Thursday"},
                          PLAN, ["Test the feature flag on Thursday"], [])
    assert r["assessment"] == "partially_addressed" and r["basis"] == "Test the feature flag on Thursday", r
    # the model copies the list label and a bad basis: the related change still counts once checked
    r = gr.enforce_retest({"assessment": "partially_addressed", "basis_quote": "flag test (planned)", "related_change": "C1. Test the feature flag on Thursday."},
                          PLAN, ["Test the feature flag on Thursday."], [])
    assert r["assessment"] == "partially_addressed", r
    # reported without any reports is not possible
    r = gr.enforce_retest({"assessment": "reported_covered_unverified"}, PLAN, [], [])
    assert r["assessment"] == "unresolved"


def test_fix_can_address_and_introduce():
    changes = ["Restore the Friday snapshot to a test cluster on Thursday and record its ID"]
    r = gr.enforce_retest({"assessment": "addressed_in_plan", "basis_quote": "Restore the Friday snapshot to a test cluster"}, PLAN, changes, [])
    assert r["assessment"] == "addressed_in_plan" and r["basis_from_change"]
    new = gr.enforce_new({"concerns": [
        {"concern": "The wrong snapshot ID gets restored", "mechanism": "two snapshots exist", "related_change_quote": "record its ID"},
        {"concern": "Monitoring misses the slow queries", "mechanism": "m", "related_change_quote": "monitor every query"},
    ]}, changes)
    assert new[0]["related_change"] == changes[0], "both can be true: the original risk addressed, a new concern from that fix"
    assert new[1]["related_change"] == "", "no cause claimed without a checked quote"


def test_quiet_finding_still_gets_card():
    ev = gr.member_evidence([{"ground": "quoted", "evidence": ["Customer support starts at 7 AM"]}])
    assert ev["n"] == 1 and ev["quotes"], "a one-worry finding still carries its evidence"



def _fake(answer):
    return lambda usage, model, system, prompt, max_tokens, check, temperature=0.0: answer(prompt)


def test_route_fixes_keeps_only_fixes_on_their_own_finding():
    cards = {"C0": {"failure": "flag left on", "mechanism": "flag -> writes on"}, "C4": {"failure": "rollback not ready", "mechanism": "new engineer -> untrained -> rollback fails"}}
    fixes = [("a", "C0", "Check the flag", "flag log"), ("b", "C4", "Remind to toggle the flag", "flag toggled"), ("c", "C4", "Drill the rollback", "drill log")]
    ans = {"answers": [{"x": "X1", "f": "F1"}, {"x": "X2", "f": "F1", "why": "it is about the flag"}, {"x": "X3", "f": "F2"}]}
    got = gr.route_fixes(_fake(lambda p: ans), None, "pro", PLAN, cards, fixes)
    assert got["a"][0] and got["c"][0] and not got["b"][0], got
    assert "different failure" in got["b"][1]
    none = gr.route_fixes(_fake(lambda p: {"answers": [{"x": "X1", "f": "none"}]}), None, "pro", PLAN, cards, fixes[:1])
    assert none["a"] == (False, "it does not stop this failure"), none
    assert gr.route_fixes(_fake(lambda p: 1 / 0), None, "pro", PLAN, cards, fixes) == {}, "a failed call keeps the critic verdict"


def test_basis_check_code_rules():
    things = {"things": [{"thing": "Cached tokens", "kind": "new object, booking or payment", "needed": "yes"},   # plan says "caches tokens"
                         {"thing": "support lacks access to the rollback snapshot", "kind": "new person or role", "needed": "yes"},  # a clause
                         {"thing": "a DBA", "kind": "new person or role", "needed": "no"},   # passing detail
                         {"thing": "rain", "kind": "ordinary background", "needed": "yes"}]}
    got = gr.basis_check(_fake(lambda p: things), lambda: None, "pro", PLAN, {"k": {"failure": "f", "mechanism": "m"}})
    assert got["k"] == {"basis": "plan", "needs": "", "question": "", "represents": "yes"}, got
    inv = {"things": [{"thing": "construction", "kind": "outside event", "needed": "yes"}]}
    got = gr.basis_check(_fake(lambda p: inv), lambda: None, "pro", PLAN, {"k": {"failure": "f", "mechanism": "m"}})
    assert got["k"] == {"basis": "invented", "needs": "construction", "question": "", "represents": "yes"}, got


def test_in_plan():
    pw = set(gr.norm(PLAN).split())
    assert gr._in_plan("Cached tokens", pw), "stem match: caches"
    assert gr._in_plan("Customer support", pw) and not gr._in_plan("Technical support", pw) and not gr._in_plan("construction", pw)


def test_route_fixes_accepts_a_fix_that_guards_several():
    cards = {"C1": {"failure": "snapshot corrupt", "mechanism": "snapshot -> corrupt"}, "C4": {"failure": "rollback not ready", "mechanism": "untrained -> rollback fails"}}
    ans = {"answers": [{"x": "X1", "f": "F1,F2"}]}
    got = gr.route_fixes(_fake(lambda p: ans), None, "pro", PLAN, cards, [("d", "C4", "Run a full rollback drill on Friday", "drill restores the snapshot")])
    assert got["d"][0], got


def test_basis_check_ask_outcome():
    card = {"k": {"failure": "Technical support might not be able to roll back.", "mechanism": "m"}}
    ask = {"things": [{"thing": "Technical support", "kind": "new person or role", "needed": "yes"}],
           "question": "Does support have an escalation path for cutover incidents?"}
    got = gr.basis_check(_fake(lambda p: ask), lambda: None, "pro", PLAN, card)["k"]
    assert got["basis"] == "ask" and got["question"].endswith("?"), got
    leans = dict(ask, question="Does technical support have rollback permissions?")
    got = gr.basis_check(_fake(lambda p: leans), lambda: None, "pro", PLAN, card)["k"]
    assert got["basis"] == "invented", "a question that assumes the invented thing is not an open question"
    noq = dict(ask, question="")
    assert gr.basis_check(_fake(lambda p: noq), lambda: None, "pro", PLAN, card)["k"]["basis"] == "invented"


def test_critic_partial_fix():
    card = {"failure": "she feels pressured", "mechanism": "Twelve friends will hide -> she feels pressured -> she leaves"}
    cand = {"id": "x", "action": "Reduce hidden friends to six", "how_to_check": "Count six friends at the stalls"}
    out = {"breaks": "Twelve friends will hide", "why": "fewer people", "targets": "yes", "concrete": "yes", "check_ok": "yes",
           "respects": "n/a", "covers": "partial", "still_open": "her wishes about a surprise and filming"}
    got = gr.critic(_fake(lambda p: out), lambda: None, "pro", PLAN, [("C2", card, cand)])
    assert got["x"][0] and cand["partial"] and "surprise" in cand["still_open"], (got, cand)


def test_open_question_is_never_a_safeguard():
    """Phase 3 R2: a planned question does not address a risk."""
    base = "Writes are paused via a feature flag; we have a rollback plan and a snapshot from Friday night."
    changes = ["Validate Friday snapshot before migration."]
    qs = ["Are there automated tests for the rollback process?"]
    # the model cites the question as the basis for "addressed"
    out = {"assessment": "addressed_in_plan", "basis_quote": "automated tests for the rollback process", "related_question": qs[0], "reason": "r"}
    got = gr.enforce_retest(out, base, changes, [], [], qs)
    assert got["assessment"] == "check_planned" and got["basis"] == "", got
    # a real change still counts as addressed (proposed)
    out = {"assessment": "addressed_in_plan", "basis_quote": "Validate Friday snapshot before migration", "reason": "r"}
    assert gr.enforce_retest(out, base, changes, [], [], qs)["assessment"] == "addressed_in_plan"
    # "check_planned" with no matching question falls back to unresolved
    out = {"assessment": "check_planned", "basis_quote": "", "related_question": "", "reason": "r"}
    assert gr.enforce_retest(out, base, changes, [], [], qs)["assessment"] == "unresolved"
    # R4: a planner assertion stays an unverified report
    out = {"assessment": "reported_covered_unverified", "basis_quote": "", "reason": "r"}
    assert gr.enforce_retest(out, base, changes, ["Flag: handled"], ["Flag: handled"], qs)["assessment"] == "reported_covered_unverified"


def test_basis_check_representativeness_and_failure():
    """Phase 2: a drifting card goes to review; a failed check is visible, never a pass."""
    card = {"k": {"failure": "The junior engineer might misconfigure SSL settings.", "mechanism": "m"}}
    mem = {"k": ["the on-call engineer misses a critical team meeting", "the junior engineer overlooks the rollback plan"]}
    drift = {"things": [{"thing": "SSL settings", "kind": "new object, booking or payment", "needed": "yes"}], "question": "", "represents": "no"}
    got = gr.basis_check(_fake(lambda p: drift), lambda: None, "pro", PLAN, card, members=mem)["k"]
    assert got["basis"] == "review" and got["represents"] == "no", got
    rep_inv = dict(drift, represents="yes")
    assert gr.basis_check(_fake(lambda p: rep_inv), lambda: None, "pro", PLAN, card, members=mem)["k"]["basis"] == "invented"
    mixed = {"things": [], "question": "", "represents": "mixed"}
    got = gr.basis_check(_fake(lambda p: mixed), lambda: None, "pro", PLAN, card, members=mem)["k"]
    assert got["basis"] == "plan" and got["represents"] == "mixed", "a card covering part of a mixed group stays"
    assert gr.basis_check(_fake(lambda p: 1 / 0), lambda: None, "pro", PLAN, card, members=mem)["k"]["basis"] == "unavailable"


def test_critic_rejects_goal_defeating_and_restating_fixes():
    """Checkpoint 2: a fix that reveals the surprise, or only repeats what the plan has, is rejected with the reason."""
    card = {"failure": "she feels uncomfortable with the fake birthday story", "mechanism": "surprise birthday dinner -> she feels misled"}
    base = {"breaks": "surprise birthday dinner", "why": "w", "targets": "yes", "concrete": "yes", "check_ok": "yes", "respects": "yes",
            "covers": "full", "still_open": "", "keeps_goal": "yes", "already": "no"}
    c1 = {"id": "a", "action": "Tell her about the proposal beforehand", "how_to_check": "She replies in writing"}
    got = gr.critic(_fake(lambda p: dict(base, keeps_goal="no")), lambda: None, "pro", PLAN, [("k", card, c1)], goal="The planner's goal (interpreted): a surprise proposal.")
    assert got["a"][0] is False and "goal" in got["a"][1], got
    c2 = {"id": "b", "action": "Ensure the rollback plan is documented", "how_to_check": "Rollback document link in the runbook"}
    got = gr.critic(_fake(lambda p: dict(base, already="yes")), lambda: None, "pro", PLAN, [("k", card, c2)])
    assert got["b"][0] is False and "already has" in got["b"][1], got
    c3 = {"id": "c", "action": "Ask her best friend about her general preferences", "how_to_check": "Best friend replies in writing"}
    assert gr.critic(_fake(lambda p: base), lambda: None, "pro", PLAN, [("k", card, c3)])["c"][0] is True

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("PASS test_grounding")
