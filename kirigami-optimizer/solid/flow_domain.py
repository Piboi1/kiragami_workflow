"""External-flow domain (box minus canopy) with named boundary faces.

SimScale's "External flow volume" + "Delete bodies" are CAD-mode operations
that the public SimScale API/SDK does not expose. So the fluid region is
built locally instead: a box sized relative to the canopy diameter, with the
canopy solid boolean-subtracted. It is written as a multi-solid ASCII STL
where each ``solid <name>`` block is one boundary, so the SimScale import
yields separately named faces for the BC assignments:

    inlet     (Max Z)  velocity inlet, flow travels in -Z
    outlet    (Min Z)  pressure outlet
    side_xmin / side_xmax / side_ymin / side_ymax   slip walls
    canopy    the parachute surface (no-slip wall, force coefficients)

Default box multipliers reproduce the manual Kiragami_CFD box for a 10 in
canopy: X/Y +-25.83 in, Z from -70 in to +31.13 in.
"""

import numpy as np
import trimesh

DEFAULT_DOMAIN_PARAMS = {
    # Multiples of the flat PARACHUTE_DIAMETER.
    "DOMAIN_LATERAL_HALF_WIDTH_D": 2.583,  # +-X and +-Y
    "DOMAIN_UPSTREAM_D": 3.113,            # Max Z (inlet side, above the apex)
    "DOMAIN_DOWNSTREAM_D": 7.0,            # Min Z (outlet side, wake)
}

FACE_NAMES = ("inlet", "outlet", "side_xmin", "side_xmax", "side_ymin", "side_ymax", "canopy")


def domain_box_bounds(diameter_in, params=None):
    """Returns ((xmin, ymin, zmin), (xmax, ymax, zmax)) in inches."""

    p = dict(DEFAULT_DOMAIN_PARAMS)
    if params:
        p.update({k: v for k, v in params.items() if k in DEFAULT_DOMAIN_PARAMS})
    lat = p["DOMAIN_LATERAL_HALF_WIDTH_D"] * diameter_in
    return (
        (-lat, -lat, -p["DOMAIN_DOWNSTREAM_D"] * diameter_in),
        (lat, lat, p["DOMAIN_UPSTREAM_D"] * diameter_in),
    )


def build_flow_domain(canopy, diameter_in, params=None):
    """Subtracts the canopy from the domain box and labels every triangle.

    Returns (fluid_mesh, labels, bounds) where labels[i] is the FACE_NAMES
    entry for fluid_mesh.faces[i].
    """

    lo, hi = domain_box_bounds(diameter_in, params)
    lo, hi = np.array(lo), np.array(hi)
    if np.any(canopy.bounds[0] <= lo) or np.any(canopy.bounds[1] >= hi):
        raise ValueError(f"canopy {canopy.bounds.tolist()} does not fit inside domain {lo.tolist()} {hi.tolist()}")

    box = trimesh.creation.box(bounds=[lo, hi])
    fluid = trimesh.boolean.difference([box, canopy], engine="manifold")
    if not fluid.is_watertight:
        raise RuntimeError("flow domain is not watertight after the boolean")

    tol = 1e-6 * float(np.max(hi - lo))
    tri = fluid.triangles  # (n, 3, 3)
    labels = np.full(len(fluid.faces), "canopy", dtype=object)
    planes = [
        ("side_xmin", 0, lo[0]), ("side_xmax", 0, hi[0]),
        ("side_ymin", 1, lo[1]), ("side_ymax", 1, hi[1]),
        ("outlet", 2, lo[2]), ("inlet", 2, hi[2]),
    ]
    for name, axis, value in planes:
        on_plane = np.all(np.abs(tri[:, :, axis] - value) < tol, axis=1)
        labels[on_plane] = name

    missing = [n for n in FACE_NAMES if n not in set(labels)]
    if missing:
        raise RuntimeError(f"flow domain is missing boundary groups: {missing}")

    return fluid, labels, (lo.tolist(), hi.tolist())


def write_named_stl(mesh, labels, path, scale=1.0):
    """Writes an ASCII STL with one ``solid <name>`` block per label."""

    tri = mesh.triangles * scale
    normals = mesh.face_normals
    with open(path, "w", encoding="ascii") as fh:
        for name in FACE_NAMES:
            idx = np.nonzero(labels == name)[0]
            if len(idx) == 0:
                continue
            fh.write(f"solid {name}\n")
            for i in idx:
                n = normals[i]
                fh.write(f"  facet normal {n[0]:.7e} {n[1]:.7e} {n[2]:.7e}\n    outer loop\n")
                for v in tri[i]:
                    fh.write(f"      vertex {v[0]:.9e} {v[1]:.9e} {v[2]:.9e}\n")
                fh.write("    endloop\n  endfacet\n")
            fh.write(f"endsolid {name}\n")
    return str(path)
