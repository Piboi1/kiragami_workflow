"""Semi-manual flow volume: the one CAD step the SimScale API cannot do.

The canopy is imported through the API as a faceted STL (a few hundred faces,
like the manual Kiragami_CFD setup). A person then runs SimScale's CAD-mode
"External flow volume" on it in the web app, which creates a *new* geometry
in the project. Its faces carry fixed labels ("Max Z@Flow region", ...), so
everything after that step (boundary conditions, mesh, run, results) is
automatic.
"""

M_PER_IN = 0.0254

# Face labels of a CAD-mode flow volume (lower-cased, as in the API mappings).
FLOW_REGION = "flow region"
WALL_LABELS = {
    "max z@flow region": "inlet",       # flow travels in -Z
    "min z@flow region": "outlet",
    "min x@flow region": "side_xmin",
    "max x@flow region": "side_xmax",
    "min y@flow region": "side_ymin",
    "max y@flow region": "side_ymax",
}


def is_flow_volume(face_labels):
    """True if the faces (an iterable of label sets) include a CAD flow volume's walls."""

    seen = set()
    for labels in face_labels:
        seen |= labels & WALL_LABELS.keys()
    return len(seen) == len(WALL_LABELS)


def box_summary(bounds_in):
    """Box corner coordinates in inches, metres and millimetres."""

    lo, hi = bounds_in
    return {
        "in": (list(lo), list(hi)),
        "m": ([round(v * M_PER_IN, 6) for v in lo], [round(v * M_PER_IN, 6) for v in hi]),
        "mm": ([round(v * M_PER_IN * 1000, 3) for v in lo], [round(v * M_PER_IN * 1000, 3) for v in hi]),
    }


def instructions(design_id, canopy_name, bounds_in, project_id=None):
    """The checklist shown to the person doing the manual step."""

    box = box_summary(bounds_in)
    (xl, yl, zl), (xh, yh, zh) = box["in"]
    (xlm, ylm, zlm), (xhm, yhm, zhm) = box["m"]
    link = f"https://www.simscale.com/workbench/?pid={project_id}" if project_id else "the SimScale workbench"
    return f"""MANUAL STEP for {design_id}  (about 2 minutes)

1. Open {link} -> project Kiragami_Optimizer -> Geometries.
2. Open the geometry named "{canopy_name}" in CAD mode.
3. Add an "External flow volume" with a CUSTOM box:
       X: {xl:+.3f} .. {xh:+.3f} in    ({xlm:+.4f} .. {xhm:+.4f} m)
       Y: {yl:+.3f} .. {yh:+.3f} in    ({ylm:+.4f} .. {yhm:+.4f} m)
       Z: {zl:+.3f} .. {zh:+.3f} in    ({zlm:+.4f} .. {zhm:+.4f} m)
   (same orientation as the Kiragami_CFD runs: Max Z is the inlet.)
4. Use "Delete bodies" to remove the canopy solid, leaving only the Flow region.
5. Save/exit the CAD mode so the new geometry is created in the project.
   Do not start a mesh or simulation; the pipeline does that.

The pipeline polls the project, finds the new geometry by its
"Max Z@Flow region" ... "Min Y@Flow region" faces, and carries on.
"""
