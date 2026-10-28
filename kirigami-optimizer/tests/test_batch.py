from optimizer.batch import propose_points
from optimizer.state import Ledger
import config


def test_propose_points_are_distinct_and_inside_the_space(tmp_path):
    space = config.SEARCH_SPACE
    pts = propose_points(Ledger(tmp_path / "ledger.json"), space, 5)
    assert len(pts) >= 5 and len({tuple(p) for p in pts}) == len(pts)
    for p in pts:
        for d, v in zip(space, p):
            assert d["low"] <= v <= d["high"]
            assert isinstance(v, int) == (d["type"] == "int")


def test_default_canopy_is_the_welded_user_style_one():
    assert config.FIXED_PARAMS["EXTRUDE_METHOD"] == "legacy" and config.FIXED_PARAMS["WELD_STRIPS"] == 1
    assert config.FIXED_PARAMS["BRIDGE_EXTRA"] == 0.0 and config.FIXED_PARAMS["JOINT_OVERLAP"] == 0.0


def test_sliver_screen_passes_the_baseline_and_rejects_a_known_failure():
    import pytest

    pytest.importorskip("manifold3d")
    from generator import generate_pattern
    from pipeline import build_geometry, design_params
    from solid import screen_canopy

    base = design_params(config.PRESETS["baseline"])
    _, _, _, paths = build_geometry(base, __import__("pathlib").Path(__import__("tempfile").mkdtemp()), "t")
    import trimesh

    ok, stats = screen_canopy(trimesh.load(paths["canopy_stl"], force="mesh"), config.OPTIMIZER["MIN_PAIR_OVERLAP_IN3"])
    assert ok and stats["slivers"] == 0
    bad = design_params({"NUM_RINGS": 20, "CUTS_PER_RING": 8, "RING_SPACING_EXPONENT": 0.705827523616112,
                         "RING_OVERLAP_FRACTION": 0.6811523728477435, "INNER_RADIUS": 1.1136065334366454})
    _, _, _, paths = build_geometry(bad, __import__("pathlib").Path(__import__("tempfile").mkdtemp()), "t")
    ok, stats = screen_canopy(trimesh.load(paths["canopy_stl"], force="mesh"), config.OPTIMIZER["MIN_PAIR_OVERLAP_IN3"])
    assert not ok and stats["slivers"] > 0     # batch design 01: SimScale meshing failed with a self-intersection


def test_implausible_cd_is_not_used_for_velocity_or_training(tmp_path):
    from optimizer.state import Ledger

    ledger = Ledger(tmp_path / "ledger.json")
    ok = {"design_id": "a", "status": "ok", "x": [1], "y": -0.7, "x_norm": [0.1], "cd": 0.7}
    bad = {"design_id": "b", "status": "failed", "x": [2], "y": 0.0, "x_norm": [0.2], "cd": None,
           "error": "SimScaleError: implausible Cd -1843276.7: the solver diverged"}
    insane = {"design_id": "c", "status": "ok", "x": [3], "y": 1.8e6, "x_norm": [0.3], "cd": -1.8e6}
    ledger.records.extend([ok, bad, insane])
    assert ledger.xy()[0] == [[1]]                             # the failure and the implausible Cd are left out of the GP data
    assert [h[2] for h in ledger.cfd_history()] == ["a"]      # a negative Cd can no longer reach sqrt()


def test_convergence_measures_separate_a_settled_run_from_a_drifting_one():
    import numpy as np

    from simscale.results import parse_force_coefficients

    def csv_text(cd):
        return "Time (s),FORCE_COEFFICIENT_CD\n" + "\n".join(f"{i + 1},{v}" for i, v in enumerate(cd))

    t = np.arange(500)
    settled = 0.733 - 0.05 * np.exp(-t / 40.0)                   # baseline-like: flat after about 200 iterations
    drifting = 0.7 + 0.5 * (t / 500.0) ** 3                      # design_0019-like: still climbing at the end
    a = parse_force_coefficients(csv_text(settled), 0.1)
    b = parse_force_coefficients(csv_text(drifting), 0.1)
    assert a["cd_noise_rel"] < 0.01 and abs(a["cd_drift_rel"]) < 0.01
    assert abs(b["cd_drift_rel"]) > 0.01


def test_runaway_guard_flags_exponential_growth_but_not_a_settling_run():
    import numpy as np

    from simscale.results import runaway_reason

    t = np.arange(1, 501)
    settled = 0.733 - 0.05 * np.exp(-t / 40.0)
    runaway = 0.7 * np.exp(np.maximum(t - 200, 0) / 250.0)         # design_0019-like: starts to climb after iteration 200
    assert all(runaway_reason(settled[:n]) is None for n in range(50, 501, 10))
    assert any(runaway_reason(runaway[:n]) for n in range(50, 501, 10))
    assert runaway_reason([0.7] * 100 + [-1.6]) is not None           # sign flip, as in design_0007


def test_results_outside_the_narrowed_space_are_not_given_to_skopt(tmp_path):
    from optimizer.space import restrict

    space = config.SEARCH_SPACE
    assert [d for d in space if d["name"] == "NUM_RINGS"][0]["low"] == 27
    inside_x = [31, 6, 1.0, 0.6, 1.0]
    outside_x = [26, 8, 1.2, 0.55, 0.7]            # design_0011: converged, but 26 rings is below the band
    x0, y0 = restrict(space, [inside_x, outside_x], [-0.74, -0.75])
    assert x0 == [inside_x] and y0 == [-0.74]


def test_settings_description_names_every_non_default_setting():
    from simscale.spec import describe_settings

    base = dict(config.SIMSCALE)
    assert describe_settings(base) == "fineness 4.5; 500 it; default numerics"
    odd = dict(base, MESH_FINENESS=6, END_TIME_S=1400, POTENTIAL_FOAM_INIT=True, RELAX_PRESSURE=0.2, RELAX_VELOCITY=0.5)
    text = describe_settings(odd)
    assert "fineness 6" in text and "1400 it" in text and "potential-flow init" in text and "relax p 0.2 U 0.5" in text
