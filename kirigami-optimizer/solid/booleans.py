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
