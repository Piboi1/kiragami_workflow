"""Kirigami parachute cut-pattern generator (refactor of legacy/kirigami_generator.py).

Units: inches, degrees.

The ring math is unchanged from the original script; the module-level globals
became a params dict so the pipeline can call ``generate_pattern(params)`` many
times in one process. Parameter names keep the original UPPERCASE spelling so
they match the ``parameters`` block of the existing ``*.report.json`` files.
"""

import json
import math
from pathlib import Path

# Defaults are the values hard-coded in the original script.
DEFAULT_PARAMS = {
    "PARACHUTE_DIAMETER": 10.0,
    "RIM_WIDTH": 0.25,
    "INNER_RADIUS": 0.75,
    "NUM_RINGS": 24,
    "RING_SPACING_MODE": "parabolic",
    "RING_SPACING_EXPONENT": 0.75,
    "CUTS_PER_RING": 8,
    "MIN_CUT_LENGTH": 0.05,
    "MAX_CUT_LENGTH": 2.95,
    "PARABOLIC_EXPONENT": 1.75,
    "ENFORCE_RING_OVERLAP": True,
    "RING_OVERLAP_DEGREES": 15.0,
    "INNER_RING_OVERLAP_DEGREES": 2.5,
    "INNER_RING_COUNT": 5,
    "MIN_GAP_DEGREES": 6.0,
    "INNER_GAP_DEGREES": 20.0,
    "INNER_GAP_RING_COUNT": 5,
}

# Optional convenience parameters that are converted into generator
# parameters by resolve_params(). They exist so the optimizer can search a
# space with no dead zones: an overlap given in degrees is silently clamped
# whenever it exceeds what the cut count allows, whereas a fraction of the
# available overlap is always meaningful.
#
#   RING_OVERLAP_FRACTION  in [0, 1]
#       RING_OVERLAP_DEGREES = f * (180 / CUTS_PER_RING - MIN_GAP_DEGREES)
#   INNER_OVERLAP_RATIO    > 0
#       INNER_RING_OVERLAP_DEGREES = ratio * RING_OVERLAP_DEGREES
DERIVED_PARAM_NAMES = ("RING_OVERLAP_FRACTION", "INNER_OVERLAP_RATIO")


def resolve_params(params=None):
    """Merge ``params`` over the defaults and expand the derived parameters."""

    p = dict(DEFAULT_PARAMS)
    p.update(params or {})

    p["NUM_RINGS"] = int(round(p["NUM_RINGS"]))
    p["CUTS_PER_RING"] = int(round(p["CUTS_PER_RING"]))
    p["INNER_RING_COUNT"] = int(round(p["INNER_RING_COUNT"]))
    p["INNER_GAP_RING_COUNT"] = int(round(p["INNER_GAP_RING_COUNT"]))

    if p.get("RING_OVERLAP_FRACTION") is not None:
        available = 180.0 / p["CUTS_PER_RING"] - p["MIN_GAP_DEGREES"]
        p["RING_OVERLAP_DEGREES"] = max(0.0, available) * float(p["RING_OVERLAP_FRACTION"])

    if p.get("INNER_OVERLAP_RATIO") is not None:
        p["INNER_RING_OVERLAP_DEGREES"] = float(p["INNER_OVERLAP_RATIO"]) * p["RING_OVERLAP_DEGREES"]

    p["OUTER_RADIUS"] = p["PARACHUTE_DIAMETER"] / 2
    return p


# ============================================================
# HELPER FUNCTIONS (same math as the original script)
# ============================================================

def normalized_radius(radius, min_radius, max_radius):
    """Converts radius to a value between 0 and 1."""

    if max_radius == min_radius:
        return 0

    value = (radius - min_radius) / (max_radius - min_radius)

    return max(0, min(1, value))


def get_ring_radius(p, index):
    """Radius of ring ``index``: linear or power-curve ('parabolic') spacing."""

    max_cut_radius = p["OUTER_RADIUS"] - p["RIM_WIDTH"]

    if p["NUM_RINGS"] == 1:
        return p["INNER_RADIUS"]

    t = index / (p["NUM_RINGS"] - 1)

    if p["RING_SPACING_MODE"].lower() == "parabolic":
        t = t ** p["RING_SPACING_EXPONENT"]

    return p["INNER_RADIUS"] + (max_cut_radius - p["INNER_RADIUS"]) * t


def get_cut_length(p, radius):
    """Cut arc length: MIN_CUT_LENGTH at INNER_RADIUS rising to MAX_CUT_LENGTH."""

    max_cut_radius = p["OUTER_RADIUS"] - p["RIM_WIDTH"]

    t = normalized_radius(radius, p["INNER_RADIUS"], max_cut_radius)
    curved_t = t ** p["PARABOLIC_EXPONENT"]

    return p["MIN_CUT_LENGTH"] + (p["MAX_CUT_LENGTH"] - p["MIN_CUT_LENGTH"]) * curved_t


def arc_length_to_angle(radius, arc_length):
    """Converts arc length into angular length in degrees."""

    if radius <= 0:
        return 0

    return math.degrees(arc_length / radius)


def get_ring_overlap(p, ring_index):
    """Requested overlap, ramping from the inner value to the normal value."""

    count = p["INNER_RING_COUNT"]
    if count <= 1 or ring_index >= count - 1:
        return p["RING_OVERLAP_DEGREES"]

    t = ring_index / (count - 1)
    return (
        p["INNER_RING_OVERLAP_DEGREES"]
        + (p["RING_OVERLAP_DEGREES"] - p["INNER_RING_OVERLAP_DEGREES"]) * t
    )


def get_ring_gap(p, ring_index):
    """Minimum uncut angular gap, ramping from the inner value to MIN_GAP_DEGREES."""

    count = p["INNER_GAP_RING_COUNT"]
    if count <= 1 or ring_index >= count - 1:
        return p["MIN_GAP_DEGREES"]

    t = ring_index / (count - 1)
    return (
        p["INNER_GAP_DEGREES"]
        + (p["MIN_GAP_DEGREES"] - p["INNER_GAP_DEGREES"]) * t
    )


# ============================================================
# RING GEOMETRY
# ============================================================

def compute_rings(p):
    """Returns (ring_results, arcs) for resolved params ``p``.

    ring_results matches the ``rings`` list in the original report JSON;
    arcs is a list of (radius, start_angle, end_angle) for the DXF.
    """

    ring_results = []
    arcs = []

    for ring_index in range(p["NUM_RINGS"]):

        radius = get_ring_radius(p, ring_index)
        cut_length = get_cut_length(p, radius)

        requested_cut_angle = arc_length_to_angle(radius, cut_length)
        cut_angle = requested_cut_angle

        segment_angle = 360 / p["CUTS_PER_RING"]
        neighbor_offset = segment_angle / 2

        requested_overlap = get_ring_overlap(p, ring_index)
        minimum_ring_gap = get_ring_gap(p, ring_index)

        # Minimum cut angle needed to create the requested inter-ring overlap.
        required_cut_angle = neighbor_offset + requested_overlap

        # Largest safe angle: leaves this ring's required bridge uncut.
        max_allowed_angle = segment_angle - minimum_ring_gap
        maximum_possible_overlap = max_allowed_angle - neighbor_offset

        if p["ENFORCE_RING_OVERLAP"]:
            cut_angle = max(cut_angle, required_cut_angle)

        # FAILSAFE: clamp any unsafe request so same-ring cuts cannot touch.
        cut_angle = min(cut_angle, max_allowed_angle)
        actual_overlap = cut_angle - neighbor_offset
        actual_gap = segment_angle - cut_angle
        actual_cut_length = radius * math.radians(cut_angle)
        actual_gap_length = radius * math.radians(actual_gap)
        overlap_was_clamped = (
            p["ENFORCE_RING_OVERLAP"] and required_cut_angle > max_allowed_angle
        )

        ring_results.append({
            "ring_number": ring_index + 1,
            "radius_inches": radius,
            "requested_cut_length_inches": cut_length,
            "requested_cut_angle_degrees": requested_cut_angle,
            "requested_overlap_degrees": requested_overlap,
            "required_cut_angle_degrees": required_cut_angle,
            "maximum_allowed_cut_angle_degrees": max_allowed_angle,
            "maximum_possible_overlap_degrees": maximum_possible_overlap,
            "actual_cut_angle_degrees": cut_angle,
            "actual_cut_length_inches": actual_cut_length,
            "actual_overlap_degrees": actual_overlap,
            "minimum_gap_degrees": minimum_ring_gap,
            "actual_gap_degrees": actual_gap,
            "actual_gap_length_inches": actual_gap_length,
            "overlap_was_clamped": overlap_was_clamped,
        })

        # Center each cut inside its segment; alternate rings are rotated
        # by half a segment.
        offset = (segment_angle - cut_angle) / 2
        ring_rotation = 0 if ring_index % 2 == 0 else segment_angle / 2

        for cut_index in range(p["CUTS_PER_RING"]):
            start_angle = cut_index * segment_angle + ring_rotation + offset
            arcs.append((radius, start_angle, start_angle + cut_angle))

    return ring_results, arcs


def _format_report(p, ring_results, dxf_path):
    lines = []
    for ring in ring_results:
        lines.append(f"Ring {ring['ring_number']}:")
        lines.append(f"  Requested overlap: {ring['requested_overlap_degrees']:.2f} degrees")
        lines.append(f"  Required cut angle: {ring['required_cut_angle_degrees']:.2f} degrees")
        lines.append(f"  Maximum allowed cut angle: {ring['maximum_allowed_cut_angle_degrees']:.2f} degrees")
        lines.append(f"  Maximum possible overlap: {ring['maximum_possible_overlap_degrees']:.2f} degrees")
        lines.append(f"  Actual cut angle used: {ring['actual_cut_angle_degrees']:.2f} degrees")
        lines.append(f"  Actual overlap achieved: {ring['actual_overlap_degrees']:.2f} degrees")
        lines.append(f"  Minimum gap required: {ring['minimum_gap_degrees']:.2f} degrees")
        lines.append(f"  Actual gap achieved: {ring['actual_gap_degrees']:.2f} degrees")
        lines.append(f"  Actual gap width: {ring['actual_gap_length_inches']:.4f} inches")
        if ring["overlap_was_clamped"]:
            lines.append("  WARNING:")
            lines.append(
                f"  Requested overlap of {ring['requested_overlap_degrees']:.2f} degrees "
                f"exceeds the maximum possible overlap of "
                f"{ring['maximum_possible_overlap_degrees']:.2f} degrees."
            )
            lines.append(
                f"  Cut angle was automatically clamped to {ring['actual_cut_angle_degrees']:.2f} degrees."
            )
            lines.append(f"  Actual overlap achieved: {ring['actual_overlap_degrees']:.2f} degrees.")

    lines.append("Kirigami DXF created successfully!" if dxf_path else "Kirigami pattern computed.")
    if dxf_path:
        lines.append(f"File: {dxf_path}")
    lines.append("")
    lines.append("Parameters:")
    lines.append(f"Diameter: {p['PARACHUTE_DIAMETER']} in")
    lines.append(f"Rim width: {p['RIM_WIDTH']} in")
    lines.append(f"Rings: {p['NUM_RINGS']}")
    lines.append(f"Cuts per ring: {p['CUTS_PER_RING']}")
    lines.append(f"Ring spacing: {p['RING_SPACING_MODE']}")
    lines.append(f"Min cut length: {p['MIN_CUT_LENGTH']} in")
    lines.append(f"Max cut length: {p['MAX_CUT_LENGTH']} in")
    lines.append(f"Parabolic exponent: {p['PARABOLIC_EXPONENT']}")
    lines.append(
        f"Inner gap transition: {p['INNER_GAP_DEGREES']:g} to "
        f"{p['MIN_GAP_DEGREES']:g} degrees across {p['INNER_GAP_RING_COUNT']} rings"
    )
    return lines


def write_dxf(arcs, p, path):
    """Writes the cut pattern (outline circle + arcs) to a DXF in inches."""

    import ezdxf

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 1  # inches
    msp = doc.modelspace()
    doc.layers.add("CUTS", color=1)
    doc.layers.add("OUTLINE", color=3)

    msp.add_circle(center=(0, 0), radius=p["OUTER_RADIUS"], dxfattribs={"layer": "OUTLINE"})
    for radius, start_angle, end_angle in arcs:
        msp.add_arc(
            center=(0, 0),
            radius=radius,
            start_angle=start_angle,
            end_angle=end_angle,
            dxfattribs={"layer": "CUTS"},
        )
    doc.saveas(str(path))


def generate_pattern(params=None, output_dir=None, name="kirigami", write_files=True, verbose=False):
    """Generates a kirigami cut pattern.

    Args:
        params: dict of generator parameters (see DEFAULT_PARAMS); missing
            keys fall back to the defaults. RING_OVERLAP_FRACTION and
            INNER_OVERLAP_RATIO are expanded by resolve_params().
        output_dir: where to write <name>.dxf, <name>.report.txt and
            <name>.report.json. Ignored when write_files is False.
        name: file stem for the outputs.
        write_files: set False to compute geometry only (no ezdxf needed).
        verbose: print the per-ring report like the original script did.

    Returns:
        The same dict that is written to <name>.report.json: schema_version,
        units, parameters (fully resolved), rings (per-ring geometry), plus
        a ``files`` entry with the paths that were written.
    """

    p = resolve_params(params)
    ring_results, arcs = compute_rings(p)

    files = {}
    dxf_path = None
    if write_files:
        out = Path(output_dir or ".")
        out.mkdir(parents=True, exist_ok=True)
        dxf_path = out / f"{name}.dxf"
        write_dxf(arcs, p, dxf_path)
        files = {
            "dxf": str(dxf_path),
            "report_txt": str(dxf_path.with_suffix(".report.txt")),
            "report_json": str(dxf_path.with_suffix(".report.json")),
        }

    lines = _format_report(p, ring_results, dxf_path)
    if verbose:
        print("\n".join(lines))

    # Same key order as the original report: OUTER_RADIUS follows the diameter.
    parameters = {"PARACHUTE_DIAMETER": p["PARACHUTE_DIAMETER"], "OUTER_RADIUS": p["OUTER_RADIUS"]}
    parameters.update({k: p[k] for k in DEFAULT_PARAMS})
    for k in DERIVED_PARAM_NAMES:
        if p.get(k) is not None:
            parameters[k] = p[k]

    metadata = {
        "schema_version": 1,
        "report_type": "exact_generator_metadata",
        "source_program": "generator/pattern.py",
        "output_file": str(dxf_path) if dxf_path else None,
        "units": "inches",
        "parameters": parameters,
        "rings": ring_results,
        "summary": {
            "ring_count": len(ring_results),
            "clamped_ring_count": sum(1 for r in ring_results if r["overlap_was_clamped"]),
            "max_cut_radius_inches": p["OUTER_RADIUS"] - p["RIM_WIDTH"],
        },
        "files": files,
    }

    if write_files:
        Path(files["report_txt"]).write_text("\n".join(lines) + "\n", encoding="utf-8")
        Path(files["report_json"]).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    return metadata


def load_report(path):
    """Loads a *.report.json written by this module or by the original script."""

    return json.loads(Path(path).read_text(encoding="utf-8"))
