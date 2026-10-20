"""Optimization loop against a fake SimScale client: resume + checkpointing."""

import pytest

import config
from optimizer.loop import run_optimization
from optimizer.state import Ledger
from pipeline import Context
from sheets import RunLogger
from velocity import VelocityEstimator

SMALL_SPACE = [
    {"name": "NUM_RINGS", "type": "int", "low": 12, "high": 16},
    {"name": "RING_OVERLAP_FRACTION", "type": "real", "low": 0.2, "high": 1.0},
]


class FakeSimScale:
    def __init__(self, crash_on_call=None):
        self.calls = 0
        self.crash_on_call = crash_on_call

    def run_design(self, project_id, design_id, domain_file, uz, area, length, work_dir, state, save_state,
                   manual=None):
        self.calls += 1
        if "run_id" not in state:
            state.update(geometry_id="g", simulation_id="s", run_id=f"r-{design_id}",
                         mesh_core_hours=0.5, run_core_hours=1.5)
            save_state(state)
        if self.calls == self.crash_on_call:
            raise KeyboardInterrupt  # simulated crash mid-run
        return {"cd": 1.0 + 0.01 * self.calls, "cl": 0.0, "cm": 0.0, "n_iterations": 500}


def _ctx(sim):
    return Context(ledger=Ledger(config.LEDGER_PATH),
                   logger=RunLogger(config.LOCAL_RESULTS_CSV, use_sheets=False),
                   estimator=VelocityEstimator(config.VELOCITY, 10.0),
                   simscale=sim, project_id="p", search_space=SMALL_SPACE, log=lambda *a: None)


@pytest.fixture(autouse=True)
def fast_geometry(monkeypatch):
    # These tests are about resume/budget logic; skip the slower STEP export.
    monkeypatch.setitem(config.SIMSCALE, "GEOMETRY_FORMAT", "STL")


def test_crash_and_resume(isolated_paths, monkeypatch):
    monkeypatch.setitem(config.FIXED_PARAMS, "INNER_OVERLAP_RATIO", 0.3)

    sim = FakeSimScale(crash_on_call=3)
    try:
        run_optimization(_ctx(sim), n_calls=5, n_initial_points=3)
    except KeyboardInterrupt:
        pass
    ledger = Ledger(config.LEDGER_PATH)
    assert len(ledger.records) == 2
    assert ledger.pending["simscale"]["run_id"] == "r-design_0003"

    sim2 = FakeSimScale()
    best = run_optimization(_ctx(sim2), n_calls=5, n_initial_points=3)
    ledger = Ledger(config.LEDGER_PATH)
    assert ledger.pending is None
    assert len(ledger.records) == 5
    assert [r["design_id"] for r in ledger.records] == [f"design_{i:04d}" for i in range(1, 6)]
    assert all(r["status"] == "ok" for r in ledger.records)
    assert best["cd"] == max(r["cd"] for r in ledger.records)
    # later records carry a CFD-informed velocity estimate
    assert ledger.records[-1]["velocity"]["nearest_run"] is not None
    assert (config.STATE_DIR / "skopt_result.pkl").exists()
    assert config.LOCAL_RESULTS_CSV.read_text().count("\n") == 6  # header + 5 rows

    # asking for the same n_calls again does nothing
    run_optimization(_ctx(FakeSimScale()), n_calls=5, n_initial_points=3)
    assert len(Ledger(config.LEDGER_PATH).records) == 5


def test_core_hour_budget_stops_the_loop(isolated_paths, monkeypatch):
    monkeypatch.setitem(config.FIXED_PARAMS, "INNER_OVERLAP_RATIO", 0.3)
    monkeypatch.setitem(config.OPTIMIZER, "CORE_HOUR_BUDGET", 5.0)  # 2 CPUh per fake design

    run_optimization(_ctx(FakeSimScale()), n_calls=10, n_initial_points=3)
    ledger = Ledger(config.LEDGER_PATH)
    assert len(ledger.records) == 3  # stops once 6 >= 5 CPUh
    assert ledger.core_hours_used() == 6.0
    assert "core_hours_used" in config.LOCAL_RESULTS_CSV.read_text().splitlines()[0]

    # re-running with the same budget starts nothing new
    run_optimization(_ctx(FakeSimScale()), n_calls=10, n_initial_points=3)
    assert len(Ledger(config.LEDGER_PATH).records) == 3
