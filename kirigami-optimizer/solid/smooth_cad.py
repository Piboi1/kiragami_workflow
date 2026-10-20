"""Canopy and flow domain as smooth CAD (OpenCascade B-rep).

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


def _shape_list(shapes):
    from OCP.collections import List_TopoDS_Shape

    lst = List_TopoDS_Shape()
    for sh in shapes:
        lst.Append(sh)
    return lst


def fuse_pieces(pieces, fuzzy_mm=1e-4):
    """General-fuse of all strip/cap solids into one solid, same-surface faces merged."""

    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

    if len(pieces) == 1:
        return pieces[0]
    op = BRepAlgoAPI_Fuse()
    op.SetArguments(_shape_list(pieces[:1]))
    op.SetTools(_shape_list(pieces[1:]))
    op.SetRunParallel(True)
    op.SetFuzzyValue(fuzzy_mm)
    op.Build()
    if not op.IsDone() or op.HasErrors():
        raise RuntimeError("canopy fuse failed")
    unify = ShapeUpgrade_UnifySameDomain(op.Shape(), True, True, True)
    unify.Build()
    return unify.Shape()


def fuse_staged(pieces, group=24, fuzzy_mm=1e-4):
    """Fuses neighbouring pieces in small groups first, then the groups.

    One general-fuse over hundreds of pieces is slow and memory-hungry; the
    pieces are in ring order, so consecutive ones are spatial neighbours.
    """

    level = list(pieces)
    while len(level) > 1:
        level = [fuse_pieces(level[i:i + group], fuzzy_mm) if len(level[i:i + group]) > 1 else level[i]
                 for i in range(0, len(level), group)]
    return level[0]


def volume_mm3(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def is_valid(shape):
    from OCP.BRepCheck import BRepCheck_Analyzer

    return bool(BRepCheck_Analyzer(shape).IsValid())


def count_solids(shape):
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    exp, n = TopExp_Explorer(shape, TopAbs_SOLID), 0
    while exp.More():
        n += 1
        exp.Next()
    return n


def build_canopy_cad(ring_data, params=None, fuzzy_mm=1e-4, group=0):
    """Returns (canopy_shape_mm, info): the fused, face-merged smooth canopy."""

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
    canopy = fuse_staged(pieces, group, fuzzy_mm) if group else fuse_pieces(pieces, fuzzy_mm)
    info = {"pieces": len(pieces), "canopy_faces": count_faces(canopy), "canopy_solids": count_solids(canopy),
            "canopy_valid": is_valid(canopy), "canopy_volume_in3": volume_mm3(canopy) / MM_PER_IN ** 3}
    return canopy, info


def build_flow_cad(canopy, lo_in, hi_in):
    """Box (inches bounds) minus the canopy, as one OpenCascade solid (mm)."""

    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    lo = np.array(lo_in) * MM_PER_IN
    hi = np.array(hi_in) * MM_PER_IN
    box = BRepPrimAPI_MakeBox(gp_Pnt(*lo), gp_Pnt(*hi)).Shape()
    cut = BRepAlgoAPI_Cut(box, canopy)
    if not cut.IsDone() or cut.HasErrors():
        raise RuntimeError("box - canopy failed")
    return cut.Shape()


def write_flow_step(fluid, lo_in, hi_in, path):
    """Writes the fluid solid with the six box walls named and coloured."""

    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_DocumentTool

    from .step_export import FACE_COLOURS, _quiet_occ

    lo, hi = np.array(lo_in) * MM_PER_IN, np.array(hi_in) * MM_PER_IN
    planes = [("side_xmin", 0, lo[0]), ("side_xmax", 0, hi[0]), ("side_ymin", 1, lo[1]),
              ("side_ymax", 1, hi[1]), ("outlet", 2, lo[2]), ("inlet", 2, hi[2])]

    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    colours = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    main = shapes.AddShape(fluid, False)
    TDataStd_Name.Set_s(main, TCollection_ExtendedString("Flow region"))

    named, exp = set(), TopExp_Explorer(fluid, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() == GeomAbs_Plane:
            pln = surf.Plane()
            axis, origin = pln.Axis().Direction(), pln.Location()
            for name, k, value in planes:
                comp = (axis.X(), axis.Y(), axis.Z())[k]
                loc = (origin.X(), origin.Y(), origin.Z())[k]
                if abs(abs(comp) - 1.0) < 1e-9 and abs(loc - value) < 1e-6:
                    sub = shapes.AddSubShape(main, face)
                    TDataStd_Name.Set_s(sub, TCollection_ExtendedString(name))
                    colours.SetColor(sub, Quantity_Color(*FACE_COLOURS[name], Quantity_TOC_RGB), XCAFDoc_ColorSurf)
                    named.add(name)
        exp.Next()
    if len(named) != 6:
        raise RuntimeError(f"found box walls {sorted(named)}, expected all six")

    _quiet_occ()
    writer = STEPCAFControl_Writer()
    for key, value in (("write.step.unit", "MM"),):
        Interface_Static.SetCVal_s(key, value)
    for key, value in (("write.stepcaf.subshapes.name", 1),):
        Interface_Static.SetIVal_s(key, value)
    writer.SetNameMode(True)
    writer.SetColorMode(True)
    if not writer.Transfer(doc, STEPControl_AsIs):
        raise RuntimeError("STEP transfer failed")
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError(f"could not write {path}")
    return {"named_walls": sorted(named), "step_faces": count_faces(fluid)}
