"""Tally: turn N raw worries into the top 5 failure modes with real counts, plus one quiet worry.

New run:
1. Nova Lite proposes 8-12 themes, each with a one-line definition, from a sample of up to 150 worries.
2. Nova Lite (temperature 0) puts every worry into exactly one theme or "other", in batches of 50.
3. Counts are computed here, in code. The model never reports a number.
4. Quiet worry: the highest-severity worry from the "what the people affected actually want" angle.
   Nova Lite marks which worries share its concern; the count is computed in code.
5. Nova Pro writes a fix, a backup and an early-warning sign for each top-5 theme and the quiet worry (6 calls).

Re-test (parent_run_id set): reuse the parent's themes and quiet concern, classify the new worries into them,
and report before/after counts per theme.
"""
import json
import os
import sys
import random
import re
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config

TABLE = os.environ["TABLE_NAME"]
LITE = os.environ["LITE_MODEL"]
PRO = os.environ["PRO_MODEL"]
WANT = "what the people affected actually want"
SAMPLE = 150
BATCH = 50

ddb = boto3.client("dynamodb")
br = boto3.client("bedrock-runtime", config=Config(retries={"max_attempts": 10, "mode": "adaptive"}, read_timeout=120))

KINDNESS = (
    "Honesty and kindness rules for every fix and backup: never deceive, mislead, lie to, manipulate or pressure "
    "anyone at all, including the person most affected, their parents and family, friends, staff and strangers. "
    "Do not invent cover stories, excuses or false reasons, and do not repeat or extend any cover story the plan "
    "already uses (for example never tell someone a fake reason, even the one in the plan). Keeping a pleasant "
    "surprise is fine (simply not telling yet), but never by saying anything false. Where another person's feelings or choices are involved, respect their right to "
    "choose, to say no, or to want something different."
)

OTHER_DEF = "other: the worry does not clearly match any theme definition above."
OFFPLAN_DEF = ("offplan: the worry depends on a person, place, vehicle, room, shop, booking, item or cost that the plan "
               "does not mention (for example a rental car or a hotel the plan never names). Check this first.")


class Usage:
    def __init__(self):
        self.t = {"lite_in": 0, "lite_out": 0, "lite_calls": 0, "pro_in": 0, "pro_out": 0, "pro_calls": 0}

    def add(self, model, resp):
        k = "pro" if model == PRO else "lite"
        self.t[f"{k}_in"] += resp["usage"]["inputTokens"]
        self.t[f"{k}_out"] += resp["usage"]["outputTokens"]
        self.t[f"{k}_calls"] += 1

    def merge(self, other):
        for k, v in other.t.items():
            self.t[k] += v


def ask(usage, model, system, prompt, max_tokens, temperature):
    resp = br.converse(
        modelId=model,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    usage.add(model, resp)
    text = resp["output"]["message"]["content"][0]["text"]
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"no JSON in model output: {text[:200]}")
    return json.loads(m.group(0))


def ask_retry(usage, model, system, prompt, max_tokens, check, temperature=0.2, tries=3):
    last = None
    for _ in range(tries):
        try:
            out = ask(usage, model, system, prompt, max_tokens, temperature)
            check(out)
            return out
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            last = e
    raise RuntimeError(f"model output unusable after {tries} tries: {last}")


def get_meta(run_id):
    it = ddb.get_item(TableName=TABLE, Key={"pk": {"S": f"run#{run_id}"}, "sk": {"S": "META"}},
                      ConsistentRead=True).get("Item")
    if not it:
        raise RuntimeError(f"run {run_id} not found")
    return it


def _quotes(it):
    """Plan quotes a worrier gave (stored as a JSON list string); [] for older runs."""
    raw = it.get("quotes", {}).get("S", "") or ""
    try:
        q = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        q = raw          # a bare string (JSONata can unwrap a one-item list)
    if isinstance(q, str):
        q = [q] if q.strip() else []
    return [str(x) for x in q if isinstance(x, str) and x.strip()][:2] if isinstance(q, list) else []


def load_worries(run_id):
    items, kwargs = [], dict(
        TableName=TABLE,
        KeyConditionExpression="pk = :pk AND begins_with(sk, :w)",
        ExpressionAttributeValues={":pk": {"S": f"run#{run_id}"}, ":w": {"S": "W#"}},
    )
    while True:
        page = ddb.query(**kwargs)
        for it in page["Items"]:
            items.append({
                "seq": int(it["seq"]["N"]),
                "character": it["character"]["S"],
                "angle": it["angle"]["S"],
                "moment": it["moment"]["S"],
                "failure": it["failure"]["S"],
                "severity": int(it["severity"]["N"]),
                "early_sign": it.get("early_sign", {}).get("S", ""),
                "cause": it.get("cause", {}).get("S", ""),
                "quotes": _quotes(it),
                "missing": it.get("missing", {}).get("S", ""),
            })
        if "LastEvaluatedKey" not in page:
            return sorted(items, key=lambda w: w["seq"])
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def propose_themes(usage, plan, worries, run_id):
    rnd = random.Random(run_id)
    sample = worries if len(worries) <= SAMPLE else rnd.sample(worries, SAMPLE)
    lines = "\n".join(f"- {w['failure']}" for w in sample)
    lo, hi = (8, 12) if len(worries) >= 60 else (5, 8)
    prompt = (
        f"Plan:\n<<<\n{plan}\n>>>\n\nWorries imagined about this plan:\n{lines}\n\n"
        f"Group these worries into {lo} to {hi} distinct failure themes. Each theme is one root cause "
        "(what goes wrong), not a consequence shared by many worries (such as 'the plan is ruined'). "
        "Name each theme in plain words. Give each a one-line definition that says what belongs in it "
        "and, where useful, what does not. Themes must not overlap: each worry should fit exactly one theme, and the same "
        "object or person (an item, a booking, a helper) must not appear in two definitions.\n"
        'Return JSON {"themes": [{"id": "T1", "name": "<max 6 words>", "definition": "<one line, max 25 words>"}]}'
    )

    def check(out):
        ts = out["themes"]
        if not (3 <= len(ts) <= 14) or not all(t.get("name") and t.get("definition") for t in ts):
            raise ValueError("bad theme list")

    out = ask_retry(usage, LITE, "You cluster risks for a pre-mortem. Output one JSON object only.", prompt, 1500, check)
    return [{"id": f"T{i}", "name": t["name"].strip(), "definition": t["definition"].strip()}
            for i, t in enumerate(out["themes"], 1)]


def in_batches(usage, worries, fn):
    batches = [worries[i:i + BATCH] for i in range(0, len(worries), BATCH)]

    def run(batch):
        u = Usage()
        return fn(u, batch), u

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(run, batches))
    out = []
    for part, u in results:
        out.extend(part)
        usage.merge(u)
    return out


def classify(usage, plan, themes, worries):
    legend = "\n".join(f"{t['id']}: {t['name']}. Definition: {t['definition']}" for t in themes) + "\n" + OTHER_DEF
    valid = {t["id"] for t in themes} | {"other"}

    def fn(u, batch):
        lines = "\n".join(f"{i}: {w['failure']}" for i, w in enumerate(batch))
        prompt = (
            f"Plan:\n<<<\n{plan}\n>>>\n\nThemes:\n{legend}\n\nWorries:\n{lines}\n\n"
            "Assign every worry to the ONE theme whose definition best matches its root cause "
            "(what goes wrong first), not its consequence. Use \"other\" when no definition clearly fits.\n"
            'Return JSON {"labels": [{"i": 0, "t": "T3"}, ...]} with one entry per worry.'
        )

        def check(out):
            if not isinstance(out["labels"], list):
                raise ValueError("labels not a list")

        out = ask_retry(u, LITE, "You label risks with theme ids. Output one JSON object only.", prompt,
                        120 + 25 * len(batch), check, temperature=0.0)
        labels = {}
        for lab in out["labels"]:
            try:
                i = int(lab["i"])
            except (KeyError, TypeError, ValueError):
                continue
            t = str(lab.get("t", "other")).strip()
            if 0 <= i < len(batch):
                labels[i] = t if t in valid else "other"
        # Anything the model skipped is counted as "other", never guessed.
        return [labels.get(i, "other") for i in range(len(batch))]

    return in_batches(usage, worries, fn)


def plan_facts(usage, plan):
    prompt = (
        f"Plan:\n<<<\n{plan}\n>>>\n\n"
        "List every concrete thing this plan states: people, places, times, vehicles or ways of travel, bookings, "
        "items, payments, apps and events. Quote them as the plan says them; add nothing the plan does not say.\n"
        'Return JSON {"facts": ["..."]}'
    )

    def check(out):
        if not isinstance(out["facts"], list) or not out["facts"]:
            raise ValueError("no facts")

    out = ask_retry(usage, LITE, "You extract facts. Output one JSON object only.", prompt, 800, check, temperature=0.0)
    return [str(f) for f in out["facts"]]


STOP = {"the", "a", "an", "of", "to", "for", "and", "or", "with", "in", "on", "at", "his", "her", "their", "my", "our",
        "your", "its", "this", "that", "none", "from", "by"}


def plan_words(plan):
    words = set(re.findall(r"[a-z0-9']+", plan.lower()))
    return words | {w.rstrip("s") for w in words} | {w.rstrip("'s") for w in words}


def off_plan(usage, plan, facts, worries):
    """One bool per worry. The model must NAME what is not in the plan; code then checks those words against
    the plan text, so anything the plan actually mentions can never be set aside."""
    fact_list = "\n".join(f"- {f}" for f in facts)
    pw = plan_words(plan)

    def fn(u, batch):
        lines = "\n".join(f"{i}: {w['failure']}" for i, w in enumerate(batch))
        prompt = (
            f"Facts stated in the plan:\n{fact_list}\n\nWorries:\n{lines}\n\n"
            "For EACH worry, decide whether it ADDS a new dependency the plan never mentions or clearly implies: a "
            "specific kind of vehicle or ride service (e.g. \"rental car\", \"taxi\"), lodging (e.g. \"hotel room\"), "
            "a shop or service provider, a booking or ticket, a document or fee, or a new person with a role. "
            "Things the plan clearly implies are NOT new (a drive implies a car and a road; a dinner implies a table). "
            "Ordinary background is NOT new: weather, tides, roads, traffic, crowds, passers-by, police, phones, "
            "batteries, health, moods, family of people in the plan, and people's reactions.\n"
            "Give c = one of vehicle, lodging, service, booking, document_or_fee, person, none; and x = the new thing "
            "in 1-3 words (or \"none\").\n"
            'Return JSON {"answers": [{"i": 0, "c": "none", "x": "none"}, ...]} with one entry per worry.'
        )

        def check(out):
            if not isinstance(out["answers"], list):
                raise ValueError("answers not a list")

        out = ask_retry(u, LITE, "You check worries against a plan strictly. Output one JSON object only.", prompt,
                        120 + 25 * len(batch), check, temperature=0.0)
        named = {}
        for x in out["answers"]:
            try:
                cat = str(x.get("c", "none")).strip().lower()
                if cat in {"vehicle", "lodging", "service", "booking", "document_or_fee", "person"}:
                    named[int(x["i"])] = str(x.get("x", "none")).strip().lower()
            except (TypeError, ValueError, KeyError, AttributeError):
                pass
        res = []
        for i in range(len(batch)):
            thing = named.get(i, "none")
            words = [w for w in re.findall(r"[a-z0-9']+", thing) if w not in STOP]
            # Off-plan only if the model named something AND none of its words appear in the plan
            # ("restaurant reservation" stays in: the plan books a restaurant; "taxi" goes: the plan never mentions one).
            res.append(bool(words) and all(w not in pw and w.rstrip("s") not in pw for w in words))
        return res

    return in_batches(usage, worries, fn)


def same_concern(usage, concern, worries):
    """One bool per worry: does it express the same underlying concern? Yes/no per worry, temperature 0."""

    def fn(u, batch):
        lines = "\n".join(f"{i}: {w['failure']}" for i, w in enumerate(batch))
        prompt = (
            f"Concern: {concern}\n\nWorries:\n{lines}\n\n"
            "For EACH worry answer \"yes\" only if it is about the same underlying concern: the same root cause, "
            "about the same person's own wishes, feelings or choice. Answer \"no\" if it merely shares a person, place, "
            "moment or consequence. Most worries will be \"no\".\n"
            'Return JSON {"answers": [{"i": 0, "a": "no"}, ...]} with one entry per worry.'
        )

        def check(out):
            if not isinstance(out["answers"], list):
                raise ValueError("answers not a list")

        out = ask_retry(u, LITE, "You compare risks strictly. Output one JSON object only.", prompt,
                        120 + 25 * len(batch), check, temperature=0.0)
        hits = set()
        for x in out["answers"]:
            try:
                if str(x.get("a", "")).strip().lower().startswith("y"):
                    hits.add(int(x["i"]))
            except (TypeError, ValueError, KeyError, AttributeError):
                pass
        return [i in hits for i in range(len(batch))]

    return in_batches(usage, worries, fn)


def write_fix(plan, theme, n_total, quiet=False, avoid=None):
    u = Usage()
    ex = "\n".join(f"- {e}" for e in theme["all_examples"])
    head = (f"Concern raised by only {theme['count']} of {n_total} worriers, but it matters: {theme['name']}\n"
            if quiet else
            f"Failure theme: {theme['name']}. Definition: {theme['definition']}\n"
            f"{theme['count']} of {n_total} worriers imagined a failure of this kind, for example:\n")
    question = ('- question: this concern as a short, kind question the planner should ask themselves, max 10 words '
                '(e.g. "Does she want this in public?").\n') if quiet else ""
    prompt = (
        f"Plan:\n<<<\n{plan}\n>>>\n\n{head}{ex}\n\n"
        "Write advice for the person making this plan.\n"
        f"{question}"
        "- fix: ONE specific change to THIS plan that prevents this kind of failure. Name the actual people, places, "
        "times or items from the plan. It must be something the planner can do themselves, cheaply, before the day. Max 30 words.\n"
        "- backup: what the planner does on the day if it still goes wrong despite the fix. It must fit with the fix "
        "and the rest of the plan, and be doable on the spot. Max 25 words.\n"
        "- early_sign: one observable warning sign of THIS concern that the planner can actually check. Max 15 words.\n"
        "- when: when to check for it, relative to the main event, max 8 words (e.g. \"2 days before\").\n"
        "- days_before: that timing as a whole number of days before the main event (0 = same day).\n"
        "Rules: be practical and concrete. Use only facts in the plan. Never invent names: refer to people by their "
        "role in the plan (e.g. \"her best friend\", \"the two friends flying in\"). Do not add new people, guides, agencies, "
        "vendors or services to the fix. No contracts, NDAs, signed agreements, legal "
        "steps, insurance, or hiring professionals the plan does not already involve. Do not rebook or replace things "
        "already fixed in the plan (dates, tickets, venues) unless that is cheap and easy. Do not repeat the worry back as advice.\n"
        "Keep what the planner clearly intends (for example a surprise) unless this concern is about that choice itself. "
        "If the fix needs to learn what someone wants, prefer an honest way that keeps an intended surprise, such as "
        "openly asking someone who knows them well.\n"
        f"{KINDNESS}\n"
        + (f"A previous fix for this concern was rejected because it adds a new risk: {avoid} "
           "Write a different fix that does not create that risk or any new dependency.\n" if avoid else "") +
        'Return JSON {' + ('"question": "", ' if quiet else '') +
        '"fix": "", "backup": "", "early_sign": "", "when": "", "days_before": 2}'
    )

    def check(out):
        need = ("fix", "backup", "early_sign") + (("question",) if quiet else ())
        if not all(out.get(k) for k in need):
            raise ValueError("missing fields")

    out = ask_retry(u, PRO, "You are a practical, kind and honest planning advisor. Output one JSON object only.",
                    prompt, 600, check, temperature=0.3)
    return out, u


def check_fixes(usage, plan, cards):
    """One Nova Lite call (temperature 0): does each fix introduce a new risk or a new dependency?"""
    lines = "\n".join(f"{i}: {c['fix']}" for i, c in enumerate(cards))
    prompt = (
        f"Plan:\n<<<\n{plan}\n>>>\n\nProposed fixes:\n{lines}\n\n"
        "For EACH fix, answer \"yes\" if doing it would introduce a NEW risk or a NEW dependency: a new way the plan "
        "could fail (for example telling more people a secret, so it may leak), or a new person, booking, item or "
        "payment the plan now relies on. Answer \"no\" if it only removes risk using what the plan already has.\n"
        'Return JSON {"checks": [{"i": 0, "risk": "no", "why": "<max 15 words, empty if no>"}]} with one entry per fix.'
    )

    def check(out):
        if not isinstance(out["checks"], list):
            raise ValueError("checks not a list")

    out = ask_retry(usage, LITE, "You review advice for hidden risks. Output one JSON object only.", prompt,
                    80 + 40 * len(cards), check, temperature=0.0)
    res = [(False, "") for _ in cards]
    for x in out["checks"]:
        try:
            i = int(x["i"])
            if 0 <= i < len(cards):
                res[i] = (str(x.get("risk", "no")).strip().lower().startswith("y"), str(x.get("why", "")).strip())
        except (TypeError, ValueError, KeyError, AttributeError):
            pass
    return res


def revise_plan(usage, plan):
    """Re-tests only: Nova Pro merges the accepted changes into the plan as short bullets in the planner's voice."""
    base, _, changes = plan.partition("\n\nChanges made to the plan:\n")
    prompt = (
        f"Original plan:\n<<<\n{base}\n>>>\n\nChanges the planner decided to make:\n{changes}\n\n"
        "Rewrite the plan as 4 to 8 short bullet points in the planner's own voice (first person), with every change "
        "merged in where it belongs. Keep every fact from the original; add nothing new. Max 22 words per bullet. "
        "Mark a bullet changed=true if it contains one of the changes.\n"
        f"{KINDNESS}\n"
        'Return JSON {"bullets": [{"text": "", "changed": false}]}'
    )
    out = ask_retry(usage, PRO, "You are a practical, kind and honest planning advisor. Output one JSON object only.",
                    prompt, 700, lambda o: o["bullets"][0]["text"], temperature=0.2)
    return [{"text": str(b.get("text", "")).strip(), "changed": bool(b.get("changed"))} for b in out["bullets"] if b.get("text")]


def apply_card(t, c):
    t["fix"], t["backup"], t["early_sign"] = c["fix"], c["backup"], c["early_sign"]
    t["when"] = c.get("when", "")
    try:
        t["days_before"] = max(0, int(c.get("days_before", 0)))
    except (TypeError, ValueError):
        t["days_before"] = 0
    if "question" in c:
        t["question"] = c["question"]


def pick_quiet(usage, worries, labels, counts):
    """From the WANT angle, Nova Lite picks the worry most about what the affected people themselves want, feel
    or choose. Fallback: highest severity, ties to the smallest theme (the quietest), then lowest seq."""
    cands = [(w, lab) for w, lab in zip(worries, labels) if w["angle"] == WANT]
    if not cands:
        return None
    lines = "\n".join(f"{i}: {w['failure']}" for i, (w, _) in enumerate(cands))
    prompt = (
        f"Worries:\n{lines}\n\nWhich ONE worry is most about what the people affected by the plan themselves want, "
        "feel or would choose (their wishes, comfort, consent), rather than logistics, secrecy or equipment? "
        'Return JSON {"i": <number>}'
    )
    try:
        out = ask_retry(usage, LITE, "You pick one worry. Output one JSON object only.", prompt, 50,
                        lambda o: int(o["i"]), temperature=0.0)
        i = int(out["i"])
        if 0 <= i < len(cands):
            return cands[i][0]
    except (RuntimeError, ValueError, TypeError):
        pass
    w, _ = min(cands, key=lambda x: (-x[0]["severity"], counts.get(x[1], 0), x[0]["seq"]))
    return w


def find_quiet(usage, worries, labels, counts, parent):
    """Re-tests track the parent's quiet concern; new runs pick one and count who shares it (in code)."""
    pq = parent["result"].get("quiet") if parent else None
    if pq:
        flags = same_concern(usage, pq["worry"], worries)
        return {**pq, "count": sum(flags), "before": pq["count"],
                "matching_seqs": [w["seq"] for w, f in zip(worries, flags) if f]}
    if parent:
        return None
    qw = pick_quiet(usage, worries, labels, counts)
    if not qw:
        return None
    flags = same_concern(usage, qw["failure"], worries)
    matches = [qw] + [w for w, f in zip(worries, flags) if f and w["seq"] != qw["seq"]]  # it always counts itself
    return {"worry": qw["failure"], "seq": qw["seq"], "character": qw["character"], "moment": qw["moment"],
            "severity": qw["severity"], "count": len(matches), "matching_seqs": sorted(w["seq"] for w in matches),
            "name": qw["failure"], "definition": "", "all_examples": [w["failure"] for w in matches[:6]]}


def handler(event, _ctx):
    """Tally v2 (worrycore) when the run asks for it or TALLY_V2=1; any v2 failure falls back to v1 (marked)."""
    want = str(event.get("tally") or "").lower()
    if want == "v2" or (os.environ.get("TALLY_V2") == "1" and want != "v1"):
        try:
            return handler_v2(event)
        except Exception as e:  # noqa: BLE001 - never sink a run because the new path failed
            import traceback
            traceback.print_exc()
            out = handler_v1(event)
            out["result"]["tally"] = "v1-fallback"
            out["result"]["tally_error"] = f"{type(e).__name__}: {str(e)[:200]}"
            return out
    out = handler_v1(event)
    out["result"]["tally"] = "v1"
    return out


def handler_v2(event):
    import tally_v2
    run_id, plan = event["run_id"], event["plan"]
    parent_id = event.get("parent_run_id") or ""
    usage = Usage()
    worries = load_worries(run_id)
    if not worries:
        raise RuntimeError("no worries to tally")
    me = get_meta(run_id)
    grid = json.loads(me["grid"]["S"]) if "grid" in me else {}
    if parent_id:
        pm = get_meta(parent_id)
        presult = json.loads(pm["result"]["S"])
        result, labels = tally_v2.retest_run(sys.modules[__name__], usage, run_id, parent_id, plan, worries,
                                             load_worries(parent_id), presult, noise_run=bool(event.get("noise")),
                                             reports=[str(r)[:300] for r in (event.get("reports") or []) if isinstance(r, str)][:8],
                                             revision=event.get("revision") if isinstance(event.get("revision"), dict) else None)
        themes = json.loads(pm["themes"]["S"])
    else:
        result, labels = tally_v2.analyse_run(sys.modules[__name__], usage, run_id, plan, worries, grid)
        themes = [{"id": t["id"], "name": t["name"], "definition": t["definition"]} for t in result["themes"]]
    return {"result": result, "themes": themes, "labels": labels, "tokens": usage.t}


def handler_v1(event):
    run_id, plan = event["run_id"], event["plan"]
    parent_id = event.get("parent_run_id") or ""
    usage = Usage()
    all_worries = load_worries(run_id)
    if not all_worries:
        raise RuntimeError("no worries to tally")
    n = len(all_worries)
    facts = plan_facts(usage, plan)
    flags = off_plan(usage, plan, facts, all_worries)
    offplan_ws = [w for w, f in zip(all_worries, flags) if f]
    worries = [w for w, f in zip(all_worries, flags) if not f]

    parent = None
    if parent_id:
        pm = get_meta(parent_id)
        parent = {"themes": json.loads(pm["themes"]["S"]), "result": json.loads(pm["result"]["S"])}
        themes = [dict(t) for t in parent["themes"]]
    else:
        themes = propose_themes(usage, plan, worries, run_id)
    labels = classify(usage, plan, themes, worries)

    # Counts come from code, not from the model.
    by_theme = {t["id"]: [] for t in themes}
    by_theme["other"] = []
    for w, lab in zip(worries, labels):
        by_theme[lab].append(w)
    counts = {k: len(v) for k, v in by_theme.items()}
    for t in themes:
        ws = by_theme[t["id"]]
        t["count"] = len(ws)
        t["avg_severity"] = round(sum(w["severity"] for w in ws) / len(ws), 2) if ws else None

    ranked = sorted((t for t in themes if t["count"] > 0), key=lambda t: (-t["count"], -(t["avg_severity"] or 0)))
    top = []
    for t in ranked[:5]:
        ws = sorted(by_theme[t["id"]], key=lambda w: -w["severity"])
        top.append({**t, "examples": [w["failure"] for w in ws[:3]], "sample_seqs": [w["seq"] for w in ws[:3]],
                    "all_examples": [w["failure"] for w in ws[:6]]})

    # Quiet worry: shown even when few agree. Re-tests track the parent's quiet concern.
    quiet, quiet_error = None, None
    try:
        quiet = find_quiet(usage, worries, labels, counts, parent)
    except RuntimeError as e:  # the quiet card is best-effort; it never sinks the run
        quiet_error = str(e)[:300]

    jobs = [(t, False) for t in top] + ([(quiet, True)] if quiet and "fix" not in quiet else [])
    with ThreadPoolExecutor(max_workers=6) as pool:
        cards = list(pool.map(lambda j: write_fix(plan, j[0], n, quiet=j[1]), jobs))
    for (t, _), (c, u) in zip(jobs, cards):
        usage.merge(u)
        apply_card(t, c)

    # Fix checker: flag fixes that add a new risk or dependency, revise those once, then check again.
    if jobs:
        try:
            checks = check_fixes(usage, plan, [t for t, _ in jobs])
            redo = [(t, q, why) for (t, q), (risk, why) in zip(jobs, checks) if risk]
            for t, _ in jobs:
                t["introduces_risk"] = False
            if redo:
                with ThreadPoolExecutor(max_workers=6) as pool:
                    again = list(pool.map(lambda j: write_fix(plan, j[0], n, quiet=j[1], avoid=j[2] or "it adds a new risk"), redo))
                for (t, _, why), (c, u) in zip(redo, again):
                    usage.merge(u)
                    t["first_fix"] = t["fix"]
                    apply_card(t, c)
                    t["revised"] = True
                rechecks = check_fixes(usage, plan, [t for t, _, _ in redo])
                for (t, _, _), (risk, why) in zip(redo, rechecks):
                    t["introduces_risk"] = risk
                    if risk:
                        t["risk_note"] = why
        except RuntimeError as e:
            for t, _ in jobs:
                t["introduces_risk"] = None  # unchecked; the UI says nothing rather than guessing
            print("fix checker failed:", e)
    for t in top:
        t.pop("all_examples", None)
    if quiet:
        quiet.pop("all_examples", None)
        quiet.pop("definition", None)
        quiet.pop("name", None)

    # Severity-weighted worry: the sum of severity over every worrier (and over the top-5 huddles).
    top_ids = {t["id"] for t in top}
    total_sev = sum(w["severity"] for w in all_worries)
    top5_sev = sum(w["severity"] for w, lab in zip(worries, labels) if lab in top_ids)
    result = {
        "n_worries": n,
        "total_severity": total_sev,
        "top5_severity": top5_sev,
        "other": counts["other"],
        "offplan": len(offplan_ws),
        "offplan_examples": [w["failure"] for w in offplan_ws[:8]],
        "plan_facts": facts,
        "top5": top,
        "quiet": quiet,
        "quiet_error": quiet_error,
        "themes": [{k: t[k] for k in ("id", "name", "definition", "count", "avg_severity")} for t in ranked],
    }
    if parent:
        before = {t["id"]: t["count"] for t in parent["result"]["themes"]}
        pws = load_worries(parent_id)
        plabels = json.loads(pm["labels"]["S"]) if "labels" in pm else {}
        sev_before = {}
        for w in pws:
            lab = plabels.get(str(w["seq"]), "other")
            sev_before[lab] = sev_before.get(lab, 0) + w["severity"]
        sev_after = {}
        for w, lab in zip(worries, labels):
            sev_after[lab] = sev_after.get(lab, 0) + w["severity"]
        ptop = [t["id"] for t in parent["result"]["top5"]]
        result["severity_before"] = sum(w["severity"] for w in pws)
        result["severity_after"] = total_sev
        result["top5_count_before"] = sum(before.get(i, 0) for i in ptop)
        result["top5_count_after"] = sum(counts.get(i, 0) for i in ptop)
        result["top5_severity_before"] = sum(sev_before.get(i, 0) for i in ptop)
        result["top5_severity_after"] = sum(sev_after.get(i, 0) for i in ptop)
        result["parent_run_id"] = parent_id
        try:
            result["revised_plan"] = revise_plan(usage, plan)
        except RuntimeError as e:
            print("revise_plan failed:", e)
        result["parent_n"] = parent["result"]["n_worries"]
        result["comparison"] = sorted(
            [{"id": t["id"], "name": t["name"], "before": before.get(t["id"], 0), "after": t["count"],
              "sev_before": sev_before.get(t["id"], 0), "sev_after": sev_after.get(t["id"], 0)} for t in themes]
            + [{"id": "other", "name": "other", "before": parent["result"].get("other", 0), "after": counts["other"]},
               {"id": "offplan", "name": "set aside: not in the plan", "before": parent["result"].get("offplan", 0),
                "after": len(offplan_ws)}],
            key=lambda c: -c["before"])
    return {
        "result": result,
        "themes": [{k: t[k] for k in ("id", "name", "definition")} for t in themes],
        "labels": {**{str(w["seq"]): lab for w, lab in zip(worries, labels)},
                   **{str(w["seq"]): "offplan" for w in offplan_ws}},
        "tokens": usage.t,
    }
