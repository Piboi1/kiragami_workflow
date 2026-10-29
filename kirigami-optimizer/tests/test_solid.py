import numpy as np

from conftest import LEGACY_3D_RING_DATA
from generator import generate_pattern
from solid import build_flow_domain, build_solid
from solid.flow_domain import FACE_NAMES, REQUIRED_FACES


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
    assert set(labels) == set(REQUIRED_FACES)  # no anchor -> no sting
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


def test_sting_makes_one_connected_boundary():
    mesh, info = build_solid(LEGACY_3D_RING_DATA)
    x, y, z = info["sting_anchor_in"]
    assert abs(np.hypot(x, y) - 4.75) < 1e-9 and abs(z + 10.62) < 1e-9

    plain, plain_labels, _ = build_flow_domain(mesh, 10.0)
    assert len(plain.split(only_watertight=False)) == 2  # box shell + floating canopy shell

    fluid, labels, _ = build_flow_domain(mesh, 10.0, sting_anchor=info["sting_anchor_in"])
    assert fluid.is_watertight
    assert set(labels) == set(FACE_NAMES)
    assert len(fluid.split(only_watertight=False)) == 1
    # sting faces run from the rim down to the outlet only
    sting_z = fluid.triangles[labels == "sting"][:, :, 2]
    assert sting_z.max() < -10.62 and sting_z.min() >= -70.0 - 1e-6
    # the canopy surface the forces act on is essentially unchanged
    area = lambda m, lab: m.area_faces[lab == "canopy"].sum()
    assert abs(area(fluid, labels) - area(plain, plain_labels)) / area(plain, plain_labels) < 0.01


def test_flush_baseline_canopy_is_watertight_after_stl_roundtrip(tmp_path):
    """The flush-hinge baseline had a zero-thickness fin (two coincident triangles) that made the
    exported STL non-watertight once its points were welded; booleans.repair removes it."""

    import io

    import trimesh

    import config
    from generator import generate_pattern
    from solid import build_solid
    from solid.canopy import DEFAULT_SOLID_PARAMS

    params = dict(config.FIXED_PARAMS)
    params.update(config.PRESETS["baseline"])
    report = generate_pattern(params, output_dir=tmp_path, name="flush")
    sp = {k: params.get(k, v) for k, v in DEFAULT_SOLID_PARAMS.items()}
    sp.update(CUTS_PER_RING=report["parameters"]["CUTS_PER_RING"], SIMPLIFY_TOLERANCE=3e-3,
              BRIDGE_EXTRA=0.0, JOINT_OVERLAP=0.0, EXTRUDE_METHOD="union")
    mesh, info = build_solid(report, sp)
    assert info["is_watertight"] and info["body_count"] == 1
    out = tmp_path / "canopy.stl"
    mesh.export(str(out), file_type="stl_ascii")
    again = trimesh.load(str(out), file_type="stl")
    assert again.is_watertight and abs(again.volume - mesh.volume) < 1e-4


def test_welded_legacy_matches_users_onshape_export():
    # The user's Onshape-exported STL has 15360 triangles in 705 pieces, 111 of them with inverted
    # orientation, volume 0.7744 in^3 and a 0.0296 in^3 centre cap (checked vertex by vertex, 1.7e-6 in).
    mesh, info = build_solid(LEGACY_3D_RING_DATA, {"EXTRUDE_METHOD": "legacy", "BRIDGE_EXTRA": 0.0,
                                                  "WELD_STRIPS": 1})
    pieces = sorted(mesh.split(only_watertight=False), key=lambda m: -len(m.faces))
    assert len(mesh.faces) == 15360 and len(pieces) == 705
    assert sum(p.volume < 0 for p in pieces) == 111
    assert abs(pieces[0].volume - 0.0296) < 1e-3
    assert abs(mesh.volume - 0.7744) < 1e-3


def test_weld_union_gives_the_run11_volume():
    # The sewn solid SimScale builds from the user's STL has 0.8826 in^3, the volume reported for Run 11.
    from solid import weld_union

    mesh, _ = build_solid(LEGACY_3D_RING_DATA, {"EXTRUDE_METHOD": "legacy", "BRIDGE_EXTRA": 0.0, "WELD_STRIPS": 1})
    union = weld_union(mesh)
    assert union.is_watertight and union.body_count == 1
    assert abs(union.volume - 0.8826) < 2e-3


def test_presplit_stl_has_one_closed_block_per_piece(tmp_path):
    import trimesh

    from solid import write_presplit_stl

    mesh, _ = build_solid(LEGACY_3D_RING_DATA, {"EXTRUDE_METHOD": "legacy", "BRIDGE_EXTRA": 0.0, "WELD_STRIPS": 1})
    path = tmp_path / "presplit.stl"
    n = write_presplit_stl(mesh, path)
    text = path.read_text()
    assert n == 705 and text.count("\nsolid ") + text.startswith("solid ") == 705
    # Neighbouring slabs share faces, so every block is a complete closed solid (more facets than the welded mesh).
    blocks = trimesh.load(path).geometry.values()
    assert all(b.is_watertight and b.volume > 0 for b in blocks)
    assert abs(sum(b.volume for b in blocks) - 0.8897) < 2e-3


def test_clean_canopy_is_one_crossing_free_body_with_uniform_thickness():
    # Legacy thickening uses node-averaged normals (up to 87 deg off a ribbon), so slabs thin and fold; the clean build uses each ribbon's own normals.
    from solid.selfcheck import self_intersections

    mesh, info = build_solid(LEGACY_3D_RING_DATA, {"EXTRUDE_METHOD": "clean", "BRIDGE_EXTRA": 0.0})
    assert info["is_watertight"] and info["body_count"] == 1
    assert not self_intersections(mesh)
    # ribbons are as thick as modelled: more material than the thinned legacy weld (0.8826 in^3), within about 15%
    assert 0.88 < mesh.volume < 1.0


def test_self_intersection_finder_sees_a_crossing():
    import trimesh

    from solid.selfcheck import self_intersections

    a = trimesh.creation.box(extents=(1, 1, 1))
    b = trimesh.creation.box(extents=(1, 1, 1))
    b.apply_translation((0.31, 0.43, 0.57))
    assert self_intersections(trimesh.util.concatenate([a, b]))
    b.apply_translation((3.0, 0, 0))
    assert not self_intersections(trimesh.util.concatenate([a, b]))


def test_collapse_short_edges_merges_a_needle_edge_without_changing_the_body():
    import trimesh

    from solid import booleans

    ball = trimesh.creation.icosphere(subdivisions=2)
    a, b = ball.edges_unique[0]
    moved = ball.copy()
    moved.vertices[b] = moved.vertices[a] + (moved.vertices[b] - moved.vertices[a]) * 1e-4  # one needle edge
    assert moved.edges_unique_length.min() < 1e-3
    out = booleans.collapse_short_edges(moved, 1e-3)
    assert out.edges_unique_length.min() >= 1e-3
    assert out.is_watertight and abs(out.volume - ball.volume) < 5e-3 * ball.volume


def test_single_block_stl_has_one_closed_solid(tmp_path):
    import trimesh

    from solid import write_patch_stl

    mesh, _ = build_solid(LEGACY_3D_RING_DATA, {"EXTRUDE_METHOD": "clean", "BRIDGE_EXTRA": 0.0})
    path = tmp_path / "c.stl"
    assert write_patch_stl(mesh, path, angle_deg=180.0) == 1
    assert path.read_text().count("solid p") == 2  # solid + endsolid
    back = trimesh.load(path, process=True)
    assert back.is_watertight and abs(back.volume - mesh.volume) < 1e-4


def test_dome_canopy_is_one_clean_sheet_without_slivers():
    import numpy as np

    from solid.selfcheck import self_intersections

    mesh, info = build_solid(LEGACY_3D_RING_DATA, {"EXTRUDE_METHOD": "dome", "BRIDGE_EXTRA": 0.0})
    assert info["is_watertight"] and info["body_count"] == 1 and mesh.volume > 0
    assert not self_intersections(mesh)
    tri = mesh.triangles
    longest = np.max([np.linalg.norm(tri[:, 1] - tri[:, 0], axis=1), np.linalg.norm(tri[:, 2] - tri[:, 1], axis=1),
                      np.linalg.norm(tri[:, 0] - tri[:, 2], axis=1)], axis=0)
    assert (2 * mesh.area_faces / longest).min() > 3e-3          # no needle or sliver triangles (the old builds reached 1e-9)
    assert abs(info["canopy_depth_in"] - 10.635) < 0.15          # same dome as the legacy build
    assert abs(info["projected_diameter_in"] - 9.6) < 0.3


def test_dome_closing_fills_narrow_slots():
    from shapely.geometry import box

    from solid.dome_canopy import plan_polygon

    # two plates 0.02 in apart (narrower than MIN_FEATURE_IN) must fuse into one, with the slot filled
    strips = [(np.array([[x, y, 0.0] for x, y in box(0, 0, 1, 0.5).exterior.coords[:3]]), np.array([[0, 1, 2]]))]
    plan = plan_polygon(strips, 0.3, min_feature=0.06)
    assert plan.geom_type == "Polygon"
