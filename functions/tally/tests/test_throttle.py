"""F04: throttle backoff, time budget and the one-at-a-time Nova Pro gate (Bedrock stubbed, no model calls).
Run: cd functions/tally && python3 -m pytest -q tests/test_throttle.py"""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from botocore.exceptions import ClientError

import importlib.util  # noqa: E402

os.environ.update(TABLE_NAME="t", LITE_MODEL="lite", PRO_MODEL="pro", BUCKET_NAME="b", AWS_DEFAULT_REGION="us-east-1")
# A private copy of app.py: test_tally_v2_adapter replaces app.ask_retry for the whole session.
_spec = importlib.util.spec_from_file_location("app_throttle_copy", os.path.join(os.path.dirname(__file__), "..", "app.py"))
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)

OK = {"output": {"message": {"content": [{"text": json.dumps({"ok": 1})}]}}, "usage": {"inputTokens": 1, "outputTokens": 1}}


def throttle():
    return ClientError({"Error": {"Code": "ThrottlingException", "Message": "Too many requests"}}, "Converse")


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    sleeps = []
    monkeypatch.setattr(app.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(app, "PRO_RPM", 1e9)  # no pacing unless a test sets it
    app.DEADLINE[0] = float("inf")
    app._pro_last[0] = 0.0
    yield sleeps
    app.DEADLINE[0] = float("inf")


def test_throttle_retried_with_full_jitter_then_succeeds(monkeypatch, fast):
    calls = {"n": 0}

    def conv(**_):
        calls["n"] += 1
        if calls["n"] <= 4:
            raise throttle()
        return OK
    monkeypatch.setattr(app.br, "converse", conv)
    out = app.ask_retry(app.Usage(), "lite", "s", "p", 10, lambda o: None)
    assert out == {"ok": 1} and calls["n"] == 5
    assert len(fast) == 4
    for i, d in enumerate(fast):  # full jitter: uniform in [0, min(cap, base * 2^i)]
        assert 0 <= d <= min(app.BACKOFF_CAP_S, app.BACKOFF_BASE_S * 2 ** i)


def test_throttle_gives_up_at_the_deadline_with_the_original_error(monkeypatch):
    monkeypatch.setattr(app.br, "converse", lambda **_: (_ for _ in ()).throw(throttle()))
    monkeypatch.setattr(app.random, "uniform", lambda a, b: b)
    app.DEADLINE[0] = time.monotonic() + 5  # room for the 2 s and 4 s backoffs? 2 fits, 2+4 would not start
    with pytest.raises(ClientError) as e:
        app.ask_retry(app.Usage(), "lite", "s", "p", 10, lambda o: None)
    assert e.value.response["Error"]["Code"] == "ThrottlingException"


def test_non_throttle_client_error_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def conv(**_):
        calls["n"] += 1
        raise ClientError({"Error": {"Code": "ValidationException", "Message": "bad"}}, "Converse")
    monkeypatch.setattr(app.br, "converse", conv)
    with pytest.raises(ClientError):
        app.ask_retry(app.Usage(), "lite", "s", "p", 10, lambda o: None)
    assert calls["n"] == 1


def test_unusable_output_still_limited_to_tries(monkeypatch):
    monkeypatch.setattr(app.br, "converse", lambda **_: OK)
    with pytest.raises(RuntimeError):
        app.ask_retry(app.Usage(), "lite", "s", "p", 10, lambda o: (_ for _ in ()).throw(KeyError("x")), tries=3)


def _peak(monkeypatch, model):
    live, peak, lock = [0], [0], threading.Lock()

    def conv(**_):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        real_sleep(0.02)
        with lock:
            live[0] -= 1
        return OK
    monkeypatch.setattr(app.br, "converse", conv)
    with ThreadPoolExecutor(max_workers=7) as pool:
        list(pool.map(lambda _: app.ask(app.Usage(), model, "s", "p", 10, 0.2), range(14)))
    return peak[0]


real_sleep = time.sleep


def test_pro_calls_run_one_at_a_time_and_lite_stays_parallel(monkeypatch):
    assert _peak(monkeypatch, "pro") == 1
    assert _peak(monkeypatch, "lite") > 1


def test_pro_calls_are_spaced_by_pro_rpm(monkeypatch, fast):
    monkeypatch.setattr(app, "PRO_RPM", 12.0)
    monkeypatch.setattr(app.br, "converse", lambda **_: OK)
    for _ in range(3):
        app.ask(app.Usage(), "pro", "s", "p", 10, 0.2)
    assert len(fast) == 2 and all(4.9 < s <= 5.0 for s in fast)  # 60 / 12 = 5 s between call starts
