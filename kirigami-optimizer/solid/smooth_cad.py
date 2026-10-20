"""WORK IN PROGRESS: canopy and flow domain as smooth CAD (OpenCascade B-rep).

The mesh-based export turned every canopy triangle into its own flat CAD
face (~22.5k faces), which SimScale's mesher could not handle cheaply.
Here each strip of the expanded kirigami is lofted as one thin solid with a
handful of smooth B-spline faces, like a native CAD model (the manual
Onshape-based setup has 954 faces). Same kinematics as solid/canopy.py.

Units: inches in, millimetres in the OpenCascade shapes (STEP is mm).
"""

import math

import numpy as np

from .canopy import DEFAULT_SOLID_PARAMS, build_open_strips, check_feasible, normalize_ring_data

MM_PER_IN = 25.4


def _strip_point(d, t):
    r = d["r1"] + t * (d["r2"] - d["r1"])
    z = d["z1"] + t * (d["z2"] - d["z1"])
    w = d["w1"] + t * (d["w2"] - d["w1"])
    th = math.radians(d["th1"] + t * (d["th2"] - d["th1"]))
    return r, z, w, th


def _centre(d, t):
    r, z, _, th = _strip_point(d, t)
    return np.array([r * math.cos(th), r * math.sin(th), z])


def strip_solid(d, thickness_in, stations=4):
    """Thin solid lofted through rectangular sections along one strip."""

    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.gp import gp_Pnt

    loft = BRepOffsetAPI_ThruSections(True, False, 1e-6)  # solid, smooth (not ruled)
    ts = np.linspace(d["t0"], d["t1"], stations)
    h = 1e-4 * (d["t1"] - d["t0"])
    for t in ts:
        r, z, w, th = _strip_point(d, t)
        c = _centre(d, t)
        tangent = _centre(d, t + h) - _centre(d, t - h)
        radial = np.array([math.cos(th), math.sin(th), 0.0])
        normal = np.cross(radial, tangent)
        normal /= np.linalg.norm(normal)
        a, b = radial * (w / 2), normal * (thickness_in / 2)
        corners = [c - a - b, c + a - b, c + a + b, c - a + b]
        poly = BRepBuilderAPI_MakePolygon()
        for p in corners:
            poly.Add(gp_Pnt(*(p * MM_PER_IN)))
        poly.Close()
        loft.AddWire(poly.Wire())
    loft.Build()
    if not loft.IsDone():
        raise RuntimeError("strip loft failed")
    return loft.Shape()


def cap_solid(cap, thickness_in):
    """Thin cone from the apex out to the cap radius, as a solid of revolution."""

    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt

    r, z, t = cap["r"], cap["z"], thickness_in / 2
    pts = [(0, 0, t), (r, 0, z + t), (r, 0, z - t), (0, 0, -t)]
    poly = BRepBuilderAPI_MakePolygon()
    for p in pts:
        poly.Add(gp_Pnt(*(np.array(p) * MM_PER_IN)))
    poly.Close()
    face = BRepBuilderAPI_MakeFace(poly.Wire(), True).Face()
    return BRepPrimAPI_MakeRevol(face, gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 2 * math.pi).Shape()


def count_faces(shape):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    exp, n = TopExp_Explorer(shape, TopAbs_FACE), 0
    while exp.More():
        n += 1
        exp.Next()
    return n


def build_canopy_cad(ring_data, params=None):
    """Returns (canopy_shape_mm, info) built from smooth lofted strips."""

    p = dict(DEFAULT_SOLID_PARAMS)
    if params:
        p.update({k: v for k, v in params.items() if k in DEFAULT_SOLID_PARAMS})
    rings = normalize_ring_data(ring_data)
    check_feasible(rings, float(p["KERF_FACTOR"]), float(p["MIN_CAP_RADIUS"]))
    record = []
    build_open_strips(rings, int(round(p["CUTS_PER_RING"])), float(p["VERTICAL_DROP"]),
                      float(p["KERF_FACTOR"]), float(p["JOINT_OVERLAP"]), record=record)
    thickness = float(p["SHEET_THICKNESS"])
    pieces = [strip_solid(d, thickness) if d["kind"] == "strip" else cap_solid(d, thickness) for d in record]
    # TODO (work in progress): fuse the pieces (BRepAlgoAPI_Fuse with fuzzy
    # value), ShapeUpgrade_UnifySameDomain, BRepCheck, then cut from the box
    # and write a named STEP; measure the face count against the manual 954.
    return pieces, {"pieces": len(pieces)}
