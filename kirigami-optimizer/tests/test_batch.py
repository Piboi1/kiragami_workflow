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
