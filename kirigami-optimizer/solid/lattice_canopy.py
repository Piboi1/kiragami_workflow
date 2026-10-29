"""Lattice canopy generator (EXTRUDE_METHOD "lattice"): the legacy hexagon web, rebuilt so that no near-degenerate geometry is left in it.

The pattern is exactly the legacy one (``build_open_strips``: flat hinge bands, steep diagonal ribbons, honeycomb of hexagonal cells, flat rim band).
What changes is how the ribbons are turned into a solid. The legacy build thickened every strip along node-averaged normals (up to 87 degrees off a ribbon)
and then fused hundreds of overlapping slabs, which left crossing triangles, needle edges (1e-7 in) and slivers (1e-9 in) wherever near-coincident faces met.
Those were the failures SimScale reported (mesher "self intersection", slit faces, ``fault-edge-invalid``).

1. every ribbon is thickened along ITS OWN normals (a uniform sheet, no skew), and ends exactly at its two node edges;
2. a small octagonal PIN (radius ``PIN_RADIUS_T`` x thickness, a little longer than the ribbon is wide) is laid along every node edge: both ribbon ends
   lie inside it, so ribbons fuse through a block of material instead of through a hair-thin contact, and the faces that intersect do so at large angles;
3. the centre cap is a smooth bell (flat at the apex and flat at the rim, no 30-wedge fan with one apex vertex), 1.5x the sheet thickness so it never shares a face plane with the first ring;
4. the exact union (manifold3d), then triangles thinner than ``CLEAN_MIN_ALTITUDE_IN`` and edges shorter than ``CLEAN_MIN_EDGES_IN`` are merged, undoing any merge
   that would push a triangle through its neighbour; the result is checked to be one watertight body without crossing triangles.
"""

import numpy as np
import trimesh

from . import booleans
from .selfcheck import self_intersections

CLEAN_MIN_EDGES_IN = (1e-3, 7e-4, 5e-4)
CLEAN_MIN_ALTITUDE_IN = 1.5e-3
MIN_TRIANGLE_HEIGHT_IN = 4e-4  # a canopy with a thinner triangle than this is refused (the old builds had 1e-9; SimScale rejects below about 1e-4)


def _pins(strips, thickness, radius_t, extension_t):
    """Octagonal prisms along every node edge (the first and last vertex pair of each strip)."""

    nodes = {}
    for vertices, _ in strips:
        for a, b in ((0, 1), (len(vertices) - 2, len(vertices) - 1)):
            nodes[tuple(np.round((vertices[a] + vertices[b]) / 2.0, 5))] = (vertices[a], vertices[b])
    pins = []
    for start, end in nodes.values():
        axis = end - start
        length = float(np.linalg.norm(axis))
        if length < 1e-9:
            continue
        pin = trimesh.creation.cylinder(radius=radius_t * thickness, height=length + 2 * extension_t * thickness, sections=8)
        transform = trimesh.geometry.align_vectors([0, 0, 1], axis / length)
        transform[:3, 3] = (start + end) / 2.0
        pin.apply_transform(transform)
        pins.append(pin)
    return pins


def cap_shell(radius, z_rim, thickness, sections=64, steps=24):
    """The centre cap: a smooth bell from the apex (z = 0, flat) down to the first ring's height, flat again at the rim.

    z(r) = z_rim * (1 - (1 - (r / radius)^2)^2): horizontal at the apex (no point, no 30-wedge fan) and horizontal at the rim, so it meets the
    flat first-ring band without a crease. It is a shell 1.5 sheet thicknesses thick (never coplanar with the band), revolved about the axis.
    """

    h = 1.5 * thickness
    r = np.linspace(0.0, radius, steps)
    u = r / radius
    z = z_rim * (1.0 - (1.0 - u ** 2) ** 2)
    dz = z_rim * 4.0 * u * (1.0 - u ** 2) / radius
    n = np.column_stack([-dz, np.ones_like(dz)])
    n /= np.linalg.norm(n, axis=1)[:, None]
    outer = np.column_stack([r, z]) + n * (h / 2)
    inner = np.column_stack([r, z]) - n * (h / 2)
    outer[0, 0] = inner[0, 0] = 0.0
    profile = np.vstack([outer, inner[::-1]])  # up the outside, back down the inside: a closed section
    shell = trimesh.creation.revolve(profile, sections=sections)
    if shell.volume < 0:
        shell.invert()
    return shell


def merge_needles(mesh):
    """Merges short edges and sliver triangles with the largest threshold that creates no crossing triangle."""

    for edge in CLEAN_MIN_EDGES_IN:
        candidate = booleans.collapse_short_edges(mesh, edge, rounds=8, min_altitude=CLEAN_MIN_ALTITUDE_IN)
        if not self_intersections(candidate, limit=1):
            mesh = candidate
            break
    return booleans.flip_slivers(mesh, CLEAN_MIN_ALTITUDE_IN)  # slivers the merges had to leave: re-triangulate them in place


def _attempt(ribbons, cap, ring0_radius, thickness, extrude_surface, pin_radius_t, pin_extension_t):
    parts = [extrude_surface(v, f, thickness) for v, f in ribbons]
    parts += _pins(ribbons, thickness, pin_radius_t, pin_extension_t)
    parts.append(cap_shell(ring0_radius, cap["z"], thickness))
    for part in parts:
        if part.volume < 0:
            part.invert()
    mesh = merge_needles(booleans.repair(booleans.union(parts)))
    tri = mesh.triangles
    longest = np.max([np.linalg.norm(tri[:, 1] - tri[:, 0], axis=1), np.linalg.norm(tri[:, 2] - tri[:, 1], axis=1),
                      np.linalg.norm(tri[:, 0] - tri[:, 2], axis=1)], axis=0)
    thinnest = float((2 * mesh.area_faces / longest).min())
    if thinnest < MIN_TRIANGLE_HEIGHT_IN:
        raise RuntimeError(f"lattice canopy still has a sliver triangle (height {thinnest:.1e} in)")
    crossings = len(self_intersections(mesh))
    if mesh.body_count != 1 or not mesh.is_watertight or crossings:
        raise RuntimeError(f"lattice canopy not clean: {mesh.body_count} bodies, watertight {mesh.is_watertight}, "
                           f"{crossings} self-intersecting triangle pairs")
    return mesh


def build_lattice_canopy(strips, cap, ring0_radius, thickness, extrude_surface, pin_radius_t=0.9, pin_extension_t=0.3):
    """Returns the fused canopy (trimesh, inches). ``strips`` end with the legacy centre-cap fan, which is replaced by a bell-shaped cap.

    The requested pin size is tried first (almost flush: pins stick out 0.4 sheet thicknesses at most); if a sliver survives the clean-up, the pins are
    made slightly larger (still under 1.5 thicknesses) and the build is repeated. A canopy that no variant makes clean raises RuntimeError.
    """

    if not np.allclose(strips[-1][0][0], 0.0):
        raise RuntimeError("expected the centre cap fan as the last strip")
    ribbons = strips[:-1]
    last = None
    for radius_t, extension_t in ((pin_radius_t, pin_extension_t), (1.0, 0.5), (1.2, 1.0)):
        try:
            return _attempt(ribbons, cap, ring0_radius, thickness, extrude_surface, radius_t, extension_t)
        except RuntimeError as exc:
            last = exc
    raise last
