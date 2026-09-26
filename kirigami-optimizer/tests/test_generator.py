import json
import subprocess
import sys

import config
from conftest import LEGACY_3D_RING_DATA, ROOT
from generator import generate_pattern


def test_matches_original_script(tmp_path):
    """Default params reproduce the original script's report JSON exactly."""
    subprocess.run([sys.executable, str(ROOT / "legacy" / "kirigami_generator.py")],
                   cwd=tmp_path, check=True, capture_output=True)
    legacy = json.loads(next(tmp_path.glob("*.report.json")).read_text())
    new = generate_pattern(output_dir=tmp_path / "new", name="x")
    assert new["rings"] == legacy["rings"]
    assert new["parameters"] == legacy["parameters"]


def test_baseline_preset_reproduces_3d_creator_ring_table():
    report = generate_pattern({**config.FIXED_PARAMS, **config.PRESETS["baseline"]}, write_files=False)
    assert len(report["rings"]) == len(LEGACY_3D_RING_DATA)
    for ring, (n, r, cut, gap) in zip(report["rings"], LEGACY_3D_RING_DATA):
        assert ring["ring_number"] == n
        assert abs(ring["radius_inches"] - r) < 1e-6
        assert abs(ring["actual_cut_angle_degrees"] - cut) < 1e-9
        assert abs(ring["actual_gap_degrees"] - gap) < 1e-9


def test_overlap_fraction_never_clamps():
    for cuts in (4, 6, 8, 10, 12):
        report = generate_pattern({"CUTS_PER_RING": cuts, "RING_OVERLAP_FRACTION": 1.0,
                                   "INNER_OVERLAP_RATIO": 0.2, "INNER_GAP_DEGREES": 6.0}, write_files=False)
        assert report["summary"]["clamped_ring_count"] == 0
        assert abs(report["rings"][-1]["actual_gap_degrees"] - 6.0) < 1e-9
