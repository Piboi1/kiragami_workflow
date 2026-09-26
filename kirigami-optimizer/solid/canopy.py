"""3D canopy solid builder (refactor + fix of legacy/kiragami3dcreator.py).

Units: inches.

Fixes relative to the original Colab script:
  * ``shell`` was used in the "EXTRUDE TO WATERTIGHT SOLID" section but never
    built from ``global_verts`` / ``global_faces``. It is now built explicitly.
  * The Colab-only ``files.download()`` call is gone; build_solid() returns a
    trimesh object and export_stl() writes it wherever the caller wants.
  * The ``k == 0 or k == last`` rim bridges, the 45 deg segment and the
    22.5 deg stagger were hard-coded for 8 cuts. They now follow CUTS_PER_RING
    (identical output for 8 cuts).

Two extrusion methods are available:
  * "union" (default): each strip is extruded into its own closed slab and the
    slabs are boolean-unioned (manifold3d) into one clean, non-self-intersecting
    solid. This is what the flow-domain boolean needs.
  * "legacy": exactly what the original script intended -- extrude the whole
    open shell along vertex normals and stitch the boundary edges. The result
    is watertight per strip but strips overlap each other. Kept so an STL can
    be reproduced for comparison against the manual SimScale runs.
"""

import math

import numpy as np
import trimesh

DEFAULT_SOLID_PARAMS = {
    "CUTS_PER_RING": 8,
    "VERTICAL_DROP": 10.62,      # max Z-drop of the deployed canopy (inches)
    "KERF_FACTOR": 0.85,         # ribbon width / ring spacing
    "SHEET_THICKNESS": 0.015,    # CFD solid thickness (inches)
    "EXTRUDE_METHOD": "union",   # "union" or "legacy"
    # "union" only: each strip is lengthened by this much at both ends (and
    # the center cap radius grows by it) so neighbouring strips overlap by a
    # real volume instead of just touching; the boolean then fuses them into
    # one connected canopy. Ignored by "legacy".
    "JOINT_OVERLAP": 0.03,       # inches
    # Feasibility: the solid center cap (inside the first ring's bridges)
    # must keep at least this radius. Sparse inner rings make the ribbons
    # (KERF_FACTOR x ring spacing) wider than the first ring's radius, which
    # is not a physical pattern and breaks the boolean union.
    "MIN_CAP_RADIUS": 0.05,      # inches
}


class InfeasibleGeometry(ValueError):
    """The requested pattern cannot be built as a physical canopy."""


def normalize_ring_data(ring_data):
    """Returns [(radius_in, gap_deg), ...] from generator rings or legacy tuples.

    Accepts:
      * the ``rings`` list from generate_pattern() / a *.report.json
        (dicts with radius_inches and actual_gap_degrees), or a whole
        report dict containing ``rings``;
      * the legacy tuple format (ring_number, radius, cut_angle, gap_angle).
    """

    if isinstance(ring_data, dict):
        ring_data = ring_data["rings"]

    out = []
    for ring in ring_data:
        if isinstance(ring, dict):
            out.append((float(ring["radius_inches"]), float(ring["actual_gap_degrees"])))
        else:
            out.append((float(ring[1]), float(ring[3])))
    if len(out) < 2:
        raise ValueError("build_solid needs at least 2 rings")
    return out


def build_open_strips(rings, cuts_per_ring, vertical_drop, kerf_factor, joint_overlap=0.0):
    """Expanded-kirigami kinematics from the original script.

    Returns a list of strips; each strip is (vertices Nx3, faces Mx3) of an
    open quad-strip surface. With joint_overlap=0 the list order and vertex
    layout match the original global_verts / global_faces construction.
    """

    r_dep = [r for r, _ in rings]
    gap_deg = [g for _, g in rings]
    r_max = r_dep[-1]
    segment = 360.0 / cuts_per_ring
    half = segment / 2.0

    def get_z(r):
        return -vertical_drop * (r / r_max) ** 2

    def get_w(k):
        if k < len(r_dep) - 1:
            return (r_dep[k + 1] - r_dep[k]) * kerf_factor
        return (r_dep[-1] - r_dep[-2]) * kerf_factor

    strips = []

    def centerline(t, r1, z1, th1, r2, z2, th2):
        r, z = r1 + t * (r2 - r1), z1 + t * (z2 - z1)
        th = math.radians(th1 + t * (th2 - th1))
        return np.array([r * math.cos(th), r * math.sin(th), z])

    def add_strip(r1, z1, w1, th1, r2, z2, w2, th2, segs):
        """Continuous quad strip connecting two coordinates."""
        # Parameter range, optionally extended past both ends by
        # joint_overlap (measured along the strip centerline).
        t0, t1 = 0.0, 1.0
        if joint_overlap > 0:
            pts = [centerline(j / segs, r1, z1, th1, r2, z2, th2) for j in range(segs + 1)]
            length = sum(np.linalg.norm(b - a) for a, b in zip(pts, pts[1:]))
            if length > 0:
                t0, t1 = -joint_overlap / length, 1.0 + joint_overlap / length
        verts = []
        faces = []
        for j in range(segs + 1):
            t = t0 + (t1 - t0) * j / segs
            r, z, w = r1 + t * (r2 - r1), z1 + t * (z2 - z1), w1 + t * (w2 - w1)
            th = math.radians(th1 + t * (th2 - th1))
            verts.extend([
                [(r - w / 2) * math.cos(th), (r - w / 2) * math.sin(th), z],
                [(r + w / 2) * math.cos(th), (r + w / 2) * math.sin(th), z],
            ])
            if j > 0:
                v = j * 2
                faces.extend([[v - 2, v - 1, v], [v - 1, v + 1, v]])
        strips.append((np.array(verts, dtype=float), np.array(faces, dtype=np.int64)))

    # ---------------------------------------------------------
    # EXPANDED KINEMATICS (OPEN HEXAGONS)
    # ---------------------------------------------------------
    for k in range(len(r_dep)):
        r_k, z_k, w_k, gap_k = r_dep[k], get_z(r_dep[k]), get_w(k), gap_deg[k]

        # True bridge width (union of upper and lower ribbons)
        w_prev = get_w(k - 1) if k > 0 else w_k
        w_bridge = w_k + w_prev

        stagger_k = 0 if k % 2 == 0 else half

        for i in range(cuts_per_ring):
            th_c = i * segment + stagger_k
            th_L, th_R = th_c - gap_k / 2, th_c + gap_k / 2

            # 1. Horizontal bridge (hinge), double-wide
            add_strip(r_k, z_k, w_bridge, th_L, r_k, z_k, w_bridge, th_R, segs=2)

            # 2. Continuous rim bridging the gaps on the first and last ring
            if k == 0 or k == len(r_dep) - 1:
                next_th_L = ((i + 1) * segment + stagger_k) - gap_k / 2
                add_strip(r_k, z_k, w_bridge, th_R, r_k, z_k, w_bridge, next_th_L, segs=3)

            # 3. Diagonal ribbons twisting down to ring k+1
            if k < len(r_dep) - 1:
                r_k1, z_k1, w_k1, gap_k1 = r_dep[k + 1], get_z(r_dep[k + 1]), get_w(k + 1), gap_deg[k + 1]

                botL_R = (th_c - half) + gap_k1 / 2
                add_strip(r_k1, z_k1, w_k1, botL_R, r_k, z_k, w_k, th_L, segs=3)

                botR_L = (th_c + half) - gap_k1 / 2
                add_strip(r_k, z_k, w_k, th_R, r_k1, z_k1, w_k1, botR_L, segs=3)

    # ---------------------------------------------------------
    # CENTER HOLE CAP
    # ---------------------------------------------------------
    w_bridge_top = get_w(0) * 2
    cap_r, cap_z = r_dep[0] - w_bridge_top / 2 + joint_overlap, get_z(r_dep[0])

    cap_angles = []
    for i in range(cuts_per_ring):
        th_c, next_th_c = i * segment, (i + 1) * segment
        cap_angles.extend(np.linspace(th_c - gap_deg[0] / 2, th_c + gap_deg[0] / 2, 3)[:-1])
        cap_angles.extend(np.linspace(th_c + gap_deg[0] / 2, next_th_c - gap_deg[0] / 2, 4)[:-1])

    cap_verts = [[0.0, 0.0, 0.0]]  # apex origin at Z=0
    for th_deg in cap_angles:
        th = math.radians(th_deg)
        cap_verts.append([cap_r * math.cos(th), cap_r * math.sin(th), cap_z])
    n_cap = len(cap_angles)
    cap_faces = [[0, (idx + 1) % n_cap + 1, idx + 1] for idx in range(n_cap)]
    strips.append((np.array(cap_verts, dtype=float), np.array(cap_faces, dtype=np.int64)))

    return strips


def _directed_boundary_edges(faces):
    """Directed edges (a, b) of ``faces`` that have no opposite twin (b, a)."""

    edges = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    edge_set = {tuple(e) for e in edges.tolist()}
    return np.array([e for e in edges.tolist() if (e[1], e[0]) not in edge_set], dtype=np.int64)


def extrude_surface(vertices, faces, thickness):
    """Thickens an open, consistently wound surface into a closed solid.

    Offsets by +/- thickness/2 along vertex normals and stitches the
    boundary with side quads whose winding matches the top/bottom faces,
    so the result is watertight with outward normals.
    """

    surface = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    normals = surface.vertex_normals
    n_v = len(vertices)
    v_top = vertices + normals * (thickness / 2.0)
    v_bot = vertices - normals * (thickness / 2.0)

    side = []
    for a, b in _directed_boundary_edges(faces):
        side.append([b, a, a + n_v])
        side.append([b, a + n_v, b + n_v])

    solid = trimesh.Trimesh(
        vertices=np.vstack((v_top, v_bot)),
        faces=np.vstack((faces, np.fliplr(faces) + n_v, np.array(side, dtype=np.int64).reshape(-1, 3))),
        process=True,
    )
    if solid.volume < 0:
        solid.invert()
    return solid


def _legacy_extrude(strips, thickness):
    """The original script's section 4, with the missing ``shell`` built."""

    global_verts = []
    global_faces = []
    for verts, faces in strips:
        offset = len(global_verts)
        global_verts.extend(verts.tolist())
        global_faces.extend((faces + offset).tolist())

    # FIX: build the shell that the original code referenced but never created.
    # process=False keeps strips as separate components (merging coincident
    # vertices would create non-manifold edges between touching strips).
    shell = trimesh.Trimesh(vertices=np.array(global_verts), faces=np.array(global_faces), process=False)

    normals = shell.vertex_normals
    v_top = shell.vertices + normals * (thickness / 2.0)
    v_bot = shell.vertices - normals * (thickness / 2.0)
    V_solid = np.vstack((v_top, v_bot))
    N_v = len(v_top)

    F_solid = np.vstack((shell.faces, np.fliplr(shell.faces) + N_v))

    # Stitch open boundaries to seal the volume
    edges = shell.edges_sorted
    unique_edges, counts = np.unique(edges, axis=0, return_counts=True)
    boundary_edges = unique_edges[counts == 1]

    stitch_faces = []
    for v0, v1 in boundary_edges:
        stitch_faces.extend([[v0, v1, v1 + N_v], [v0, v1 + N_v, v0 + N_v]])

    # process=False: merging coincident vertices across strips would create
    # edges shared by four faces and break per-strip watertightness.
    solid = trimesh.Trimesh(vertices=V_solid, faces=np.vstack((F_solid, stitch_faces)), process=False)
    trimesh.repair.fix_normals(solid, multibody=True)
    return solid


def check_feasible(rings, kerf_factor, min_cap_radius):
    """Raises InfeasibleGeometry if ribbons would cross the canopy center."""

    r = [radius for radius, _ in rings]
    cap_r = r[0] - (r[1] - r[0]) * kerf_factor  # r0 - w_bridge/2, w_bridge = 2*w0
    if cap_r < min_cap_radius:
        raise InfeasibleGeometry(
            f"center cap radius {cap_r:.3f} in < {min_cap_radius} in: first ring at {r[0]:.3f} in "
            f"but ribbons are {(r[1] - r[0]) * kerf_factor:.3f} in wide"
        )


def build_solid(ring_data, params=None):
    """Builds the watertight 3D canopy solid.

    Args:
        ring_data: generator output (report dict or its ``rings`` list) or the
            legacy tuple list (ring_number, radius_in, cut_deg, gap_deg).
        params: dict with any of DEFAULT_SOLID_PARAMS (CUTS_PER_RING,
            VERTICAL_DROP, KERF_FACTOR, SHEET_THICKNESS, EXTRUDE_METHOD).
            Extra keys are ignored, so the full design dict can be passed.

    Returns:
        (mesh, info): the trimesh solid (inches) and a dict of geometric
        quantities used downstream (projected radius/area, depth, ...).
    """

    p = dict(DEFAULT_SOLID_PARAMS)
    if params:
        p.update({k: v for k, v in params.items() if k in DEFAULT_SOLID_PARAMS})
    cuts = int(round(p["CUTS_PER_RING"]))
    thickness = float(p["SHEET_THICKNESS"])

    rings = normalize_ring_data(ring_data)
    check_feasible(rings, float(p["KERF_FACTOR"]), float(p["MIN_CAP_RADIUS"]))
    method = p["EXTRUDE_METHOD"]
    joint_overlap = float(p["JOINT_OVERLAP"]) if method == "union" else 0.0
    strips = build_open_strips(rings, cuts, float(p["VERTICAL_DROP"]), float(p["KERF_FACTOR"]), joint_overlap)

    if method == "union":
        slabs = [extrude_surface(v, f, thickness) for v, f in strips]
        mesh = trimesh.boolean.union(slabs, engine="manifold")
    elif method == "legacy":
        mesh = _legacy_extrude(strips, thickness)
    else:
        raise ValueError(f"unknown EXTRUDE_METHOD {method!r}")

    radial = np.linalg.norm(mesh.vertices[:, :2], axis=1)
    projected_radius = float(radial.max())
    info = {
        "method": method,
        "is_watertight": bool(mesh.is_watertight),
        "body_count": int(mesh.body_count),
        "triangle_count": int(len(mesh.faces)),
        "projected_radius_in": projected_radius,
        "projected_diameter_in": 2 * projected_radius,
        # Reference area for Cd: disk of the canopy's maximum radial extent.
        "reference_area_in2": math.pi * projected_radius ** 2,
        "canopy_depth_in": float(mesh.vertices[:, 2].max() - mesh.vertices[:, 2].min()),
        "surface_area_in2": float(mesh.area),
        "volume_in3": float(mesh.volume),
        "bounds_in": mesh.bounds.tolist(),
    }
    return mesh, info


def export_stl(mesh, path):
    """Writes the solid as binary STL (inches)."""

    mesh.export(str(path))
    return str(path)
