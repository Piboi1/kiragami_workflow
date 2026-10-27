"""Local pre-check of a welded canopy for the defect that makes SimScale's mesher fail.

SimScale reports "self intersection in the mesh" for designs where two neighbouring ribbon slabs overlap by a
sliver (a pair overlap volume of 1e-8 .. 1e-7 in^3, against about 1e-5 for a normal overlap). Measured on 5 designs:

    baseline        smallest pair overlap 1.2e-5 in^3   meshed
    batch design 03 smallest pair overlap 4.9e-6        meshed (Cd 0.78)
    batch design 04 smallest pair overlap 1.7e-7        mesh failed
    batch design 01 smallest pair overlap 5.4e-8        mesh failed
    batch design 02 smallest pair overlap 1.6e-8        mesh failed

so a design is rejected when its smallest pair overlap is below MIN_PAIR_OVERLAP_IN3 (1e-6). The check takes about a
second. It needs manifold3d. The threshold is calibrated on only 5 designs; re-check it as more results come in.
"""

import numpy as np

from . import booleans

MIN_PAIR_OVERLAP_IN3 = 1e-6


def pair_overlap_volumes(mesh):
    """Volumes (in^3) of the overlaps between every pair of overlapping welded pieces of ``mesh``."""

    pieces = []
    for piece in mesh.split(only_watertight=False):
        piece = piece.copy()
        if piece.volume < 0:
            piece.invert()
        pieces.append(piece)
    bounds = np.array([p.bounds for p in pieces])
    manifolds = [booleans.to_manifold(p) for p in pieces]
    volumes = []
    for a in range(len(pieces)):
        lo, hi = bounds[a]
        hits = np.where(np.all(bounds[a + 1:, 0] < hi, axis=1) & np.all(bounds[a + 1:, 1] > lo, axis=1))[0] + a + 1
        for b in hits:
            v = (manifolds[a] ^ manifolds[b]).volume()
            if v > 1e-12:
                volumes.append(v)
    return np.array(volumes)


def screen_canopy(mesh, min_overlap=MIN_PAIR_OVERLAP_IN3):
    """Returns (ok, stats). ok is False when a sliver overlap below ``min_overlap`` exists."""

    vols = pair_overlap_volumes(mesh)
    smallest = float(vols.min()) if len(vols) else float("inf")
    stats = {"pairs": int(len(vols)), "min_overlap_in3": smallest, "slivers": int((vols < min_overlap).sum())}
    return smallest >= min_overlap, stats
