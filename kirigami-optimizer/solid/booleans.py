"""Double-precision mesh booleans with manifold3d.

trimesh's built-in Manifold engine converts every mesh to 32-bit floats,
which moves vertices by ~5e-7 in at canopy scale. That is about SimScale's
CAD modelling tolerance (1e-5 mm), so the rounding left faces of the
canopy micro-clashing and the STEP import was rejected ("bad_face_face").
These helpers keep everything in float64 end to end.
"""

import numpy as np
import trimesh


def to_manifold(mesh):
    import manifold3d

    m = manifold3d.Manifold(manifold3d.Mesh64(
        vert_properties=np.ascontiguousarray(mesh.vertices, dtype=np.float64),
        tri_verts=np.ascontiguousarray(mesh.faces, dtype=np.uint64),
    ))
    if m.status() != manifold3d.Error.NoError:
        raise ValueError(f"mesh is not a valid manifold volume: {m.status()}")
    return m


def from_manifold(m):
    out = m.to_mesh64()
    return trimesh.Trimesh(vertices=np.asarray(out.vert_properties)[:, :3].astype(np.float64),
                           faces=np.asarray(out.tri_verts).astype(np.int64), process=False)


def union(meshes):
    import manifold3d

    return from_manifold(manifold3d.Manifold.batch_boolean([to_manifold(m) for m in meshes],
                                                           manifold3d.OpType.Add))


def difference(a, b):
    return from_manifold(to_manifold(a) - to_manifold(b))


def simplify(mesh, tolerance):
    """Collapses edges/features smaller than ``tolerance`` (mesh units)."""

    return from_manifold(to_manifold(mesh).simplify(tolerance))


def repair(mesh):
    """Welds coincident vertices and drops pairs of coincident (back-to-back)
    triangles, i.e. zero-thickness internal fins the boolean can leave behind.

    Such a fin has two triangles on the same three points; once the points
    are welded (as every STL reader does) they are an edge shared by four
    faces, the file is no longer watertight and SimScale's automatic face
    split fails. Returns the repaired mesh, or ``mesh`` unchanged when the
    repair would not leave a watertight solid.
    """

    welded = mesh.copy()
    welded.merge_vertices()
    key = np.sort(welded.faces, axis=1)
    _, inverse, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    keep = counts[inverse.ravel()] == 1
    if keep.all() and welded.is_watertight == mesh.is_watertight and len(welded.vertices) == len(mesh.vertices):
        return mesh
    fixed = trimesh.Trimesh(welded.vertices, welded.faces[keep], process=False)
    fixed.remove_unreferenced_vertices()
    if fixed.is_watertight and fixed.body_count == mesh.body_count and abs(fixed.volume - mesh.volume) < 1e-3 * abs(mesh.volume):
        return fixed
    return mesh


def collapse_short_edges(mesh, min_edge, rounds=4, min_altitude=0.0):
    """Merges the endpoints of every edge shorter than ``min_edge`` (mesh units), then drops the triangles that degenerate.

    A boolean of near-coincident faces leaves needle triangles with edges of 1e-7 in; STEP importers reject those edges
    ("fault-edge-invalid"). With ``min_altitude`` the shortest edge of every sliver (a triangle whose smallest height is below
    it, however long its edges) is merged too; the mesher refuses faces thinner than its CAD tolerance.
    A merge that would push a triangle through another is undone for that cluster only (the rest still merges), so the result has
    no new crossing triangles. Returns the cleaned mesh, or ``mesh`` unchanged if the result is not one watertight body.
    """

    from solid.selfcheck import self_intersections

    cur = mesh
    for _ in range(rounds):
        short = cur.edges_unique[cur.edges_unique_length < min_edge]
        if min_altitude > 0:
            tri = cur.triangles
            lengths = np.stack([np.linalg.norm(tri[:, 1] - tri[:, 0], axis=1), np.linalg.norm(tri[:, 2] - tri[:, 1], axis=1),
                                np.linalg.norm(tri[:, 0] - tri[:, 2], axis=1)], axis=1)
            sliver = np.nonzero(2 * cur.area_faces / lengths.max(axis=1) < min_altitude)[0]
            pairs = np.array([[(0, 1), (1, 2), (2, 0)][k] for k in lengths[sliver].argmin(axis=1)]).reshape(-1, 2)
            extra = cur.faces[sliver[:, None], pairs] if len(sliver) else np.zeros((0, 2), dtype=int)
            short = np.vstack([short, extra]) if len(short) else extra
        if not len(short):
            break

        edges = short
        for _attempt in range(8):
            parent = np.arange(len(cur.vertices))

            def find(a):
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            for a, b in edges:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
            roots = np.array([find(i) for i in range(len(parent))])
            counts = np.bincount(roots, minlength=len(parent))
            centres = np.column_stack([np.bincount(roots, weights=cur.vertices[:, k], minlength=len(parent)) for k in range(3)])
            centres /= np.maximum(counts, 1)[:, None]
            faces = roots[cur.faces]
            ok = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
            raw = trimesh.Trimesh(centres, faces[ok], process=False)
            crossings = self_intersections(raw)
            if not crossings:
                break
            kept = np.nonzero(ok)[0]
            bad_faces = kept[np.unique(np.array(crossings).ravel())]
            blocked = {int(r) for r in roots[cur.faces[bad_faces].ravel()] if counts[r] > 1}
            edges = np.array([e for e in edges if int(roots[e[0]]) not in blocked])
            if not len(edges):
                return cur
        else:
            return cur
        out = raw.copy()
        out.remove_unreferenced_vertices()
        out = repair(out)
        if not (out.is_watertight and out.body_count == mesh.body_count and abs(out.volume - mesh.volume) < 5e-3 * abs(mesh.volume)):
            return cur
        cur = out
    return cur
