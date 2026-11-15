"""Grounding, challenge and re-test assessment (change plan v2, A2, A3, A7).

Rules enforced here in code, whatever the model says:
- A quote counts only if it appears in the plan text (case, whitespace and punctuation ignored). The UI shows the
  plan's own words for it, so a quote on screen is always real plan text.
- A finding is "supported" only with at least one checked quote. "contradicted" needs a checked quote of the
  safeguard and a reason; otherwise it becomes "unverified".
- On a re-test, "addressed_in_plan" and "partially_addressed" need a checked quote from the original plan or an
  accepted change. A planner's statement is never evidence: a basis found only there gives
  "reported_covered_unverified".
- A new concern names an accepted change only when its quote is checked against the accepted changes.
"""
import re
from concurrent.futures import ThreadPoolExecutor

ASSESS = ("supported", "unverified", "contradicted")
RETEST = ("addressed_in_plan", "partially_addressed", "unresolved", "reported_covered_unverified", "check_planned")
_TRANS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


# ------------------------------------------------------------------ quote checks (pure)
def _norm_map(text):
    """Lowercase alphanumerics with single spaces, plus a map from each kept char back to its index in text."""
    out, idx, space = [], [], True
    for i, ch in enumerate(str(text).translate(_TRANS)):
        c = ch.lower()
        if c.isalnum():
            out.append(c)
            idx.append(i)
            space = False
        elif not space:
            out.append(" ")
            idx.append(i)
            space = True
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def norm(text):
    return _norm_map(text)[0]


def find_quote(quote, text, min_words=2):
    """The span of text that matches quote (ignoring case, whitespace, punctuation), or None.
    Needs at least min_words words so a lone common word does not count as evidence."""
    q = norm(quote)
    if not q or len(q.split()) < min_words:
        return None
    t, idx = _norm_map(text)
    at = t.find(q)
    if at < 0:
        return None
    a, b = idx[at], idx[at + len(q) - 1] + 1
    return str(text)[a:b]


def check_quotes(quotes, text, limit=2):
    """Checked quotes, as the exact words of text, deduplicated, at most limit."""
    seen, out = set(), []
    for q in quotes if isinstance(quotes, list) else ([quotes] if quotes else []):
        if not isinstance(q, str):
            continue
        span = find_quote(q, text)
        if span and norm(span) not in seen:
            seen.add(norm(span))
            out.append(span)
        if len(out) >= limit:
            break
    return out


def ground_worries(worries, plan):
    """Set w['evidence'] and w['ground'] on each worry; return the counts.
    quoted: at least one quote checked; ungrounded: gave quotes, none in the plan; omission: no quote, names what the
    plan does not say; none: neither. Ungrounded worries still count in huddles; they never supply evidence."""
    counts = {"quoted": 0, "ungrounded": 0, "omission": 0, "none": 0}
    for w in worries:
        qs = w.get("quotes") or []
        ev = check_quotes(qs, plan)
        w["evidence"] = ev
        if ev:
            g = "quoted"
        elif qs:
            g = "ungrounded"
        elif str(w.get("missing") or "").strip():
            g = "omission"
        else:
            g = "none"
        w["ground"] = g
        counts[g] += 1
    return counts


def _top(items, k):
    """Most frequent strings (by normalised form), first spelling kept."""
    freq, first = {}, {}
    for s in items:
        n = norm(s)
        if not n:
            continue
        freq[n] = freq.get(n, 0) + 1
        first.setdefault(n, s)
    return [first[n] for n, _ in sorted(freq.items(), key=lambda x: -x[1])[:k]]


def member_evidence(members):
    """For one huddle's worries: how many quoted the plan, the commonest checked quotes and omissions."""
    return {
        "n": len(members),
        "n_quoted": sum(1 for w in members if w.get("ground") == "quoted"),
        "n_omission": sum(1 for w in members if w.get("ground") == "omission"),
        "quotes": _top([q for w in members for q in w.get("evidence") or []], 4),
        "missing": _top([str(w.get("missing") or "").strip() for w in members if w.get("ground") in ("omission", "quoted")], 3),
    }


# ------------------------------------------------------------------ rule enforcement (pure)
def _s(x, n=300):
    return str(x if x is not None else "").strip()[:n]


def _list(x, k, n=160):
    if isinstance(x, str):
        x = [x]
    return [_s(v, n) for v in (x if isinstance(x, list) else []) if _s(v)][:k]


def enforce_card(out, plan):
    """Turn a challenge answer into a Risk Card that obeys the evidence rules."""
    a = _s(out.get("assessment")).lower()
    a = a if a in ASSESS else "unverified"
    evidence = check_quotes(_list(out.get("evidence"), 4), plan)
    control = check_quotes(_list(out.get("control_quote"), 2), plan)
    reason = _s(out.get("contradiction_reason"), 240)
    if a == "contradicted" and not (control and reason):
        a = "unverified"
    if a != "contradicted":   # decided in code: grounded if a quote checks, otherwise something the plan does not say
        a = "supported" if evidence else "unverified"
    return {
        "name": _s(out.get("name"), 60),
        "failure": _s(out.get("failure"), 200),
        "mechanism": _s(out.get("mechanism"), 240),
        "evidence": evidence,
        "assumptions": _list(out.get("assumptions"), 2, 140),
        "assessment": a,
        "control_quote": control[0] if a == "contradicted" else "",
        "contradiction_reason": reason if a == "contradicted" else "",
        "question": _s(out.get("question"), 160),
    }


def enforce_retest(out, base_plan, changes, reports, own_reports=None, questions=()):
    """Turn a re-test answer into an assessment that obeys the planner-report rules.
    own_reports: the planner statements that name THIS finding (a statement counts only for the finding it names)."""
    own = list(reports if own_reports is None else own_reports)
    a = _s(out.get("assessment")).lower()
    a = a if a in RETEST else "unresolved"
    plan_text = base_plan + "\n" + "\n".join(changes)
    in_plan = []
    rel = re.sub(r"^\s*[CR]\d+[.):]\s*", "", _s(out.get("related_change"), 300))   # the model may copy the list label
    whole = [c for c in changes if rel and len(norm(rel).split()) >= 2 and (norm(rel) in norm(c) or norm(c) in norm(rel))]
    for cand in [_s(out.get("basis_quote"), 240), rel] + whole:
        in_plan = check_quotes([cand], plan_text, 1) if cand else []
        if in_plan:
            break
    note = ""
    if a in ("addressed_in_plan", "partially_addressed") and not in_plan:
        a, note = ("reported_covered_unverified", "Only the planner's statement covers this.") if own else \
                  ("unresolved", "No phrase in the plan or the accepted changes supports this.")
    if a == "reported_covered_unverified" and not own:
        a = "unresolved"
    if a == "unresolved" and own:
        a, note = "reported_covered_unverified", "The planner says this is handled; no evidence was given."
    # An open question is never a safeguard: if the only related item is a question, the check is planned, outcome unknown.
    rq = _s(out.get("related_question"), 300)
    asked = [q for q in questions if rq and len(norm(rq).split()) >= 2 and (norm(rq) in norm(q) or norm(q) in norm(rq))]
    basis_q = _s(out.get("basis_quote"), 240)
    on_question = bool(basis_q) and any(find_quote(basis_q, q) for q in questions) and not any(find_quote(basis_q, c) for c in changes)
    if on_question:
        in_plan = []
        if a in ("addressed_in_plan", "partially_addressed"):
            a = "unresolved"
    if a in ("unresolved", "check_planned") and (asked or on_question):
        a, note = "check_planned", "You plan to find this out; the answer is not known yet, so nothing is addressed."
    elif a == "check_planned":
        a = "unresolved"   # the model said a check was planned, but no open question matches this finding
    return {
        "assessment": a,
        "basis": in_plan[0] if in_plan and a in ("addressed_in_plan", "partially_addressed") else "",
        "basis_from_change": bool(in_plan and any(find_quote(in_plan[0], c) for c in changes)),
        "reason": " ".join(x for x in (_s(out.get("reason"), 240), note) if x),
        "still_open": _s(out.get("still_open"), 160),
    }


def enforce_new(out, changes):
    """Up to 3 new concerns; a related change only when its quote is checked against the accepted changes."""
    res = []
    for c in (out.get("concerns") if isinstance(out.get("concerns"), list) else [])[:3]:
        if not isinstance(c, dict) or not _s(c.get("concern")) or not _s(c.get("mechanism")):
            continue   # a concern with no "how it would happen" is not shown
        rel_q = _s(c.get("related_change_quote"), 200)
        related = next((ch for ch in changes if rel_q and find_quote(rel_q, ch)), "")
        res.append({"concern": _s(c.get("concern"), 200), "mechanism": _s(c.get("mechanism"), 240), "related_change": related})
    return res


# ------------------------------------------------------------------ Nova Lite calls
SYSTEM = "You check risks against a plan's exact text. You are strict and never assume facts the plan does not state. Output one JSON object only."


def challenge(ask_retry, usage_local, lite, plan, findings):
    """One Nova Lite call per finding (temperature 0). findings: [{key, examples, quotes, missing}] -> {key: card}."""
    def one(f):
        ex = "\n".join(f"- {e}" for e in f["examples"][:5])
        qs = "\n".join(f'- "{q}"' for q in f.get("quotes") or []) or "- (none)"
        ms = "\n".join(f"- {m}" for m in f.get("missing") or []) or "- (none)"
        prompt = (
            f"Plan:\n<<<\n{plan}\n>>>\n\nImagined failures of this plan, grouped together:\n{ex}\n\n"
            f"Plan phrases these failures point to (already checked against the plan):\n{qs}\n\n"
            f"What these failures say the plan does not state:\n{ms}\n\n"
            "Answer strictly from the plan text.\n"
            "failure: the shared concern in one plain sentence (max 20 words), phrased as what MIGHT happen, never as a prediction.\n"
            "name: a short name for THIS failure, max 5 words, specific (say what fails, e.g. 'Snapshot cannot be restored'), never a vague category "
            "like 'inadequate preparation', and never a prediction of what a person will decide (say 'Discomfort with the setup', not 'Proposal refusal').\n"
            "mechanism: how this plan leads to it, as a short chain like 'A -> B -> C' (max 25 words).\n"
            "evidence: up to 2 phrases copied word for word from the plan that this failure depends on (max 12 words each), or [].\n"
            "assumptions: up to 2 things this failure depends on that the plan does not say (max 12 words each).\n"
            "assessment: \"supported\" if the plan states a condition that makes this failure possible; \"contradicted\" ONLY if "
            "the plan states a safeguard that stops THIS exact mechanism (a related safeguard is not enough: having a rollback "
            "plan does not stop 'the rollback fails'); otherwise \"unverified\".\n"
            "control_quote: if contradicted, the safeguard's phrase copied word for word from the plan; otherwise \"\".\n"
            "contradiction_reason: if contradicted, how that safeguard stops this mechanism (max 20 words); otherwise \"\".\n"
            "question: one short, kind question for the planner about the specific safeguard that would stop this mechanism "
            "(max 16 words). Ask whether that safeguard is in place, not whether something related exists.\n"
            'Return JSON {"name": "", "failure": "", "mechanism": "", "evidence": [], "assumptions": [], "assessment": "<supported, unverified or contradicted>", '
            '"control_quote": "", "contradiction_reason": "", "question": ""}'
        )
        out = ask_retry(usage_local(), lite, SYSTEM, prompt, 500, lambda o: o["question"] and o["assessment"], temperature=0.0)
        return f["key"], enforce_card(out, plan)

    with ThreadPoolExecutor(max_workers=7) as pool:
        res = {}
        for f, r in zip(findings, pool.map(_safe(one), findings)):
            if r:
                res[r[0]] = r[1]
            else:
                res[f["key"]] = None
        return res


def assess_original(ask_retry, usage_local, lite, base_plan, changes, reports, cards, questions=()):
    """Re-test: one Nova Lite call per original finding. cards: [{key, failure, mechanism, evidence}].
    questions: open questions the planner will look into. They are shown to the model as NOT safeguards, and code turns
    any verdict that rests only on a question into check_planned."""
    ch = "\n".join(f"C{i + 1}. {c}" for i, c in enumerate(changes)) or "(none)"
    qs = "\n".join(f"Q{i + 1}. {q}" for i, q in enumerate(questions)) or "(none)"
    rp = "\n".join(f"R{i + 1}. {r}" for i, r in enumerate(reports)) or "(none)"

    def one(c):
        prompt = (
            f"Original plan:\n<<<\n{base_plan}\n>>>\n\n"
            f"Accepted changes (planned actions, not done yet):\n{ch}\n\n"
            f"Planner statements (the planner's word only, NOT verified):\n{rp}\n\n"
            f"Open questions the planner will look into (NOT safeguards, NOT answered):\n{qs}\n\n"
            f"Original risk ({c.get('name') or 'this finding'}): {c['failure']}\nHow it happens: {c['mechanism']}\n"
            f"Plan phrases it depended on: {'; '.join(c.get('evidence') or []) or '(none)'}\n\n"
            "Step 1. related_change: the accepted change that acts on THIS risk, copied word for word, or \"\" if none does.\n"
            "Step 2. related_statement: the planner statement about THIS risk, copied word for word, or \"\" if none.\n"
            "Step 2b. related_question: the open question about THIS risk, copied word for word, or \"\" if none. A question "
            "never addresses a risk.\n"
            "Step 3. assessment, by these rules:\n"
            "- addressed_in_plan: related_change (or the original plan) would stop this mechanism, or catch it in time to prevent harm.\n"
            "- partially_addressed: related_change reduces it or checks one part, but another path to the failure stays open.\n"
            "- reported_covered_unverified: no related change, but a related_statement says it is handled.\n"
            "- check_planned: no related change, but an open question about this risk is planned (outcome unknown).\n"
            "- unresolved: none of the above.\n"
            "basis_quote: for addressed or partially addressed, the words of the related change (or the original plan) that do "
            "it; for reported_covered_unverified, the words of the statement; otherwise \"\".\n"
            "reason: what the plan now does about this mechanism and the exact condition that remains open (max 30 words).\n"
            "still_open: the exact remaining condition, specific to this mechanism (max 15 words).\n"
            'Return JSON {"related_change": "", "related_statement": "", "related_question": "", "assessment": "<addressed_in_plan, partially_addressed, reported_covered_unverified, check_planned or unresolved>", "basis_quote": "", "reason": "", "still_open": ""}'
        )
        out = ask_retry(usage_local(), lite, SYSTEM, prompt, 400, lambda o: o["assessment"], temperature=0.0)
        own = [r for r in reports if c.get("name") and r.lower().startswith(c["name"].lower() + ":")]
        return c["key"], enforce_retest(out, base_plan, changes, reports, own, questions)

    with ThreadPoolExecutor(max_workers=7) as pool:
        return {r[0]: r[1] for r in pool.map(_safe(one), cards) if r}


def new_concerns(ask_retry, usage, lite, plan, changes, examples, original=()):
    """Re-test: group worries that matched no original huddle into up to 3 concerns (one Nova Lite call)."""
    if not examples:
        return []
    orig = "\n".join(f"- {o}" for o in original if o) or "- (none)"
    ex = "\n".join(f"- {e}" for e in examples[:12])
    ch = "\n".join(f"- {c}" for c in changes) or "- (none)"
    prompt = (
        f"Plan (with accepted changes):\n<<<\n{plan}\n>>>\n\nAccepted changes:\n{ch}\n\n"
        f"Original risks (already known; skip any worry that restates one of these):\n{orig}\n\n"
        f"Worries from the re-test that matched none of the original huddles:\n{ex}\n\n"
        "Group the genuinely new ones into at most 3 distinct concerns that are realistic for this plan (fewer, or none, is fine). "
        "Drop any worry that does not make sense on its own: say who or what fails and why, using only people and things in "
        "the plan or its changes.\n"
        "concern: one plain sentence (max 15 words). mechanism: how it would happen (max 20 words).\n"
        "related_change_quote: only if DOING one accepted change creates this new way to fail (for example, telling more "
        "people a secret makes a leak more likely), a phrase copied word for word from that change; otherwise \"\". "
        "A change that merely fails to fix an old problem does not cause it.\n"
        'Return JSON {"concerns": [{"concern": "", "mechanism": "", "related_change_quote": ""}]}'
    )
    out = ask_retry(usage, lite, SYSTEM, prompt, 450, lambda o: isinstance(o["concerns"], list), temperature=0.0)
    return enforce_new(out, changes)


def _safe(fn):
    def run(x):
        try:
            return fn(x)
        except Exception as e:  # noqa: BLE001 - one failed card must not sink the run; the UI shows the card without it
            print("grounding call failed:", type(e).__name__, str(e)[:200])
            return None
    return run


# ------------------------------------------------------------------ fix critic (A5)
VAGUE_CHECK = re.compile(r"\b(understand\w*|aware\w*|ensure|make sure|meeting|discuss\w*|feel\w*|confident|familiar\w*)\b", re.I)


def vague_check(text):
    """A check must be observable (a log, a test result, a written reply, a restored row). Code rejects the vague ones."""
    t = str(text or "").strip()
    return len(t.split()) < 3 or bool(VAGUE_CHECK.search(t))


def critic(ask_retry, usage_local, lite, plan, pairs, goal=""):
    """pairs: [(key, card, cand)]. One Nova Lite call each (temperature 0): does the fix stop THIS mechanism, with a
    concrete action? Code adds the observable-check rule. Returns {cand id: (ok, why)}."""
    def one(p):
        key, cd, c = p
        prompt = (
            f"Plan:\n<<<\n{plan}\n>>>\n\nFailure: {cd.get('failure', '')}\nHow it happens: {cd.get('mechanism', '')}\n\n"
            + (f"{goal}\n" if goal else "") +
            f"Proposed fix: {c['action']}\nHow to check it works: {c.get('how_to_check', '')}\n\n"
            "targets: \"yes\" only if doing this fix would stop or catch THIS mechanism before harm. \"no\" if it addresses "
            "a different problem, only restates the worry, or only informs people without changing what fails.\n"
            "concrete: \"yes\" if it is a specific action someone can do; \"no\" if it is vague.\n"
            "check_ok: \"yes\" only if the check is something you can observe that shows THIS mechanism is stopped (a test "
            "result, a log, a restored copy, a written answer); \"no\" for impressions such as someone seeming happy or calm.\n"
            "respects: \"n/a\" if the fix does not touch any person's wishes, feelings or choices (most technical fixes); "
            "otherwise \"yes\" if it respects the affected person's own choice, \"no\" if it decides for them, guesses their "
            "wishes through someone else when they could be asked, pressures or deceives anyone.\n"
            "keeps_goal: \"no\" if the fix gives up the planner's goal or something it should keep (e.g. it reveals a "
            "surprise that should stay a surprise) when another way exists; otherwise \"yes\".\n"
            "already: \"yes\" if the fix only restates something the plan already says it has (e.g. \"have a rollback plan\" "
            "when the plan has one) instead of acting on what fails; otherwise \"no\".\n"
            "covers: \"full\" if the fix stops this mechanism; \"partial\" if it only reduces it or leaves part of the cause open.\n"
            "still_open: if partial, what stays open (max 14 words); otherwise \"\".\n"
            "First write breaks: copy, word for word, the step of the mechanism above that this fix breaks, or \"none\". "
            "If none, targets is \"no\". Then why (max 15 words), then answer each.\n"
            'Return JSON {"breaks": "<step or none>", "why": "<max 15 words>", "targets": "<yes or no>", "concrete": "<yes or no>", "check_ok": "<yes or no>", "respects": "<yes, no or n/a>", "covers": "<full or partial>", "still_open": "", "keeps_goal": "<yes or no>", "already": "<yes or no>"}'
        )
        out = ask_retry(usage_local(), lite, SYSTEM, prompt, 360, lambda o: o["targets"], temperature=0.0)
        yes = lambda k: str(out.get(k, "no")).strip().lower().startswith("y")
        respects = str(out.get("respects", "n/a")).strip().lower()
        # code check: the step the fix claims to break must be a real step of the checked mechanism
        steps = [x.strip() for x in str(cd.get("mechanism", "")).split("->") if x.strip()]
        brk = str(out.get("breaks", "")).strip()
        on_step = bool(brk) and brk.lower() != "none" and (not steps or any(
            find_quote(brk, st, min_words=1) or find_quote(st, brk, min_words=1) for st in steps))
        keeps = not str(out.get("keeps_goal", "yes")).strip().lower().startswith("n")
        ok = (yes("targets") and on_step and yes("concrete") and yes("check_ok") and keeps and not yes("already")
              and not respects.startswith("no") and not vague_check(c.get("how_to_check")))
        why = _s(out.get("why"), 160) or ("the check is not observable" if vague_check(c.get("how_to_check")) else "")
        if yes("targets") and not on_step:
            why = "the fix does not break a step of this failure"
        elif not keeps:
            why = "the fix gives up the planner's goal: " + why
        elif yes("already"):
            why = "the fix repeats what the plan already has instead of acting on what fails"
        if ok and str(out.get("covers", "full")).strip().lower().startswith("p"):
            c["partial"], c["still_open"] = True, _s(out.get("still_open"), 120)
        return c["id"], (ok, why)

    with ThreadPoolExecutor(max_workers=7) as pool:
        return {r[0]: r[1] for r in pool.map(_safe(one), pairs) if r}


def route_fixes(ask_retry, usage, model, plan, cards, fixes):
    """Second, independent fix check. ONE call sees every finding of the run and every fix, and names the finding each
    fix most directly prevents (or none). A fix passes only if it lands on its own finding: a 'reminder to toggle the flag'
    written for 'rollback readiness' lands on the flag finding and is dropped. Multiple choice over the run's own findings
    is steadier than a yes/no on one pair. cards: {key: card}; fixes: [(fix id, own key, action, how_to_check)].
    Returns {fix id: (ok, why)}; {} if the call fails (the caller then keeps the critic's verdict)."""
    keys = [k for k, cd in cards.items() if cd and cd.get("mechanism")]
    if not keys or not fixes:
        return {}
    flist = "\n".join(f"F{i + 1}: {cards[k].get('failure', '')} How it happens: {cards[k]['mechanism']}" for i, k in enumerate(keys))
    xlist = "\n".join(f"X{j + 1}: {a} (check: {h})" for j, (_, _, a, h) in enumerate(fixes))
    prompt = (
        f"Plan:\n<<<\n{plan}\n>>>\n\nFailures:\n{flist}\n\nFixes:\n{xlist}\n\n"
        "For EACH fix, name the failure whose cause it most directly removes, tests or guards. If it directly guards "
        "several, list them all (\"F1,F3\"). Answer \"none\" if it only reminds or informs people while the cause stays, "
        "or if it does not stop any of these failures.\n"
        'Return JSON {"answers": [{"x": "X1", "f": "F2, or F1,F3, or none", "why": "<max 12 words>"}]} with one entry per fix.'
    )
    try:
        out = ask_retry(usage, model, SYSTEM, prompt, 60 + 40 * len(fixes), lambda o: isinstance(o["answers"], list), temperature=0.0)
    except Exception as e:  # noqa: BLE001
        print("route_fixes failed:", type(e).__name__, str(e)[:200])
        return {}
    got = {}
    for a in out["answers"]:
        try:
            j = int(str(a.get("x", "")).strip().upper().lstrip("X")) - 1
            nums = [int(n) for n in re.findall(r"F\s*(\d+)", str(a.get("f", "")).upper())]
            got[j] = ([keys[n - 1] for n in nums if 0 < n <= len(keys)], _s(a.get("why"), 120))
        except (TypeError, ValueError, AttributeError):
            pass
    res = {}
    for j, (fid, own, action, _) in enumerate(fixes):
        if j not in got:
            continue
        ks, why = got[j]
        res[fid] = (own in ks, "" if own in ks else ("it prevents a different failure" if ks else "it does not stop this failure") + (": " + why if why else ""))
    return res


INVENTED_KINDS = ("new person or role", "new object, booking or payment", "outside event")


def basis_check(ask_retry, usage_local, model, plan, cards, members=None):
    """Invented-cause check, one call per card (temperature 0). The model lists each thing the card's failure sentence
    mentions that the plan does not (the chain is not shown: Nova Lite's chains add passing actors such as "a DBA"), and sorts it into a fixed kind; code decides. A failure happening (an error, a
    delay, a crash, a feeling, a refusal) and ordinary background (weather, traffic, phones, health) are imagined
    failures and stay. A new person or role, a new object/booking/payment, or an unusual outside event the plan gives
    no reason to expect (construction, a strike, an inspection) makes the finding 'invented', unless the concern can be
    asked as a question about what the plan leaves unsaid without assuming that thing: then it is 'ask' (an open question,
    no fix). cards: {key: card}. Returns {key: {"basis": "plan"|"ask"|"invented", "needs": str, "question": str}}.
    members: {key: [sample of the cluster's worries]}. The same call says whether the card represents those worries.
    A card about something only a few of them mention ("no") cannot decide the cluster's fate: basis "review"; a card
    that covers one real part of a mixed group ("mixed") stays, marked as partial coverage (run d9db470a: an SSL card
    stood for 112 worries about on-call mistakes, 1 of which mentioned SSL, and the whole group was set aside).
    A key whose call fails gets basis "unavailable" (never treated as checked).
    Stem matching (_in_plan) only recognises that the plan mentions a thing; it is never evidence that a finding is backed."""
    kinds = ["failure happening", "ordinary background", *INVENTED_KINDS]

    def one(item):
        key, cd = item
        prompt = (
            f"Plan:\n<<<\n{plan}\n>>>\n\nPossible failure: {cd.get('failure', '')}\n\n"
            + (("Worries this possible failure is meant to summarise (a sample):\n" + "\n".join(f"- {m}" for m in mem[key]) + "\n\n"
                "represents: \"yes\" if the possible failure describes what MOST of these worries are about; \"mixed\" if the "
                "worries are about several different things; \"no\" if the possible failure is about something only a few of "
                "them mention.\n") if mem.get(key) else "") +
            "List every person, role, object, booking, payment, place or event in the possible failure that the plan does "
            "NOT mention. People and things the plan names do not count, and neither do the users or customers of "
            "an app or service the plan names. For each, give its kind:\n"
            "- failure happening: the failure itself or a step of it (an error, delay, crash, corruption, leak, a feeling, "
            "a refusal, someone finding out)\n"
            "- ordinary background: weather, tide, traffic, crowds, phone battery, illness, network\n"
            "- new person or role: a person or role the plan never mentions (a chef, a guard, a vendor)\n"
            "- new object, booking or payment: an object, booking, deposit or payment the plan never mentions\n"
            "- outside event: an unusual event the plan gives no reason to expect (construction, a strike, a closure, an inspection)\n"
            "needed: \"yes\" if the failure could NOT happen without this thing; \"no\" if it is a passing detail and the same "
            "failure could still happen without it.\n"
            "question: if the underlying concern is still worth checking WITHOUT assuming any of those things, because the plan "
            "leaves something unsaid that matters, one short question about what the plan leaves unsaid (max 14 words, e.g. "
            "'Does support have an escalation path for cutover incidents?'). Otherwise \"\".\n"
            'Return JSON {"things": [{"thing": "<the noun itself, max 4 words>", "kind": "<one kind above>", "needed": "<yes or no>"}], '
            '"question": ""' + (', "represents": "<yes, mixed or no>"' if mem.get(key) else '') + '}'
        )
        out = ask_retry(usage_local(), model, SYSTEM, prompt, 420, lambda o: isinstance(o["things"], list), temperature=0.0)
        rp = str(out.get("represents", "yes")).strip().lower() if mem.get(key) else "yes"
        rp = "no" if rp.startswith("n") else "mixed" if rp.startswith("m") else "yes"
        if rp == "no":   # the card is about something only a few of its worries mention: it cannot speak for the group
            return key, {"basis": "review", "needs": "", "question": "", "represents": "no"}
        bad = [_s(t.get("thing"), 60) for t in out["things"] if isinstance(t, dict)
               and str(t.get("kind", "")).strip().lower() in INVENTED_KINDS and _s(t.get("thing"))
               and str(t.get("needed", "yes")).strip().lower().startswith("y")]
        # code checks: a thing is a short noun, not a clause ("support lacks access to snapshots" is a failure step),
        # and the model said "new" about something the plan does not name
        bad = [b for b in bad if len(b.split()) <= 5 and not _in_plan(b, plan_words)]
        if not bad:
            return key, {"basis": "plan", "needs": "", "question": "", "represents": rp}
        q = _s(out.get("question"), 160)
        # "ask": a real gap the plan leaves open. Code check: the question must not lean on the invented thing itself.
        bad_words = {w for b in bad for w in norm(b).split() if w not in _FILLER and len(w) > 2 and w not in plan_words}
        if q.endswith("?") and len(q.split()) >= 4 and not (bad_words & set(norm(q).split())):
            return key, {"basis": "ask", "needs": "; ".join(bad[:2]), "question": q, "represents": rp}
        return key, {"basis": "invented", "needs": "; ".join(bad[:2]), "question": "", "represents": rp}

    plan_words = set(norm(plan).split())
    mem = {k: [m for m in (v or [])][:8] for k, v in (members or {}).items()}
    items = [(k, cd) for k, cd in cards.items() if cd and cd.get("mechanism")]
    with ThreadPoolExecutor(max_workers=7) as pool:
        got = {r[0]: r[1] for r in pool.map(_safe(one), items) if r}
    for k, _ in items:   # a failed check is visible, never a silent pass
        got.setdefault(k, {"basis": "unavailable", "needs": "", "question": ""})
    return got


_FILLER = {"a", "an", "the", "of", "to", "in", "on", "for", "with", "and", "or", "her", "his", "their", "our", "my", "new", "is", "was"}


def _in_plan(thing, plan_words):
    """True if at least two thirds of the thing's content words appear in the plan (plural 's' ignored)."""
    ws = [w for w in norm(thing).split() if w not in _FILLER]
    if not ws:
        return True
    stems = {w[:5] for w in plan_words if len(w) >= 5}
    hit = sum(1 for w in ws if w in plan_words or w.rstrip("s") in plan_words or w + "s" in plan_words
              or (len(w) >= 5 and w[:5] in stems))   # crude stem: "cached" matches "caches"
    return hit * 3 >= 2 * len(ws)
