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
