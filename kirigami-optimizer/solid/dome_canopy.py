"""Dome canopy generator (EXTRUDE_METHOD "dome"): the kirigami web built as ONE thickened sheet instead of hundreds of overlapping slabs.

Why a rewrite. Every failure seen so far (SimScale mesher "self intersection", slit/sliver faces, solver divergence in the open ribbon web) came from
geometry the old builds created, not from the pattern:

* slabs were thickened along node-averaged normals and then fused by a boolean, which left crossing triangles, needle edges (1e-7 in) and
  slivers where near-coincident faces met (mesher failures, STEP/STL import faults);
* ribbons met at acute wedges with sharp inside corners, and the voids between them narrowed to a point; cells in such wedges are extremely skewed
  and the steady solver blew up at one of them, on a different ribbon every run (r 1.0 .. 2.3 in).

What this builds instead (same ring/cut pattern, same dome profile z = -drop * (r / r_max)^2, same hole layout seen from above):

1. the plan view of the pattern: the union of every ribbon footprint of ``build_open_strips`` plus the centre cap, as ONE 2D polygon (exact 2D union);
2. morphological closing with radius ``MIN_FEATURE_IN / 2``: slots narrower than ``MIN_FEATURE_IN`` fill in and every sharp inside corner becomes a
   fillet, so no void ends in a point and no hole is smaller than the mesher can resolve;
3. a quality triangulation of that polygon (no minimum angle below 28 degrees, graded by ``MESH_H_IN``);
4. the triangles are lifted onto the dome and offset by +-thickness/2 along the dome's own (analytic, smooth) normal, with side walls along the boundary.

The result is one closed, manifold body by construction: no boolean, no overlapping slabs, no crossing triangles, no needle triangles, uniform
thickness, and a smooth apex (the old cap was a fan of 30 slender wedges meeting in one vertex).
"""

import math

import numpy as np
import trimesh

MIN_FEATURE_IN = 0.06      # voids narrower than this fill in (closing radius = half of it); every inside corner gets at least this fillet diameter
MESH_H_IN = 0.09           # target triangle edge length of the sheet (inches)
MIN_ANGLE_DEG = 28.0       # Delaunay quality bound
CAP_OVERLAP_IN = 0.01      # the cap disc reaches this far into the first ring so the two fuse
SIMPLIFY_IN = 0.004        # boundary simplification (removes the micro-edges that closing's arcs leave)


def plan_polygon(strips, cap_r, min_feature=MIN_FEATURE_IN, cap_overlap=CAP_OVERLAP_IN):
    """The pattern seen from above as one shapely polygon (with holes), closing applied."""

    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union

    pieces = [Point(0.0, 0.0).buffer(cap_r + cap_overlap, 64)]
    for vertices, faces in strips:
        for a, b, c in faces:
            tri = Polygon(vertices[[a, b, c], :2])
            if tri.area > 1e-12:
                pieces.append(tri.buffer(0))
    plan = unary_union(pieces)
    radius = min_feature / 2.0
    plan = plan.buffer(radius, 8).buffer(-radius, 8)  # closing: fills narrow slots, fillets the inside corners
    plan = plan.simplify(SIMPLIFY_IN, preserve_topology=True)
    if plan.geom_type != "Polygon":  # keep the connected main body (a stray piece would be a loose floating part)
        plan = max(plan.geoms, key=lambda g: g.area)
    return plan


def triangulate(plan, h=MESH_H_IN, min_angle=MIN_ANGLE_DEG):
    """Quality constrained Delaunay triangulation of a shapely polygon. Returns (points Nx2, triangles Mx3), triangles counter-clockwise."""

    import triangle

    points, segments, holes = [], [], []

    def add_ring(coords):
        coords = np.asarray(coords)[:-1]
        # subdivide long boundary edges so the boundary is as fine as the interior
        ring = []
        for p, q in zip(coords, np.roll(coords, -1, axis=0)):
            n = max(1, int(math.ceil(np.linalg.norm(q - p) / h)))
            ring.extend(p + (q - p) * k / n for k in range(n))
        start = len(points)
        points.extend(ring)
        segments.extend((start + i, start + (i + 1) % len(ring)) for i in range(len(ring)))

    add_ring(plan.exterior.coords)
    for hole in plan.interiors:
        add_ring(hole.coords)
        from shapely.geometry import Polygon

        holes.append(list(Polygon(hole).representative_point().coords[0]))
    spec = {"vertices": np.array(points), "segments": np.array(segments)}
    if holes:
        spec["holes"] = np.array(holes)
    out = triangle.triangulate(spec, f"pq{min_angle:g}a{0.5 * h * h:.6f}")
    return out["vertices"], out["triangles"]


def dome_profile(r, drop, r_max):
    """Height and slope of the dome z = -drop * (r / r_max)^2."""

    return -drop * (r / r_max) ** 2, -2.0 * drop * r / r_max ** 2


def build_dome_canopy(strips, cap_r, vertical_drop, r_max, thickness, min_feature=MIN_FEATURE_IN, h=MESH_H_IN):
    """Returns the closed, outward-oriented canopy solid (trimesh, inches)."""

    plan = plan_polygon(strips, cap_r, min_feature)
    pts, tris = triangulate(plan, h)
    r = np.hypot(pts[:, 0], pts[:, 1])
    z, slope = dome_profile(r, vertical_drop, r_max)
    centre = np.column_stack([pts, z])
    unit = np.where(r > 1e-12, 1.0 / np.maximum(r, 1e-12), 0.0)
    normal = np.column_stack([-slope * pts[:, 0] * unit, -slope * pts[:, 1] * unit, np.ones(len(pts))])  # dome normal, pointing up/outward
    normal /= np.linalg.norm(normal, axis=1)[:, None]
    n = len(pts)
    top = centre + normal * (thickness / 2.0)
    bottom = centre - normal * (thickness / 2.0)

    # triangles are counter-clockwise in plan view, so the top surface faces up; the bottom is the mirror image
    tris = np.asarray(tris)
    d1, d2 = pts[tris[:, 1]] - pts[tris[:, 0]], pts[tris[:, 2]] - pts[tris[:, 0]]
    ccw = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
    tris = np.where((ccw < 0)[:, None], tris[:, ::-1], tris)
    edges = np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    present = {tuple(e) for e in edges.tolist()}
    boundary = [(a, b) for a, b in edges.tolist() if (b, a) not in present]
    walls = []
    for a, b in boundary:  # boundary edge a->b has material on its left; the outward wall faces right
        walls.append([a, b + n, b])
        walls.append([a, a + n, b + n])
    faces = np.vstack([tris, tris[:, ::-1] + n, np.array(walls, dtype=np.int64)])
    mesh = trimesh.Trimesh(np.vstack([top, bottom]), faces, process=False)
    trimesh.repair.fix_normals(mesh)
    if mesh.volume < 0:
        mesh.invert()
    return mesh
