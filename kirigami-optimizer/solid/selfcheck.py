"""Local triangle self-intersection finder (the defect SimScale's importer/mesher rejects).

Candidate pairs come from an AABB tree; pairs sharing a vertex are skipped; the rest get an exact segment-vs-triangle test.
"""

import numpy as np
import trimesh


def _seg_tri(p, q, tri, eps=1e-12):
    """Does segment p-q cross the interior of triangle tri (Moller-Trumbore on the segment)?"""

    d = q - p
    e1, e2 = tri[1] - tri[0], tri[2] - tri[0]
    h = np.cross(d, e2)
    a = e1 @ h
    if abs(a) < eps:
        return False
    f = 1.0 / a
    s = p - tri[0]
    u = f * (s @ h)
    if u < 1e-9 or u > 1 - 1e-9:
        return False
    qv = np.cross(s, e1)
    v = f * (d @ qv)
    if v < 1e-9 or u + v > 1 - 1e-9:
        return False
    t = f * (e2 @ qv)
    return 1e-9 < t < 1 - 1e-9


def tri_tri(ta, tb):
    for i in range(3):
        if _seg_tri(ta[i], ta[(i + 1) % 3], tb) or _seg_tri(tb[i], tb[(i + 1) % 3], ta):
            return True
    return False


def self_intersections(mesh, limit=None):
    """List of (face_i, face_j) pairs that properly intersect and share no vertex."""

    import rtree

    tris = mesh.triangles
    faces = mesh.faces
    lo, hi = tris.min(axis=1), tris.max(axis=1)
    idx = rtree.index.Index(((i, (*lo[i], *hi[i]), None) for i in range(len(tris))), properties=rtree.index.Property(dimension=3))
    out = []
    for i in range(len(tris)):
        vi = set(faces[i])
        for j in idx.intersection((*lo[i], *hi[i])):
            if j <= i or vi & set(faces[j]):
                continue
            if tri_tri(tris[i], tris[j]):
                out.append((i, int(j)))
                if limit and len(out) >= limit:
                    return out
    return out


def near_contacts(mesh, tol, region=None):
    """Non-adjacent triangle pairs closer than tol (vertex-to-triangle distance, both ways).

    Returns a list of (distance, face_i, face_j), smallest first. region(points)->bool mask limits the faces examined.
    """

    import rtree
    from trimesh.triangles import closest_point

    tris, faces = mesh.triangles, mesh.faces
    lo, hi = tris.min(axis=1) - tol, tris.max(axis=1) + tol
    idx = rtree.index.Index(((i, (*lo[i], *hi[i]), None) for i in range(len(tris))), properties=rtree.index.Property(dimension=3))
    keep = np.ones(len(tris), bool) if region is None else region(mesh.triangles_center)
    out = []
    for i in np.nonzero(keep)[0]:
        vi = set(faces[i])
        for j in idx.intersection((*lo[i], *hi[i])):
            if j <= i or vi & set(faces[j]):
                continue
            d = min(np.linalg.norm(closest_point(tris[j:j + 1].repeat(3, 0), tris[i]) - tris[i], axis=1).min(),
                    np.linalg.norm(closest_point(tris[i:i + 1].repeat(3, 0), tris[j]) - tris[j], axis=1).min())
            if d < tol:
                out.append((float(d), int(i), int(j)))
    return sorted(out)
