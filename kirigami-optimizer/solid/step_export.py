"""Writes the flow domain as a STEP solid with named, coloured boundary faces.

Why STEP instead of STL: SimScale imports every ``solid`` block of an STL
as its own sheet body, and its automatic sewing could not reliably turn the
box + floating canopy into one fluid body. A STEP file can state the right
topology outright: one solid whose outer shell is the domain box and whose
inner (void) shell is the canopy -- exactly what SimScale's "External flow
volume" + "Delete bodies" produce in the web app.

The B-rep is built directly from the watertight fluid mesh (one planar
face per triangle, edges shared between neighbouring triangles), so no
sewing is needed. Each box wall gets a STEP face name AND a distinct colour,
giving two independent ways to recognise it after import; every other
face belongs to the canopy.

Units: the mesh is in inches; the STEP file is written in millimetres.
"""

from pathlib import Path

import numpy as np
import trimesh


MM_PER_IN = 25.4

# Distinct colours per boundary (RGB 0-1). SimScale exposes face colours as
# the SDL/TYSA_COLOUR mapping attribute.
FACE_COLOURS = {
    "inlet": (1.0, 0.0, 0.0),
    "outlet": (0.0, 1.0, 0.0),
    "side_xmin": (0.0, 0.0, 1.0),
    "side_xmax": (1.0, 1.0, 0.0),
    "side_ymin": (1.0, 0.0, 1.0),
    "side_ymax": (0.0, 1.0, 1.0),
    "canopy": (0.5, 0.5, 0.5),
}
BOX_FACES = ("inlet", "outlet", "side_xmin", "side_xmax", "side_ymin", "side_ymax")


# STEP writes pure colours by name instead of as RGB.
PREDEFINED_COLOURS = {"red": (1, 0, 0), "green": (0, 1, 0), "blue": (0, 0, 1), "yellow": (1, 1, 0),
                      "magenta": (1, 0, 1), "cyan": (0, 1, 1), "black": (0, 0, 0), "white": (1, 1, 1)}


def colour_key(rgb):
    """Normalised 'R,G,B' (0-255) string used to match colours after import."""

    return ",".join(f"{round(c * 255):d}" for c in rgb)


def parse_colour(value):
    """'R,G,B' key from a colour written as a name, '#RRGGBB', or 3 numbers (0-1 or 0-255)."""

    import re

    text = str(value).strip().lower()
    if text in PREDEFINED_COLOURS:
        return colour_key(PREDEFINED_COLOURS[text])
    m = re.fullmatch(r"#?([0-9a-f]{6})", text)
    if m:
        h = m.group(1)
        return ",".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:e[-+]?\d+)?", text)]
    if len(nums) >= 3:
        rgb = nums[-3:]
        if max(rgb) <= 1.0:
            rgb = [c * 255 for c in rgb]
        return ",".join(f"{round(c):d}" for c in rgb)
    return None


COLOUR_TO_FACE = {colour_key(rgb): name for name, rgb in FACE_COLOURS.items() if name in
                  ("inlet", "outlet", "side_xmin", "side_xmax", "side_ymin", "side_ymax")}


def _patches(fluid, labels, tol_in=1e-9):
    """Groups triangles into exactly planar patches (one B-rep face each).

    Region growing from a seed triangle: a neighbour joins only if it has the
    same boundary label, a parallel normal and all its vertices within
    ``tol_in`` of the seed plane, well inside OpenCascade's 1e-7 mm vertex
    tolerance. (trimesh's own facets tolerate ~0.002 in of
    twist, which makes invalid planar faces.)
    """

    from collections import deque

    V, F, N = fluid.vertices, fluid.faces, fluid.face_normals
    neighbours = [[] for _ in range(len(F))]
    for a, b in fluid.face_adjacency:
        neighbours[a].append(b)
        neighbours[b].append(a)
    patch = np.full(len(F), -1)
    patches = []
    for seed in range(len(F)):
        if patch[seed] >= 0:
            continue
        n, p0 = N[seed], V[F[seed][0]]
        patch[seed] = len(patches)
        members, queue = [seed], deque([seed])
        while queue:
            f = queue.popleft()
            for g in neighbours[f]:
                if (patch[g] < 0 and labels[g] == labels[seed] and np.dot(N[g], n) > 1 - 1e-12
                        and np.abs((V[F[g]] - p0) @ n).max() < tol_in):
                    patch[g] = len(patches)
                    members.append(g)
                    queue.append(g)
        members = np.array(members)
        if len(members) > 1 and _pinched(F[members]):
            # Boundary loops touching at a vertex make an invalid face; keep
            # those triangles as separate faces instead.
            patches.extend(np.array([m]) for m in members)
        else:
            patches.append(members)
    return patches


def _pinched(tri_faces):
    loops = _boundary_loops(tri_faces)
    verts = [v for loop in loops for v in loop]
    return len(verts) != len(set(verts))


def _boundary_loops(tri_faces):
    """Closed loops of directed boundary edges of a set of triangles.

    Boundary edges keep the triangles' winding, so with the patch normal the
    outer loop runs counter-clockwise and holes clockwise.
    """

    directed = [(int(u), int(v)) for a, b, c in tri_faces for u, v in ((a, b), (b, c), (c, a))]
    present = set(directed)
    nxt = {}
    for u, v in directed:
        if (v, u) not in present:
            nxt.setdefault(u, []).append(v)
    loops = []
    while nxt:
        start = next(iter(nxt))
        loop, u = [start], start
        while True:
            v = nxt[u].pop()
            if not nxt[u]:
                del nxt[u]
            if v == start:
                break
            loop.append(v)
            u = v
        loops.append(loop)
    return loops


def build_brep(fluid, labels, merge_planar=True):
    """Returns (solid, faces, face_labels): one planar B-rep face per patch."""

    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeVertex
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from OCP.TopoDS import TopoDS_Shell, TopoDS_Solid, TopoDS_Wire

    V = np.asarray(fluid.vertices, dtype=float) * MM_PER_IN
    F = np.asarray(fluid.faces)
    N = np.asarray(fluid.face_normals)
    labels = np.asarray(labels)

    builder = BRep_Builder()
    vertices = {}
    edges = {}

    def vertex(i):
        if i not in vertices:
            vertices[i] = BRepBuilderAPI_MakeVertex(gp_Pnt(*V[i])).Vertex()
        return vertices[i]

    def edge(u, v):
        key = (u, v) if u < v else (v, u)
        if key not in edges:
            edges[key] = BRepBuilderAPI_MakeEdge(vertex(key[0]), vertex(key[1])).Edge()
        return edges[key] if u < v else edges[key].Reversed()

    def wire(loop):
        w = TopoDS_Wire()
        builder.MakeWire(w)
        for u, v in zip(loop, loop[1:] + loop[:1]):
            builder.Add(w, edge(u, v))
        return w

    def loop_area(loop, normal):
        pts = V[loop]
        return float(np.dot(np.cross(pts, np.roll(pts, -1, axis=0)).sum(axis=0), normal)) / 2.0

    patches = _patches(fluid, labels) if merge_planar else [np.array([i]) for i in range(len(F))]
    faces, face_labels, patch_of_triangle = [], [], np.empty(len(F), dtype=int)
    for k, tris in enumerate(patches):
        patch_of_triangle[tris] = k
        normal = N[tris[0]]
        loops = _boundary_loops(F[tris])
        loops.sort(key=lambda lp: -loop_area(lp, normal))  # outer (largest, CCW) first
        maker = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*V[loops[0][0]]), gp_Dir(*normal)), wire(loops[0]), True)
        for hole in loops[1:]:
            maker.Add(wire(hole))
        faces.append(maker.Face())
        face_labels.append(str(labels[tris[0]]))

    # One shell per connected surface: the box and the canopy void.
    groups = trimesh.graph.connected_components(fluid.face_adjacency, nodes=np.arange(len(F)))
    shells = []
    for group in groups:
        shell = TopoDS_Shell()
        builder.MakeShell(shell)
        patch_ids = sorted(set(patch_of_triangle[group].tolist()))
        for k in patch_ids:
            builder.Add(shell, faces[k])
        shell.Closed(True)
        shells.append((any(face_labels[k] in BOX_FACES for k in patch_ids), shell))
    outer = [sh for is_box, sh in shells if is_box]
    if len(outer) != 1:
        raise RuntimeError(f"expected one box shell, found {len(outer)}")

    solid = TopoDS_Solid()
    builder.MakeSolid(solid)
    builder.Add(solid, outer[0])
    for is_box, sh in shells:
        if not is_box:
            builder.Add(solid, sh)  # void: its faces already point into the cavity
    return solid, faces, face_labels


def check_solid(solid, expected_volume_in3):
    """Validates the B-rep; returns a dict of facts (raises on failure)."""

    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(solid, props)
    volume_in3 = props.Mass() / MM_PER_IN ** 3
    valid = BRepCheck_Analyzer(solid).IsValid()
    if not valid:
        raise RuntimeError("STEP solid failed OpenCascade's BRepCheck")
    if abs(volume_in3 - expected_volume_in3) > 1e-6 * expected_volume_in3:
        raise RuntimeError(f"STEP solid volume {volume_in3} != mesh volume {expected_volume_in3}")
    return {"brep_valid": valid, "brep_volume_in3": volume_in3}


def _quiet_occ():
    """Stops OpenCascade printing its transfer statistics to stdout."""

    from OCP.Message import Message, Message_Gravity

    for printer in Message.DefaultMessenger_s().Printers():
        printer.SetTraceLevel(Message_Gravity.Message_Fail)


def write_step(fluid, labels, path, check=True):
    """Writes the fluid domain as a named/coloured STEP solid. Returns info dict."""

    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_DocumentTool

    solid, faces, face_labels = build_brep(fluid, labels)
    info = check_solid(solid, float(fluid.volume)) if check else {}

    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    colours = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    main = shapes.AddShape(solid, False)
    TDataStd_Name.Set_s(main, TCollection_ExtendedString("Flow region"))

    # Only the six box walls are named/coloured: thousands of canopy styled
    # items would bloat the file, and "not a box wall" identifies them anyway.
    for face, name in zip(faces, face_labels):
        if name in BOX_FACES:
            sub = shapes.AddSubShape(main, face)
            TDataStd_Name.Set_s(sub, TCollection_ExtendedString(name))
            colours.SetColor(sub, Quantity_Color(*FACE_COLOURS[name], Quantity_TOC_RGB), XCAFDoc_ColorSurf)

    _quiet_occ()
    writer = STEPCAFControl_Writer()  # registers the write.* parameters
    for key, value in (("write.step.unit", "MM"),):
        if not Interface_Static.SetCVal_s(key, value):
            raise RuntimeError(f"could not set STEP option {key}")
    # Sub-shape (face) names; and no 2D parameter-space curves, which planar
    # faces do not need and which would double the file size.
    for key, value in (("write.stepcaf.subshapes.name", 1), ("write.surfacecurve.mode", 0)):
        if not Interface_Static.SetIVal_s(key, value):
            raise RuntimeError(f"could not set STEP option {key}")
    writer.SetNameMode(True)
    writer.SetColorMode(True)
    if not writer.Transfer(doc, STEPControl_AsIs):
        raise RuntimeError("STEP transfer failed")
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError(f"could not write {path}")
    info.update({"step_faces": len(faces), "step_box_faces": sum(lab in BOX_FACES for lab in face_labels),
                 "step_unit": "mm"})
    return info


def read_step_summary(path):
    """Reads a STEP back: solids/shells/faces, validity, and the named faces
    with their colours (parsed from the file text, as an importer sees them)."""

    import re

    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    _quiet_occ()
    reader = STEPControl_Reader()
    reader.ReadFile(str(path))
    reader.TransferRoots()
    shape = reader.OneShape()

    def count(kind):
        exp, n = TopExp_Explorer(shape, kind), 0
        while exp.More():
            n += 1
            exp.Next()
        return n

    text = Path(path).read_text(errors="replace")
    entities = dict(re.findall(r"^(#\d+)\s*=\s*(.*?);\s*$", text, flags=re.M | re.S))
    named = {}
    for ref, body in entities.items():
        m = re.match(r"ADVANCED_FACE\('([^']+)'", body)
        if m:
            named.setdefault(m.group(1), {"faces": 0, "ids": []})
            named[m.group(1)]["faces"] += 1
            named[m.group(1)]["ids"].append(ref)
    # colour of each named face: STYLED_ITEM(..., (style), #face) -> ... -> COLOUR_RGB
    colour_of = {}
    for ref, body in entities.items():
        m = re.match(r"(?:OVER_RIDING_)?STYLED_ITEM\('[^']*',\(([^)]*)\),(#\d+)", body)
        if not m:
            continue
        todo, seen = [x.strip() for x in m.group(1).split(",")], set()
        while todo:
            r = todo.pop()
            if r in seen or r not in entities:
                continue
            seen.add(r)
            c = re.match(r"COLOUR_RGB\('[^']*',([^,]+),([^,]+),([^)]+)\)", entities[r])
            if c:
                colour_of[m.group(2)] = colour_key(tuple(float(v) for v in c.groups()))
                break
            c = re.match(r"DRAUGHTING_PRE_DEFINED_COLOUR\('([^']+)'\)", entities[r])
            if c:
                colour_of[m.group(2)] = parse_colour(c.group(1))
                break
            todo.extend(re.findall(r"#\d+", entities[r]))
    for info in named.values():
        info["colours"] = sorted({colour_of.get(r, "none") for r in info.pop("ids")})
    return {"solids": count(TopAbs_SOLID), "shells": count(TopAbs_SHELL), "faces": count(TopAbs_FACE),
            "valid": bool(BRepCheck_Analyzer(shape).IsValid()), "named_faces": named}
