import numpy as np

from conftest import LEGACY_3D_RING_DATA
from generator import generate_pattern
from solid import build_flow_domain, build_solid
from solid.flow_domain import FACE_NAMES


def test_legacy_tuples_and_generator_rings_give_same_solid():
    a, _ = build_solid(LEGACY_3D_RING_DATA, {"EXTRUDE_METHOD": "legacy"})
    report = generate_pattern({"NUM_RINGS": 30, "RING_SPACING_EXPONENT": 1.15, "RING_OVERLAP_DEGREES": 16,
                               "INNER_RING_OVERLAP_DEGREES": 14, "INNER_RING_COUNT": 6,
                               "INNER_GAP_DEGREES": 6}, write_files=False)
    b, _ = build_solid(report, {"EXTRUDE_METHOD": "legacy"})
    assert len(a.faces) == len(b.faces)
    assert np.allclose(a.vertices, b.vertices, atol=1e-5)


def test_legacy_extrusion_is_watertight():
    mesh, info = build_solid(LEGACY_3D_RING_DATA, {"EXTRUDE_METHOD": "legacy"})
    assert info["is_watertight"]


def test_union_is_one_watertight_body():
    mesh, info = build_solid(LEGACY_3D_RING_DATA)
    assert info["is_watertight"] and info["body_count"] == 1
    assert mesh.volume > 0
    assert abs(info["canopy_depth_in"] - 10.635) < 0.05


def test_other_cut_counts_build():
    for cuts in (4, 6, 12):
        report = generate_pattern({"NUM_RINGS": 18, "CUTS_PER_RING": cuts, "RING_OVERLAP_FRACTION": 0.7,
                                   "INNER_OVERLAP_RATIO": 0.2}, write_files=False)
        _, info = build_solid(report, {"CUTS_PER_RING": cuts})
        assert info["is_watertight"] and info["body_count"] == 1


def test_flow_domain_labels_and_box():
    mesh, _ = build_solid(LEGACY_3D_RING_DATA)
    fluid, labels, (lo, hi) = build_flow_domain(mesh, 10.0)
    assert fluid.is_watertight
    assert set(labels) == set(FACE_NAMES)
    assert np.allclose(lo, [-25.83, -25.83, -70.0]) and np.allclose(hi, [25.83, 25.83, 31.13])
    # fluid volume = box - canopy
    assert abs(fluid.volume - (51.66 * 51.66 * 101.13 - mesh.volume)) < 1e-6 * fluid.volume


def test_ribbons_crossing_center_are_infeasible():
    import pytest

    from solid import InfeasibleGeometry

    report = generate_pattern({"NUM_RINGS": 16, "CUTS_PER_RING": 12, "RING_SPACING_EXPONENT": 0.6,
                               "INNER_RADIUS": 0.5, "RING_OVERLAP_FRACTION": 1.0,
                               "INNER_OVERLAP_RATIO": 0.2}, write_files=False)
    with pytest.raises(InfeasibleGeometry):
        build_solid(report, {"CUTS_PER_RING": 12})
