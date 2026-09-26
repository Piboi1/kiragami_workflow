import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

# The baseline design; its rings equal the table hard-coded in the original
# kiragami3dcreator.py (Kiragami_CFD Run 11 geometry).
LEGACY_3D_RING_DATA = [
    (1, 0.750000, 36.5, 8.5), (2, 0.833234, 36.9, 8.1), (3, 0.934708, 37.3, 7.7),
    (4, 1.044437, 37.7, 7.3), (5, 1.159894, 38.1, 6.9), (6, 1.279807, 38.5, 6.5),
    (7, 1.403396, 38.5, 6.5), (8, 1.530127, 38.5, 6.5), (9, 1.659611, 38.5, 6.5),
    (10, 1.791553, 38.5, 6.5), (11, 1.925716, 38.5, 6.5), (12, 2.061910, 38.5, 6.5),
    (13, 2.199976, 38.5, 6.5), (14, 2.339780, 38.5, 6.5), (15, 2.481209, 38.5, 6.5),
    (16, 2.624162, 38.5, 6.5), (17, 2.768554, 38.5, 6.5), (18, 2.914305, 38.5, 6.5),
    (19, 3.061350, 38.5, 6.5), (20, 3.209625, 38.5, 6.5), (21, 3.359076, 38.5, 6.5),
    (22, 3.509653, 38.5, 6.5), (23, 3.661309, 38.5, 6.5), (24, 3.814004, 38.5, 6.5),
    (25, 3.967698, 38.5, 6.5), (26, 4.122355, 38.5, 6.5), (27, 4.277944, 38.5, 6.5),
    (28, 4.434433, 38.5, 6.5), (29, 4.591794, 38.5, 6.5), (30, 4.750000, 38.5, 6.5),
]


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Points every run/state path at a temp dir."""

    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "state" / "ledger.json")
    monkeypatch.setattr(config, "LOCAL_RESULTS_CSV", tmp_path / "runs" / "results.csv")
    return tmp_path
