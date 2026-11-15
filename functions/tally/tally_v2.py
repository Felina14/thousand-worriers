"""Tally v2 adapter: Bedrock I/O around worrycore ("AI generates, maths decides").

worrycore/ (pure numpy, written separately; do not edit) does screening, clustering, risk = L x I, bootstrap
confidence, the quiet worry, fix selection and re-test statistics. This file only prepares inputs, calls Amazon Nova
where worrycore asks for judgement, stores state in S3 and maps the outputs onto the result shape the UI already reads
(plus v2 extras: funnel, health, risk, confidence, plan card, verdicts).
"""
import json
import re
import os
from concurrent.futures import ThreadPoolExecutor

import boto3
import numpy as np

import grounding as gr
import worrycore as wc

BUCKET = os.environ.get("BUCKET_NAME", "")
s3 = boto3.client("s3")


def _key(run_id):
    return f"results/{run_id}/v2.json"


def _texts(worries):
    # The failure text alone: generic cause tags ("miscommunication", "inexperience") pulled unrelated failures into
    # one huddle when they were weighted in.
    return [w["failure"] for w in worries]


def split_big(state, vecs, worries, share=0.2, passes=3):
    """Second-stage split: any huddle holding more than `share` of the room is re-clustered once on its own members;
    a split is kept only when it gives at least two parts of 8%+ of that huddle. Updates labels, centroids, sizes and
    the judge examples in place, so ranking and re-test use the split huddles."""
    keep = state["keep"]
    w = np.asarray(state["weights"], dtype=np.float64)
    labels = np.asarray(state["labels"])
    X = wc.normalize(np.asarray(vecs, dtype=np.float32)[keep])
    total, changed = w.sum(), False
    for _ in range(passes):
        nxt, split_any = int(labels.max()) + 1 if len(labels) else 0, False
        for lab in sorted({int(l) for l in labels if l >= 0}):
            idx = np.where(labels == lab)[0]
            if w[idx].sum() < share * total or len(idx) < 12:
                continue
            sub = wc.cluster(X[idx], w[idx].tolist(), k_min=2, k_max=5, min_share=0.08)
            sl = np.asarray(sub["labels"])
            parts = [p for p in set(sl.tolist()) if p >= 0]
            if len(parts) < 2:
                continue
            keep_part = max(parts, key=lambda p: w[idx][sl == p].sum())
            for p in parts:
                if p != keep_part:
                    labels[idx[sl == p]] = nxt
                    nxt += 1
            split_any = changed = True
        if not split_any:
            break
    if not changed:
        return state
    cents, sizes = {}, {}
    for lab in sorted({int(l) for l in labels if l >= 0}):
        m = labels == lab
        c = (X[m] * w[m, None]).sum(axis=0)
        cents[lab] = (c / (np.linalg.norm(c) or 1.0)).tolist()
        sizes[lab] = float(w[m].sum())
    reps = wc.representatives(X, labels.tolist(), cents)
    state.update(labels=labels.tolist(), centroids={str(k): v for k, v in cents.items()}, sizes={str(k): v for k, v in sizes.items()},
                 k=len(cents), split=True)
    state["ask"]["judge"] = [{"label": lab, "size": sizes[lab], "examples": [worries[keep[p]]["failure"] for p in pos]} for lab, pos in reps.items()]
    return state


def _tid(label):
    return f"C{int(label)}"


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    return o


# ------------------------------------------------------------------ LLM calls worrycore asks for
def judge_clusters(ctx, plan, facts, items):
    """One Nova Lite call per cluster (temperature 0): name, definition, plausibility, controllability."""
    fact_list = "\n".join(f"- {f}" for f in facts)

    def one(item):
        ex = "\n".join(f"- {e}" for e in item["examples"][:5])
        prompt = (
            f"Plan:\n<<<\n{plan}\n>>>\n\nFacts stated in the plan:\n{fact_list}\n\n"
            f"These imagined failures were grouped together by meaning:\n{ex}\n\n"
            "1. name: the shared root cause in plain words (max 5 words).\n"
            "2. definition: one line saying what belongs in this group (max 25 words).\n"
            "3. plausible: true only if this is a realistic failure of THIS plan given ONLY the facts stated. "
            "Reject (false) if it depends on people, places, vehicles, bookings, payments, documents or steps the plan "
            "never mentions, or on a specific role or supplier the plan does not name (a chef, caterer, DJ, neighbour), even if "
            "the place might have one, or on an unusual outside event the plan gives no reason to expect (an inspection, a closure, a strike). "
            "Ordinary background (weather, traffic, crowds, phones, health, feelings) is allowed.\n"
            "4. controllability: controllable (the planner can prevent it), partial (can reduce or prepare), or none.\n"
            'Return JSON {"name": "", "definition": "", "plausible": true, "controllability": "partial"}'
        )
        out = ctx.ask_retry(ctx.usage_local(), ctx.LITE, "You judge and name groups of risks strictly. Output one JSON object only.",
                            prompt, 220, lambda o: o["name"], temperature=0.0)
        pl = out.get("plausible", True)
        pl = pl if isinstance(pl, bool) else str(pl).strip().lower().startswith(("t", "y"))
        return item["label"], {"name": str(out.get("name", "")).strip(), "definition": str(out.get("definition", "")).strip(),
                               "plausible": pl, "controllability": str(out.get("controllability", "partial")).strip().lower()}

    with ThreadPoolExecutor(max_workers=5) as pool:
        return dict(pool.map(one, items))


def goal_context(ctx, plan):
    """One Nova Lite call per plan: the plan's underlying goal, what to preserve, constraints, unknowns. Interpreted, not
    plan facts. Fixes and the critic use it so a fix does not silently defeat the plan's purpose (proposal: "tell her
    about the surprise" removed the surprise). Returns {} if the call fails (nothing is invented)."""
    prompt = (
        f"Plan:\n<<<\n{plan}\n>>>\n\n"
        "goal: what the planner is trying to achieve, in one short sentence (max 15 words).\n"
        "preserve: up to 2 things the planner clearly wants to keep (e.g. that it stays a surprise), only if the plan shows it.\n"
        "tactics: up to 2 chosen methods that could be changed without losing the goal (e.g. a cover story).\n"
        "constraints: up to 2 limits that must hold (e.g. respect the other person's freedom to choose).\n"
        "unknowns: up to 2 important things the planner does not know (e.g. her preference about a crowd).\n"
        'Return JSON {"goal": "", "preserve": [], "tactics": [], "constraints": [], "unknowns": []}'
    )
    try:
        out = ctx.ask_retry(ctx.usage_local(), ctx.LITE, "You read plans carefully. Output one JSON object only.", prompt, 300,
                            lambda o: isinstance(o["goal"], str), temperature=0.0)
    except RuntimeError as e:
        print("goal context failed:", e)
        return {}
    lst = lambda k: [str(x).strip()[:120] for x in (out.get(k) or []) if str(x).strip()][:2]
    return {"goal": str(out.get("goal", "")).strip()[:160], "preserve": lst("preserve"), "tactics": lst("tactics"),
            "constraints": lst("constraints"), "unknowns": lst("unknowns")}


def goal_text(g):
    if not g or not g.get("goal"):
        return ""
    parts = [f"The planner's goal (interpreted): {g['goal']}."]
    if g.get("preserve"):
        parts.append("Keep where possible: " + "; ".join(g["preserve"]) + ".")
    if g.get("tactics"):
        parts.append("Tactics that may change if needed: " + "; ".join(g["tactics"]) + ".")
    if g.get("constraints"):
        parts.append("Must hold: " + "; ".join(g["constraints"]) + ".")
    return " ".join(parts)


def fix_candidates(ctx, plan, moments, items, n_total, affected=None, goal=None):
    """Nova Pro (fixes only): 2 structured candidates per top theme, for the affected card and for the quiet worry."""
    def one(item):
        quiet = item["label"] == "quiet"
        aff = item["label"] == "affected"
        ex = "\n".join(f"- {e}" for e in item["examples"][:5])
        if aff:
            head = (f"What the people affected by this plan actually want: of {(affected or {}).get('of', '?')} worriers asked to "
                    f"look only at that, {(affected or {}).get('count', '?')} raised this concern:\n{ex}\n"
                    "Fix it in a way that respects what the person most affected wants: kind and honest. It may keep a "
                    "pleasant surprise, but never lie, pressure or decide for them.\n"
                    "Both fixes must directly answer THIS concern for the people it is about (if it is about customers, the "
                    "fix acts toward those customers, e.g. telling them in advance); a fix about a different problem or for "
                    "different people is wrong.\n")
        elif quiet:
            head = f"Concern raised by only a few of {n_total} worriers, but it matters:\n{ex}\n"
        else:
            head = f"Failure theme: {item['name']}\nExamples imagined by the worriers:\n{ex}\n"
        cd = item.get("card") or {}
        if cd.get("mechanism"):
            head += (f"Checked against the plan: the failure is \"{cd.get('failure', '')}\"; how it happens: {cd['mechanism']}"
                     + (f"; the plan does not say: {'; '.join(cd.get('assumptions') or [])}" if cd.get("assumptions") else "")
                     + ".\nEach fix must stop THIS mechanism (not a different problem), and how_to_check must show it is stopped.\n")
        gt = goal_text(goal)
        if gt:
            head += (gt + " A fix must keep the goal and what should be kept. If the concern is about what an affected "
                     "person wants, find it out without revealing what should stay a surprise (for example, ask someone "
                     "close to them about their general preferences), and never decide for them.\n")
        prompt = (
            f"Plan:\n<<<\n{plan}\n>>>\n\n{head}\nResponse type wanted: {item.get('response', 'reduce')} "
            "(prevent = stop it happening; reduce = make it less likely or smaller; prepare = be ready if it happens).\n"
            "Write 2 different candidate fixes for the person making this plan. Each must be ONE specific, cheap change "
            "they can make themselves, naming the actual people, places, times or items from the plan.\n"
            f"Fields: action (max 25 words), owner (a person or role named in the plan, or \"me\"), moment (exactly one of: "
            f"{json.dumps(moments)}), effort (low|medium|high), backup (what to do on the day if it still goes wrong, fits "
            "the action, max 20 words), how_to_check (an observable check that shows the fix is in place and works, max 20 "
            "words), if_check_fails (what to do if that check fails, max 15 words), early_sign (an observable warning sign of this concern, max 15 words), when (when to "
            "check the sign, relative to the main event, max 8 words), days_before (whole number, 0 = same day).\n"
            + ('Also give "question": this concern as a short, kind question the planner should ask themselves (max 10 words).\n' if quiet else "")
            + "Rules: use only facts in the plan; never invent names; add no new people, vendors or services; no contracts, "
            "NDAs, legal steps or insurance; do not rebook what is already fixed unless cheap. "
            f"{ctx.KINDNESS}\n"
            'Return JSON {' + ('"question": "", ' if quiet else '') +
            '"candidates": [{"action": "", "owner": "", "moment": "", "effort": "low", "backup": "", "how_to_check": "", '
            '"if_check_fails": "", "early_sign": "", "when": "", "days_before": 2}]}'
        )
        out = ctx.ask_retry(ctx.usage_local(), ctx.PRO, "You are a practical, kind and honest planning advisor. Output one JSON object only.",
                            prompt, 1300, lambda o: o["candidates"][0]["action"], temperature=0.3)
        cands = []
        for k, c in enumerate(out["candidates"][:2]):
            mom = c.get("moment") if c.get("moment") in moments else (moments[0] if moments else "")
            try:
                days = max(0, int(c.get("days_before", 2)))
            except (TypeError, ValueError):
                days = 2
            cands.append({"id": f"{item['label']}-{k}", "themes": [item["label"]], "action": re.sub(r"^(me|i)\s*:\s*", "", str(c.get("action", "")).strip(), flags=re.I),
                          "owner": str(c.get("owner", "")).strip(), "moment": mom, "effort": str(c.get("effort", "medium")).lower(),
                          "response": item.get("response", "reduce"), "backup": str(c.get("backup", "")).strip(),
                          "how_to_check": str(c.get("how_to_check", "")).strip(), "if_check_fails": str(c.get("if_check_fails", "")).strip(),
                          "early_sign": str(c.get("early_sign", "")).strip(), "when": str(c.get("when", "")).strip(), "days_before": days})
        return item["label"], cands, out.get("question", "")

    with ThreadPoolExecutor(max_workers=7) as pool:
        res = list(pool.map(one, items))
    cands, question = [], ""
    for label, cs, q in res:
        cands.extend(c for c in cs if c["action"])
        if label == "quiet":
            question = q
    return cands, question


_GENERIC_PERSON = {"my", "her", "his", "their", "the", "a", "an", "team", "group", "people", "members", "staff", "s"}


def _head_noun(person):
    ws = [w for w in gr.norm(person).split() if w not in _GENERIC_PERSON]
    return ws[-1] if ws else ""


def _mentions(text, head):
    words = set(gr.norm(text).split())
    return head in words or head.rstrip("s") in words or head + "s" in words


def spotlight_cards(ctx, plan, worries, want):
    """The people-affected spotlight as 1-3 cards, with NO agreement count. (Counting was unstable: recounting the same
    worries of run 15a31e75 moved the top group from 11 to 8-9 and changed its topic; run bd4d228d's 49 mixed surprise,
    crowd, camera and parents.) Nova Pro reads the worries from the 'what the people affected actually want' angle and
    writes each card: the person, the plan words that affect them, a possible unmet need, what is unknown, the question
    to ask, and one representative worry by number. Code keeps a card only if its plan quote is found word for word and
    the worry number exists; the worry text shown is the worrier's own. Returns (summary for ranking['affected'], None)
    or (None, None) when nothing solid is found (the UI then says no distinct blind spot was found)."""
    sub = [w for w in worries if w.get("angle") == want and str(w.get("failure", "")).strip()]
    if len(sub) < 3:
        return None, None
    lines = "\n".join(f"W{i + 1}: {w['failure']}" for i, w in enumerate(sub[:120]))
    prompt = (
        f"Plan:\n<<<\n{plan}\n>>>\n\nEach worry below came from a worrier who looked only at what the people affected by "
        f"this plan actually want:\n{lines}\n\n"
        "Write 1 to 3 spotlight cards, each about a DIFFERENT person or a different need, most important first. Only write a "
        "card that several worries support. Each card:\n"
        "- person: who is affected, as the plan names them (max 5 words)\n"
        "- plan_quote: the words of the plan that affect them, copied word for word (max 14 words)\n"
        "- need: one specific thing they might want that the plan does not give them, as a possibility, never as a fact "
        "(max 18 words). Specific: 'a private moment without an audience', not 'to be comfortable with the plan'.\n"
        "- unknown: what the planner does not know yet (max 14 words)\n"
        "- question: one short, kind question for the planner to find out, addressed to the planner (max 14 words, "
        "e.g. 'Does she want a crowd at her proposal?'). It must not trick or deceive anyone.\n"
        "- worry: the number of the one worry that says this need most directly, like \"W7\"\n"
        'Return JSON {"cards": [{"person": "", "plan_quote": "", "need": "", "unknown": "", "question": "", "worry": "W1"}]} or {"cards": []}'
    )
    out = ctx.ask_retry(ctx.usage_local(), ctx.PRO, "You find what a plan overlooks about the people it affects. Output one JSON object only.",
                        prompt, 700, lambda o: isinstance(o["cards"], list), temperature=0.0)
    cards = []
    for c in out["cards"][:3]:
        if not isinstance(c, dict):
            continue
        q = gr.find_quote(str(c.get("plan_quote", "")), plan)
        try:
            w = sub[int(str(c.get("worry", "")).strip().upper().lstrip("W")) - 1]
        except (ValueError, IndexError):
            w = None
        need, ques = gr._s(c.get("need"), 200), gr._s(c.get("question"), 140)
        if not (q and w and need and ques):
            continue   # a card must point at the plan's own words and a real worry
        # code check: the worry must be about the card's person (its head noun as a whole word). A card about "her best
        # friend" backed by a worry about the girlfriend (run bd4d228d) gets a matching worry, or is dropped.
        head = _head_noun(c.get("person", ""))
        if head and not _mentions(w["failure"], head):
            alt = [x for x in sub if _mentions(x["failure"], head)]
            nw = set(gr.norm(need).split())
            w = max(alt, key=lambda x: len(nw & set(gr.norm(x["failure"]).split()))) if alt else None
            if not w:
                continue
        cards.append({"person": gr._s(c.get("person"), 60), "plan_quote": q, "need": need, "unknown": gr._s(c.get("unknown"), 160),
                      "question": ques, "worry": w["failure"], "seq": w["seq"], "character": w.get("character"), "moment": w.get("moment")})
    if not cards:
        return None, None
    top = cards[0]
    summary = {"worry": top["worry"], "concern": top["need"], "count": None, "of": len(sub), "avg_severity": None,
               "examples": [c["worry"] for c in cards], "character": top["character"], "moment": top["moment"],
               "spotlight": cards, "groups": None}
    return summary, None


def merge_duplicates(result, seq_label):
    """Two top findings with the same name are one finding: keep the larger, add the other's worriers to it."""
    seen, keep = {}, []
    for t in result["top5"]:
        k = gr.norm(t["name"])
        if k in seen:
            main = seen[k]
            main["count"] += t["count"]
            if main.get("grounding") and t.get("grounding"):
                for f in ("n", "n_quoted", "n_omission"):
                    main["grounding"][f] += t["grounding"][f]
            for s, l in list(seq_label.items()):
                if l == t["id"]:
                    seq_label[s] = main["id"]
            main["merged_from"] = main.get("merged_from", []) + [t["id"]]
        else:
            seen[k] = t
            keep.append(t)
    result["top5"] = sorted(keep, key=lambda t: -t["count"])


def _key_of(label):
    return label if label in ("quiet", "affected") else _tid(label)


def vet_fixes(ctx, plan, moments, items, cands, n_total, affected, goal=None):
    """Fix critic: keep only fixes that stop the checked mechanism with an observable check. A finding whose fixes all
    fail gets one rewrite; if that fails too it gets no fix and is marked for human review (a bad fix is worse than none)."""
    cards = {it["label"]: it.get("card") or {} for it in items}

    def judge(cs):
        pairs = [(c["themes"][0], cards.get(c["themes"][0], {}), c) for c in cs if cards.get(c["themes"][0])]
        # Nova Pro for both checks: on a fixed test set of real fixes (scratch critic_test.py, runs bd4d228d/15a31e75)
        # Lite passed 2 of 5 bad fixes; Pro critic + routing passed none, in two repeats.
        verdict = gr.critic(ctx.ask_retry, ctx.usage_local, ctx.PRO, plan, pairs, goal=goal_text(goal))
        route = gr.route_fixes(ctx.ask_retry, ctx.usage_local(), ctx.PRO, plan, cards,
                               [(c["id"], c["themes"][0], c["action"], c.get("how_to_check", "")) for c in cs if cards.get(c["themes"][0])])
        for c in cs:
            # no card, or the critic call failed: nothing to judge against, so only the code rule applies
            if c["id"] not in verdict:   # the critic call failed: keep the fix only if the code rule passes, marked unchecked
                c["unchecked"] = True
            ok, why = verdict.get(c["id"], (not gr.vague_check(c.get("how_to_check")), "not checked by the critic"))
            if not route:
                c["unchecked"] = True
            r_ok, r_why = route.get(c["id"], (True, ""))
            if ok and not r_ok:
                ok, why = False, r_why
            c["critic_ok"], c["critic_why"] = ok, why
        return [c for c in cs if c["critic_ok"]]

    good = judge(cands)
    have = {c["themes"][0] for c in good}
    redo = [dict(it, card=dict(it.get("card") or {}, failure=(it.get("card") or {}).get("failure", "") +
             " (Earlier fixes missed this mechanism: " + "; ".join(c["critic_why"] for c in cands if c["themes"][0] == it["label"] and c.get("critic_why")) + ")"))
            for it in items if it["label"] not in have]
    review = []
    if redo:
        again, _ = fix_candidates(ctx, plan, moments, redo, n_total, affected=affected, goal=goal)
        for c in again:
            c["id"] += "-r"
        good += judge(again)
        have = {c["themes"][0] for c in good}
        review = [_key_of(it["label"]) for it in items if it["label"] not in have]
    return good, review


# ------------------------------------------------------------------ main entry points
class Ctx:
    """Bundle of app.py helpers so this module stays free of circular imports."""
    def __init__(self, app, usage):
        self.ask_retry, self.LITE, self.PRO, self.KINDNESS = app.ask_retry, app.LITE, app.PRO, app.KINDNESS
        self.check_fixes, self.revise_plan, self.plan_facts = app.check_fixes, app.revise_plan, app.plan_facts
        self.Usage, self.usage = app.Usage, usage
        self._locals = []

    def usage_local(self):
        u = self.Usage()
        self._locals.append(u)
        return u

    def flush(self):
        for u in self._locals:
            self.usage.merge(u)
        self._locals = []


def analyse_run(app, usage, run_id, plan, worries, grid):
    ctx = Ctx(app, usage)
    facts = [f for f in (grid.get("facts") or []) if isinstance(f, str) and f.strip()] or app.plan_facts(usage, plan)
    moments = grid.get("moments") or []
    gcounts = gr.ground_worries(worries, plan)
    (W,) = wc.vectorize([_texts(worries)])
    S, F, G = wc.vectorize([[w["failure"] for w in worries], facts + [plan], wc.GENERIC_WORRIES], dims=0)
    state = wc.analyse(worries, W, F, G, params=wc.TFIDF_PARAMS, screen_vecs=S)
    state = split_big(state, W, worries)
    judgements = judge_clusters(ctx, plan, facts, state["ask"]["judge"]) if state["ask"]["judge"] else {}
    ranking = wc.rank(state, worries, W, judgements, want_angle=app.WANT)
    aref = None
    if ranking.get("affected"):
        try:
            aff, aref = spotlight_cards(ctx, plan, worries, app.WANT)
        except RuntimeError as e:
            print("affected grouping failed:", e)
            aff = None
        if aff:
            ranking["affected"] = aff
            for item in ranking["ask"]["fixes"]:
                if item["label"] == "affected":
                    item["examples"] = aff["examples"]
        else:
            # nothing solid: no spotlight card (the UI says no distinct blind spot was found)
            ranking["affected"] = None
            ranking["ask"]["fixes"] = [it for it in ranking["ask"]["fixes"] if it["label"] != "affected"]
    # A3 first: challenge each finding on a first-pass result, so each fix can target the checked mechanism
    first, seq_label = _to_result(worries, state, ranking, {"chosen": []}, [], "", [], facts, judgements, aref)
    central = {_tid(j["label"]): j["examples"] for j in state["ask"]["judge"]}
    cards, grounds, sample = add_cards(ctx, plan, worries, seq_label, first, aref, central=central)
    # Invented-cause check (Nova Pro): a finding whose chain needs a person, booking, payment or outside event the plan
    # never mentions is set aside, not ranked and not fixed. Graded by hand on runs bd4d228d / 15a31e75.
    # the spotlight cards already passed their own word-for-word plan-quote check, so they are not re-judged here
    basis = gr.basis_check(ctx.ask_retry, ctx.usage_local, ctx.PRO, plan, {k: v for k, v in cards.items() if k != "affected"},
                           members=sample)
    names = {_tid(t["label"]): t["name"] for t in ranking["themes"]}
    for key, b in basis.items():
        if cards.get(key):
            cards[key]["basis"], cards[key]["needs"] = b["basis"], b["needs"]
            cards[key]["represents"] = b.get("represents", "yes")
            if b["basis"] == "ask":   # an open question: the plan leaves this unsaid; find out, no fix
                cards[key]["question"], cards[key]["open_question"] = b["question"], True
            elif b["basis"] == "review":
                # the card does not represent its group: keep the group, drop the card's specific claims, no fix
                cards[key].update(needs_review=True, represents=b.get("represents", "no"),
                                  question=f"Review this group of worries: {names.get(key, 'this group')}. Does your plan cover it?")
            elif b["basis"] == "unavailable":
                cards[key]["validation_unavailable"] = True
    # "invented" removes a finding only when its card speaks for the group; a mixed group keeps its worries for review
    invented = {k for k, b in basis.items() if b["basis"] == "invented" and b.get("represents", "yes") == "yes"}
    for k, b in basis.items():
        if b["basis"] == "invented" and k not in invented and cards.get(k):
            cards[k].update(needs_review=True, question=f"Review this group of worries: {names.get(k, 'this group')}. Does your plan cover it?")
    asks = {k for k, b in basis.items() if b["basis"] in ("ask", "review") or (b["basis"] == "invented" and k not in invented)}
    fix_items = []
    for item in ranking["ask"]["fixes"]:
        key = item["label"] if item["label"] in ("quiet", "affected") else _tid(item["label"])
        if (cards.get(key) or {}).get("assessment") == "contradicted":
            continue   # the plan already covers this mechanism: no fix
        if key in invented or key in asks:
            continue   # set aside below, or an open question: no fix for a failure the plan gives no basis for
        fix_items.append({**item, "card": cards.get(key)})
    goal = goal_context(ctx, plan)
    cands, question = fix_candidates(ctx, plan, moments, fix_items, len(worries), affected=ranking.get("affected"), goal=goal)
    cands, review = vet_fixes(ctx, plan, moments, fix_items, cands, len(worries), ranking.get("affected"), goal=goal)
    card = wc.plan_card(ranking, cands, moments, budget=8)
    chosen = card["chosen"]
    if chosen:
        try:
            checks = app.check_fixes(ctx.usage_local(), plan, [{"fix": c["action"]} for c in chosen])
            for c, (risk, why) in zip(chosen, checks):
                c["introduces_risk"] = bool(risk)
                if risk:
                    c["risk_note"] = why
        except RuntimeError as e:
            print("v2 fix checker failed:", e)
    revised = []   # the revised plan is built after the planner decides (Revise state machine), not here
    result, seq_label = _to_result(worries, state, ranking, card, cands, question, revised, facts, judgements, aref)
    for h, key in [(t, t["id"]) for t in result["top5"]] + [(result.get("affected"), "affected"), (result.get("quiet"), "quiet")]:
        if h:
            h["card"], h["grounding"] = cards.get(key), grounds.get(key)
            if key not in ("affected", "quiet") and (cards.get(key) or {}).get("name") and not cards[key].get("needs_review"):
                h["cluster_name"], h["name"] = h["name"], cards[key]["name"]   # name the checked failure, not the cluster
    result["grounding"] = gcounts
    result["goal_context"] = goal   # interpreted from the plan, shown as such
    merge_duplicates(result, seq_label)
    for h, key in [(t, t["id"]) for t in result["top5"]] + [(result.get("affected"), "affected"), (result.get("quiet"), "quiet")]:
        if h and key in review:
            h["fix"], h["fix_review"] = "", True
    set_aside = [{"name": t["name"], "count": t["count"], "needs": cards[t["id"]]["needs"], "failure": cards[t["id"]].get("failure", ""),
                  "mechanism": cards[t["id"]].get("mechanism", "")} for t in result["top5"] if t["id"] in invented]
    result["top5"] = [t for t in result["top5"] if t["id"] not in invented]
    for key in ("affected", "quiet"):
        h = result.get(key)
        if h and key in invented:
            set_aside.append({"name": (h.get("card") or {}).get("name") or h.get("worry", "")[:60], "count": h.get("count", 1), "needs": cards[key]["needs"],
                              "failure": cards[key].get("failure", ""), "mechanism": cards[key].get("mechanism", "")})
            result[key] = None
    result["set_aside"] = set_aside
    for t in result["top5"] + [result.get("quiet")]:
        if not t:
            continue
        cd = t.get("card") or {}
        if cd.get("open_question"):
            t["open_question"], t["fix"], t["fix_review"] = True, "", False
        if cd.get("needs_review"):
            t["needs_review"], t["fix"], t["fix_review"] = True, "", False
        t["validation"] = ("needs_review" if cd.get("needs_review") else "open_question" if cd.get("open_question")
                           else "unavailable" if cd.get("validation_unavailable") or not cd else "checked")
    ctx.flush()

    s3.put_object(Bucket=BUCKET, Key=_key(run_id), ContentType="application/json",
                  Body=json.dumps(_jsonable({"state": state, "ranking": ranking, "card": card, "facts": facts,
                                             "judgements": judgements, "candidates": cands, "affected_ref": aref})))
    return result, seq_label


def add_cards(ctx, plan, worries, seq_label, result, aref, central=None):
    """A3: challenge each finding the planner will review (top 5, the affected card, the quiet worry) against the plan.
    central: {"C<n>": the cluster's most central worries}. Cards are written from those, not from the three most severe
    members (severity-picked examples let a card describe a corner of the cluster). Also returns, per finding, an evenly
    spread sample of member worries for the representativeness check."""
    by_seq = {str(w["seq"]): w for w in worries}
    members = {}
    for seq, lab in seq_label.items():
        members.setdefault(lab, []).append(by_seq[seq])
    finds, holders = [], {}
    for t in result["top5"]:
        holders[t["id"]] = t
        ms = members.get(t["id"], [])
        ex = (central or {}).get(t["id"]) or t.get("examples") or [m["failure"] for m in ms[:5]]
        finds.append({"key": t["id"], "examples": ex, **gr.member_evidence(ms)})
    a = result.get("affected")
    if a:
        holders["affected"] = a
        ms = [by_seq[str(s)] for s in (aref or {}).get("member_seqs", []) if str(s) in by_seq] or \
             [w for w in worries if w["failure"] == a["worry"]]
        finds.append({"key": "affected", "examples": [a["worry"]] + (a.get("examples") or []), **gr.member_evidence(ms)})
    q = result.get("quiet")
    if q:
        holders["quiet"] = q
        ms = [w for w in worries if w["failure"] == q["worry"]]
        finds.append({"key": "quiet", "examples": [q["worry"]], **gr.member_evidence(ms)})
    cards = gr.challenge(ctx.ask_retry, ctx.usage_local, ctx.LITE, plan, finds)
    sample = {}
    for t in result["top5"]:
        ms = members.get(t["id"], [])
        step = max(1, len(ms) // 8)
        sample[t["id"]] = [m["failure"] for m in ms[::step][:8]]   # evenly spread, deterministic
    return cards, {f["key"]: {k: f[k] for k in ("n", "n_quoted", "n_omission")} for f in finds}, sample


def _best_fix(label, chosen, cands):
    for pool in (chosen, cands):
        for c in pool:
            if label in c.get("themes", []):
                return c
    return None


def _to_result(worries, state, ranking, card, cands, question, revised, facts, judgements, aref=None):
    keep, lab = state["keep"], ranking["labels_after_judge"]
    seq_label = {}
    for i, w in enumerate(worries):
        seq_label[str(w["seq"])] = "filtered"
    for pos, l in enumerate(lab):
        seq = str(worries[keep[pos]]["seq"])
        seq_label[seq] = "offplan" if l == -3 else ("other" if l < 0 else _tid(l))
    chosen = card["chosen"]
    top5 = []
    for t in ranking["themes"]:
        if t["label"] not in ranking["top"]:
            continue
        f = _best_fix(t["label"], chosen, cands) or {}
        top5.append({
            "id": _tid(t["label"]), "name": t["name"], "definition": t["definition"], "count": int(round(t["count"])),
            # cluster_count: weighted size of the TEXT CLUSTER (grouped by wording). The finding card may describe only part
            # of it, so the UI never presents it as agreement with the finding (run d9db470a: 38 of 136 members matched).
            "cluster_name": t["name"], "cluster_count": int(round(t["count"])), "count_basis": "text_cluster",
            "avg_severity": t.get("impact"), "risk": t.get("risk"), "likelihood": t.get("likelihood"),
            "risk_lo": t.get("risk_lo"), "risk_hi": t.get("risk_hi"), "top5_share": t.get("top5_share"),
            "response": t.get("response"), "controllability": t.get("controllability"), "examples": t.get("examples", []),
            "fix": f.get("action", ""), "owner": f.get("owner", ""), "effort": f.get("effort", ""), "fix_moment": f.get("moment", ""),
            "backup": f.get("backup", ""), "how_to_check": f.get("how_to_check", ""), "if_check_fails": f.get("if_check_fails", ""), "partial": bool(f.get("partial")), "still_open": f.get("still_open", ""), "fix_unchecked": bool(f.get("unchecked")),
            "early_sign": f.get("early_sign", ""), "when": f.get("when", ""),
            "days_before": f.get("days_before", 2), "introduces_risk": f.get("introduces_risk", False), "risk_note": f.get("risk_note", ""),
        })
    q = None
    rq = ranking.get("quiet")
    if rq:
        match = next((w for w in worries if w["failure"] == rq["worry"] and w.get("character") == rq.get("character")), None) \
            or next((w for w in worries if w["failure"] == rq["worry"]), None)
        f = _best_fix("quiet", chosen, cands) or {}
        q = {"worry": rq["worry"], "seq": match["seq"] if match else None, "character": rq.get("character"), "moment": rq.get("moment"),
             "angle": rq.get("angle"), "severity": rq["severity"], "count": rq["count"], "cluster_count": rq["count"], "count_basis": "text_cluster_of_quiet_worry", "isolation": rq.get("isolation"),
             "label": rq.get("label"), "question": question or rq["worry"], "fix": f.get("action", ""), "backup": f.get("backup", ""),
             "owner": f.get("owner", ""), "how_to_check": f.get("how_to_check", ""), "if_check_fails": f.get("if_check_fails", ""), "partial": bool(f.get("partial")), "still_open": f.get("still_open", ""), "fix_unchecked": bool(f.get("unchecked")),
             "early_sign": f.get("early_sign", ""), "when": f.get("when", ""), "days_before": f.get("days_before", 2),
             "introduces_risk": f.get("introduces_risk", False), "risk_note": f.get("risk_note", "")}
    a = None
    ra = ranking.get("affected")
    if ra:
        f = _best_fix("affected", chosen, cands) or {}
        rep = next((w for w in worries if aref and w["seq"] == aref["rep_seq"]), None) \
            or next((w for w in worries if ra.get("spotlight") and w["seq"] == ra["spotlight"][0]["seq"]), None) \
            or next((w for w in worries if w["failure"] == ra["worry"]), None)
        a = {"worry": ra["worry"], "concern": ra.get("concern"), "other_concerns": ra.get("groups"), "count": ra["count"], "of": ra["of"], "avg_severity": ra.get("avg_severity"),
             "spotlight": ra.get("spotlight"),
             "examples": ra.get("examples", []), "seq": rep["seq"] if rep else None,
             "character": (rep or {}).get("character", ra.get("character")), "moment": (rep or {}).get("moment", ra.get("moment")),
             "fix": f.get("action", ""), "owner": f.get("owner", ""), "effort": f.get("effort", ""), "fix_moment": f.get("moment", ""),
             "backup": f.get("backup", ""), "how_to_check": f.get("how_to_check", ""), "if_check_fails": f.get("if_check_fails", ""), "partial": bool(f.get("partial")), "still_open": f.get("still_open", ""), "fix_unchecked": bool(f.get("unchecked")),
            "early_sign": f.get("early_sign", ""), "when": f.get("when", ""),
             "days_before": f.get("days_before", 2), "introduces_risk": f.get("introduces_risk", False), "risk_note": f.get("risk_note", ""),
             "chosen": any("affected" in c.get("themes", []) for c in chosen)}
    counts = {}
    for v in seq_label.values():
        counts[v] = counts.get(v, 0) + 1
    top_ids = {_tid(l) for l in ranking["top"]}
    sev = {str(w["seq"]): int(w["severity"]) for w in worries}
    return {
        "tally": "v2",
        "n_worries": len(worries),
        "other": counts.get("other", 0) + counts.get("filtered", 0),
        "offplan": counts.get("offplan", 0),
        "offplan_examples": [w["failure"] for w in worries if seq_label[str(w["seq"])] == "offplan"][:8],
        "plan_facts": facts,
        "top5": top5,
        "quiet": q,
        "affected": a,
        "themes": [{"id": _tid(t["label"]), "name": t["name"], "definition": t["definition"], "count": int(round(t["count"])),
                    "avg_severity": t.get("impact"), "risk": t.get("risk")} for t in ranking["themes"]],
        "funnel": ranking["funnel"], "health": state.get("health", {}),
        "plan_card": [{k: c.get(k) for k in ("id", "action", "owner", "moment", "effort", "response", "backup", "how_to_check", "if_check_fails", "early_sign", "partial", "still_open",
                                               "when", "days_before", "themes", "introduces_risk", "risk_note")} for c in chosen],
        "revised_plan": revised,
        "total_severity": sum(sev.values()),
        "top5_severity": sum(v for k, v in sev.items() if seq_label.get(k) in top_ids),
    }, seq_label


def _load(key):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None
    except s3.exceptions.ClientError as e:   # without s3:ListBucket a missing key comes back as AccessDenied
        if e.response.get("Error", {}).get("Code") in ("AccessDenied", "403", "404", "NoSuchKey"):
            return None
        raise


def _retest_affected(pa, aref, parent_worries, worries, PW, NW, rt, want):
    """Before -> after for the affected card: the re-run's worriers with the WANT angle (kept by the screen) are mapped
    by kNN (worrycore.assign_knn) onto the parent's WANT-angle sub-clusters; count those landing on the affected one."""
    if not pa:
        return None
    a = dict(pa)
    a["before"], a["of_before"] = pa.get("count"), pa.get("of")
    if not aref:
        return a
    pidx = {w["seq"]: i for i, w in enumerate(parent_worries)}
    ref = [(pidx[s], l) for s, l in zip(aref["seqs"], aref["labels"]) if s in pidx]
    sel = [(i, wt) for i, wt in zip(rt["keep"], rt.get("weights") or [1.0] * len(rt["keep"])) if worries[i].get("angle") == want]
    if len(ref) < 3 or not sel:
        a["count"], a["of"] = 0, int(round(sum(wt for _, wt in sel)))
        return a
    labs, _ = wc.assign_knn(np.asarray(NW)[[i for i, _ in sel]], np.asarray(PW)[[i for i, _ in ref]], [l for _, l in ref])
    # same counting as worrycore (near-duplicates share one vote)
    a["count"] = int(round(sum(wt for (_, wt), l in zip(sel, labs) if l == aref["target"])))
    a["of"] = int(round(sum(wt for _, wt in sel)))
    return a


def retest_run(app, usage, run_id, parent_id, plan, worries, parent_worries, parent_result, noise_run=False, reports=None,
               revision=None):
    """revision: the canonical ledger from the Revise workflow (re-tests started with a revision_id). When present, the
    worriers read its effective_plan_text, the assessments read its plan lines + accepted changes, its open questions are
    passed separately (never as safeguards), and the shown revised plan is its own bullets (no second rewrite)."""
    ctx = Ctx(app, usage)
    reports = reports or []
    gcounts = gr.ground_worries(worries, plan)
    pv2 = json.loads(s3.get_object(Bucket=BUCKET, Key=_key(parent_id))["Body"].read())
    pstate, pranking, facts = pv2["state"], pv2["ranking"], pv2.get("facts") or []
    PW, NW = wc.vectorize([_texts(parent_worries), _texts(worries)])
    NS, F, G = wc.vectorize([[w["failure"] for w in worries], facts + [plan], wc.GENERIC_WORRIES], dims=0)
    noise = None if noise_run else _load(f"results/{parent_id}/noise.json")
    if noise:
        noise["compare"]["themes"] = {int(k): v for k, v in noise["compare"]["themes"].items()}
    rt = wc.retest(pstate, pranking, PW, worries, NW, F, G, noise=noise, params=wc.TFIDF_PARAMS, new_screen_vecs=NS)
    if noise_run:  # the same plan again: this run-to-run variation is what later re-tests must beat
        s3.put_object(Bucket=BUCKET, Key=f"results/{parent_id}/noise.json", ContentType="application/json",
                      Body=json.dumps(_jsonable({"compare": rt["compare"], "run_id": run_id})))
    names = {t["label"]: t["name"] for t in pranking["themes"]}
    comparison = []
    shown = {t["id"] for t in parent_result.get("top5", [])}
    for l in pranking["top"]:
        if _tid(l) not in shown:
            continue   # set aside as invented (or merged): not a finding the planner saw
        c = rt["compare"]["themes"].get(l) or rt["compare"]["themes"].get(str(l)) or {}
        v = rt["verdicts"]["themes"].get(l) or rt["verdicts"]["themes"].get(str(l))
        comparison.append({"id": _tid(l), "name": names.get(l, f"Theme {l}"), "before": c.get("before", 0), "after": c.get("after", 0),
                           "change": c.get("change", 0), "ci90": c.get("ci90"), "verdict": v})
    tot = rt["compare"]["total"]
    seq_label = {str(w["seq"]): "filtered" for w in worries}
    for pos, l in enumerate(rt["labels"]):
        seq_label[str(worries[rt["keep"][pos]]["seq"])] = "new" if l == -2 else ("other" if l < 0 else _tid(l))
    q = dict(parent_result.get("quiet") or {}) or None
    if q and q.get("label") is not None and int(q["label"]) >= 0:
        nq = sum(1 for l in rt["labels"] if l == int(q["label"]))
        q["before"], q["count"] = q.get("count"), nq
    a = _retest_affected(parent_result.get("affected"), pv2.get("affected_ref"), parent_worries, worries, PW, NW, rt, app.WANT)
    questions = []
    if revision:
        revised = revision.get("bullets") or []
        base = " ".join(str(x.get("text", "")) for x in revision.get("plan_lines") or []) or revision.get("original_plan", "")
        changes = [str(c.get("action", "")) for c in revision.get("accepted_changes") or [] if c.get("action")]
        questions = [str(q.get("question", "")) for q in revision.get("open_questions") or [] if q.get("question")]
        reports = [str(r.get("text", "")) for r in revision.get("planner_reports") or [] if r.get("text")] or reports
    else:   # older clients: parent plan + appended change lines
        try:
            revised = app.revise_plan(ctx.usage_local(), plan) if "Changes made to the plan:" in plan else []
        except RuntimeError:
            revised = []
        base, _, tail = plan.partition("\n\nChanges made to the plan:\n")
        changes = [ln[2:].strip() for ln in tail.splitlines() if ln.startswith("- ") and ln[2:].strip()]
        # old "Find out before the day:" lines are questions, not changes
        questions = [c.split(":", 1)[1].strip() for c in changes if c.lower().startswith("find out")]
        changes = [c for c in changes if not c.lower().startswith("find out")]
    assessments, concerns = {}, []
    if not noise_run:
        orig = [(k, h) for k, h in [(t["id"], t) for t in parent_result.get("top5", [])]
                + [("affected", parent_result.get("affected")), ("quiet", parent_result.get("quiet"))] if h and h.get("card")]
        label = {"affected": "What she actually wants" if "propos" in base.lower() else "What the people affected want", "quiet": "The quiet worry"}
        cards = [{"key": k, "name": h.get("name") if k not in label else label[k], "failure": h["card"]["failure"] or h.get("name") or h.get("worry", ""),
                  "mechanism": h["card"]["mechanism"], "evidence": h["card"]["evidence"]} for k, h in orig]
        assessments = gr.assess_original(ctx.ask_retry, ctx.usage_local, ctx.LITE, base, changes, reports, cards, questions=questions)
        try:
            allnew = [worries[rt["keep"][i]]["failure"] for i, l in enumerate(rt["labels"]) if l == -2]
            ex = [allnew[int(j * len(allnew) / 12)] for j in range(12)] if len(allnew) > 12 else allnew   # spread, not the first few
            concerns = gr.new_concerns(ctx.ask_retry, ctx.usage_local(), ctx.PRO, plan, changes, ex,
                                       original=[c["failure"] for c in cards])
        except RuntimeError as e:
            print("new concerns failed:", e)
    ctx.flush()
    sev = sum(int(w["severity"]) for w in worries)
    return {
        "tally": "v2", "n_worries": len(worries), "parent_run_id": parent_id, "parent_n": parent_result.get("n_worries"),
        "top5": parent_result.get("top5", []), "quiet": q, "affected": a, "themes": parent_result.get("themes", []),
        "comparison": comparison, "per1000": True,
        "severity_before": tot["before"], "severity_after": tot["after"], "severity_change_ci90": tot["ci90"],
        "total_verdict": rt["verdicts"]["total"], "new_worries": tot.get("new_worries", 0),
        "new_worry_examples": rt.get("new_worry_examples", []),
        "top5_count_before": round(sum(c["before"] for c in comparison), 1), "top5_count_after": round(sum(c["after"] for c in comparison), 1),
        "funnel": rt["funnel"], "revised_plan": revised, "total_severity": sev,
        "noise_run": bool(noise_run), "noise_checked": bool(noise),
        "assessments": assessments, "new_concerns": concerns, "planner_reports": reports, "grounding": gcounts,
        "revision_id": (revision or {}).get("revision_id", ""), "open_questions": questions, "accepted_changes": changes,
        "other": sum(1 for v in seq_label.values() if v in ("other", "filtered")), "offplan": 0,
    }, seq_label
