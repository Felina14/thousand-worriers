"""Worst-case duration of one worry run, computed from statemachine/worry-run.asl.yaml, must stay under 90% of
the execution TimeoutSeconds, so no run is cut off by the timeout while it is still making progress.

Every number is read from the definition. Two inputs are not bounded by the definition, and the test names them:
  SWARM_S        the Distributed Map has no TimeoutSeconds; the swarm is budgeted at its lease (lock#swarm lease_s).
                 Measured swarms take ~70 s (docs/wa-review/after-f05-*.json).
  STATE_CALL_S   one DynamoDB / Step Functions / S3 / CloudWatch call, budgeted at 5 s.
A Lambda.ServiceException-type failure is returned at invoke time, so those retries add their waits, not 600 s.

Run: python3 -m pytest -q tests/test_worry_run_budget.py
"""
import pathlib
import re

import yaml

ASL = pathlib.Path(__file__).resolve().parent.parent / "statemachine" / "worry-run.asl.yaml"
STATE_CALL_S = 5
MARGIN = 0.9

# States that do not run on the success path of one run, or are counted in a named phase below.
PHASES = {"Grid", "Swarm", "Tally", "AcquireLock", "ReadLock", "CheckHolder", "TakeOver", "WaitForLock"}


def load():
    text = ASL.read_text()
    for k in ("TableName", "BucketName", "LiteModel", "MicroModel", "TallyFunctionArn", "StackName"):
        text = text.replace("${" + k + "}", "x")
    return yaml.safe_load(text)


def retry_wait(retrier):
    """Longest total wait a retrier adds (FULL jitter waits at most the un-jittered delay)."""
    cap = retrier.get("MaxDelaySeconds", float("inf"))
    iv, br = retrier.get("IntervalSeconds", 1), retrier.get("BackoffRate", 2.0)
    return sum(min(cap, iv * br ** i) for i in range(retrier.get("MaxAttempts", 3)))


def retriers(state, names):
    return [r for r in state.get("Retry", []) if set(r["ErrorEquals"]) & set(names)]


def budget():
    d = load()
    S = d["States"]
    parts = {}

    # Grid: 3 attempts (GridRetry: grid_tries < 3); each one = 1 call + its Bedrock retries, every call <= TimeoutSeconds.
    tries = int(re.search(r"\$grid_tries < (\d+)", S["GridRetry"]["Choices"][0]["Condition"]).group(1))
    g = S["Grid"]
    (bed,) = retriers(g, ["Bedrock.ThrottlingException"])
    parts["grid"] = tries * ((1 + bed["MaxAttempts"]) * g["TimeoutSeconds"] + retry_wait(bed))

    # One pass of the lock loop after the queue limit is reached: Wait + AcquireLock (with retries) + ReadLock + CheckHolder.
    (acq,) = retriers(S["AcquireLock"], ["DynamoDB.ThrottlingException"])
    loop = S["WaitForLock"]["Seconds"] + retry_wait(acq) + 3 * STATE_CALL_S + (1 + acq["MaxAttempts"]) * STATE_CALL_S
    swarm_wait = int(re.search(r"(\d+)\]\) : (\d+)", S["Init"]["Assign"]["lock_max_wait_s"]).group(1))
    parts["swarm_queue"] = swarm_wait + loop
    parts["swarm"] = S["SaveGrid"]["Assign"]["lease_s"]  # SWARM_S: budgeted at the swarm lease

    tq = S["RequestTallyLock"]["Assign"]
    parts["tally_queue"] = tq["max_wait_s"] + loop

    t = S["Tally"]
    (thr,) = retriers(t, ["ThrottlingException"])
    (lam,) = retriers(t, ["Lambda.TooManyRequestsException"])
    parts["tally"] = (1 + thr["MaxAttempts"]) * t["TimeoutSeconds"] + retry_wait(thr) + retry_wait(lam) + lam["MaxAttempts"] * STATE_CALL_S

    # Every other Task state outside the Map: one call plus its retries, plus every retry's waits.
    other = 0
    for name, st in S.items():
        if st["Type"] != "Task" or name in PHASES:
            continue
        rs = st.get("Retry", [])
        other += STATE_CALL_S * (1 + sum(r.get("MaxAttempts", 3) for r in rs)) + sum(retry_wait(r) for r in rs)
    parts["other_states"] = other
    return parts, d["TimeoutSeconds"], tq


def test_worst_case_fits_under_the_execution_timeout_with_margin():
    parts, timeout, _ = budget()
    total = sum(parts.values())
    print("\n".join(f"{k:>14}: {v:8.0f} s" for k, v in parts.items()), f"\n{'total':>14}: {total:8.0f} s of {timeout} s")
    assert total <= MARGIN * timeout, (total, timeout, parts)


def test_tally_lease_covers_every_tally_attempt():
    """A RUNNING tally must not lose its lease to a waiter while it is still allowed to retry."""
    d = load()
    t = d["States"]["Tally"]
    (thr,) = retriers(t, ["ThrottlingException"])
    (lam,) = retriers(t, ["Lambda.TooManyRequestsException"])
    worst = (1 + thr["MaxAttempts"]) * t["TimeoutSeconds"] + retry_wait(thr) + retry_wait(lam)
    assert d["States"]["RequestTallyLock"]["Assign"]["lease_s"] > worst


def test_lambda_timeout_matches_tally_task_timeout():
    tmpl = (ASL.parent.parent / "template.yaml").read_text()
    fn = tmpl[tmpl.index("  TallyFunction:"):]
    lam_timeout = int(re.search(r"\n      Timeout: (\d+)", fn).group(1))
    assert load()["States"]["Tally"]["TimeoutSeconds"] >= lam_timeout
