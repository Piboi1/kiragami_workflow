import math

import config
from velocity import VelocityEstimator, cd_from_velocity, terminal_velocity, terminal_velocity_from_drop
from velocity.estimator import drop_time


def test_terminal_velocity_round_trip():
    v = terminal_velocity(0.05, 1.1, 0.048, 1.196)
    assert math.isclose(cd_from_velocity(0.05, v, 0.048, 1.196), 1.1)


def test_drop_correction_inverts_fall_time():
    vt = terminal_velocity_from_drop(4.937, 1.322)
    assert math.isclose(drop_time(4.937, vt), 1.322, rel_tol=1e-9)
    assert vt > 4.937 / 1.322  # average speed understates terminal speed


def test_seed_velocity_reproduces_drop_test():
    est = VelocityEstimator(config.VELOCITY, 10.0)
    out = est.estimate([], [], config.VELOCITY["DROP_TEST_SEED"]["reference_area_in2"])
    assert math.isclose(out["predicted_speed_mps"], est.seed_info["seed_velocity_mps"], rel_tol=1e-9)


def test_anchored_refinement_uses_relative_cfd():
    est = VelocityEstimator({**config.VELOCITY, "CD_REFINEMENT": "anchored"}, 10.0)
    history = [([0.0, 0.0], 1.0, "a"), ([1.0, 1.0], 2.0, "b")]
    near_b, info = est.estimate_cd([1.0, 1.0], history)
    near_a, _ = est.estimate_cd([0.0, 0.0], history)
    assert near_b > est.seed_cd > near_a
    assert info["nearest_run"] == "b"


def test_direct_refinement_moves_toward_cfd():
    est = VelocityEstimator({**config.VELOCITY, "CD_REFINEMENT": "direct"}, 10.0)
    cd, _ = est.estimate_cd([0.5], [([0.5], 1.2, "a")])
    assert est.seed_cd < cd < 1.2
