"""The SimScale polling watchdog must cancel runaway jobs on SimScale."""

from types import SimpleNamespace

import pytest

from simscale.runner import SimScaleClient, SimScaleError


def _client():
    c = SimScaleClient.__new__(SimScaleClient)
    c.cfg = {"POLL_SECONDS": 0}
    c.log = lambda *a: None
    return c


def _job(statuses, progresses, hours):
    """Getter that walks through job states; stays on the last one."""
    state = {"i": 0, "cancelled": False}

    def get():
        i = min(state["i"], len(statuses) - 1)
        state["i"] += 1
        status = "CANCELED" if state["cancelled"] else statuses[i]
        return SimpleNamespace(status=status, progress=progresses[min(i, len(progresses) - 1)],
                               compute_resource=SimpleNamespace(value=hours[min(i, len(hours) - 1)]))

    def cancel():
        state["cancelled"] = True

    return get, cancel, state


def test_cpu_cap_cancels_on_simscale():
    get, cancel, state = _job(["RUNNING"] * 10, [0.1] * 10, [0.5, 1.0, 2.5, 3.0])
    with pytest.raises(SimScaleError, match="over the 2 CPU h cap"):
        _client()._poll("meshing", get, 3600, cancel=cancel, cpu_cap=2.0)
    assert state["cancelled"]


def test_stall_cancels_on_simscale(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr("simscale.runner.time.time", lambda: clock.__setitem__("t", clock["t"] + 100) or clock["t"])
    monkeypatch.setattr("simscale.runner.time.sleep", lambda s: None)
    get, cancel, state = _job(["RUNNING"] * 100, [0.1] * 100, [0.1] * 100)
    with pytest.raises(SimScaleError, match="stuck at 10%"):
        _client()._poll("meshing", get, 10 ** 9, cancel=cancel, stall_seconds=900)
    assert state["cancelled"]


def test_healthy_job_is_not_cancelled():
    get, cancel, state = _job(["RUNNING", "RUNNING", "FINISHED"], [0.1, 0.5, 1.0], [0.1, 0.2, 0.3])
    obj = _client()._poll("meshing", get, 3600, cancel=cancel, cpu_cap=2.0, stall_seconds=900)
    assert obj.status == "FINISHED" and not state["cancelled"]


def test_queue_time_does_not_count(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr("simscale.runner.time.time", lambda: clock.__setitem__("t", clock["t"] + 100) or clock["t"])
    monkeypatch.setattr("simscale.runner.time.sleep", lambda s: None)
    # 20 polls queued (~2000 s, far over the 300 s limit), then finishes quickly
    get, cancel, state = _job(["QUEUED"] * 20 + ["RUNNING", "FINISHED"], [0.0] * 20 + [0.5, 1.0], [0.0] * 22)
    obj = _client()._poll("meshing", get, 300, cancel=cancel, stall_seconds=900)
    assert obj.status == "FINISHED" and not state["cancelled"]
