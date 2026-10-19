import csv

import pytest

import config
from generator import generate_pattern
from pipeline import build_geometry, design_params
from solid.depth_model import DepthModel


def test_fit_matches_measurements():
    model = DepthModel(config.DEPTH_MODEL)
    assert len(model.y) >= 4
    assert model.loo_rmse < 2.0
    # 30-ring Run 11 chute: measured 10.62 in
    report = generate_pattern({**config.FIXED_PARAMS, **config.PRESETS["baseline"]}, write_files=False)
    depth, info = model.predict(report)
    assert abs(depth - 10.62) < 1.0 and not info["depth_extrapolated"]


def test_more_rings_deeper_and_extrapolation_flagged():
    model = DepthModel(config.DEPTH_MODEL)
    shallow, _ = model.predict(generate_pattern({"NUM_RINGS": 18}, write_files=False))
    deep, _ = model.predict(generate_pattern({"NUM_RINGS": 30}, write_files=False))
    assert deep > shallow
    _, info = model.predict(generate_pattern({"NUM_RINGS": 36, "CUTS_PER_RING": 4}, write_files=False))
    assert info["depth_extrapolated"] and "cuts_per_ring" in info["depth_notes"]


def test_new_csv_rows_are_used(tmp_path):
    src = config.DEPTH_MODEL["DATA_FILE"]
    rows = list(csv.DictReader(open(src, newline="")))
    extra = dict(rows[0], name="cpr4_test", CUTS_PER_RING="4", depth_in="12.0")
    path = tmp_path / "depths.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows + [extra])
    model = DepthModel({**config.DEPTH_MODEL, "DATA_FILE": path, "FEATURES": ["n_bands", "cuts_per_ring"]})
    assert len(model.y) == 5 and 4.0 in model.cuts_seen


def test_too_few_rows_rejected(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("name,NUM_RINGS,depth_in\na,20,6\n")
    with pytest.raises(ValueError):
        DepthModel({**config.DEPTH_MODEL, "DATA_FILE": path})


def test_pipeline_uses_predicted_depth(tmp_path):
    _, info, solid_params, _ = build_geometry(design_params({"NUM_RINGS": 18}), tmp_path, "d")
    assert info["depth_model"].startswith("depth_in =")
    assert abs(info["canopy_depth_in"] - solid_params["VERTICAL_DROP"]) < 0.1
    _, info, _, _ = build_geometry(design_params(config.PRESETS["baseline"]), tmp_path, "b")
    assert info["depth_model"] == "fixed" and abs(info["canopy_depth_in"] - 10.62) < 0.1
