import json
import math
from pathlib import Path

import ezdxf


# ============================================================
# KIRIGAMI PARACHUTE GENERATOR
# ============================================================
#
# Units: inches
#
# This program creates concentric circular arc cuts and exports
# them as a DXF file.
#
# IMPORTANT VARIABLES TO EXPERIMENT WITH:
#
# PARACHUTE_DIAMETER
# RIM_WIDTH
# INNER_RADIUS
# NUM_RINGS
# CUTS_PER_RING
# MIN_CUT_LENGTH
# MAX_CUT_LENGTH
# PARABOLIC_EXPONENT
# RING_SPACING_MODE
#
# ============================================================


# ============================================================
# OUTPUT
# ============================================================

OUTPUT_FILE = "kirigami_Q(0.75)_OL(2.5-15)_IG(20-6)_R24.dxf"

# Every message sent to the terminal is also retained so it can be
# saved beside the DXF after generation.
TERMINAL_OUTPUT_LINES = []


def report_print(message=""):
    """Print a message and retain it for the permanent text report."""

    text = str(message)
    print(text)
    TERMINAL_OUTPUT_LINES.append(text)


# ============================================================
# PARACHUTE SIZE
# ============================================================

PARACHUTE_DIAMETER = 10.0      # inches
OUTER_RADIUS = PARACHUTE_DIAMETER / 2

RIM_WIDTH = .250                # solid uncut outer rim

# Radius where the first cuts begin
INNER_RADIUS = 0.75


# ============================================================
# RING SETTINGS
# ============================================================

NUM_RINGS = 24

# "linear" = equal radial spacing
# "parabolic" = spacing changes according to a curve
RING_SPACING_MODE = "parabolic"

# Controls how strongly spacing changes if using parabolic mode
RING_SPACING_EXPONENT = 0.75


# ============================================================
# CUT SETTINGS
# ============================================================

# Same number of cuts on every ring
CUTS_PER_RING = 8

# Arc length near center
MIN_CUT_LENGTH = 0.05          # inches

# Arc length near outer edge
MAX_CUT_LENGTH = 2.95           # inches

# Controls the parabolic relationship
#
# 1.0 = linear
# 2.0 = quadratic/parabolic
# >2  = stronger increase toward outside
# <2  = more gradual increase
#
PARABOLIC_EXPONENT = 1.75

# ============================================================
# INTER-RING OVERLAP
# ============================================================

# Forces cuts on alternating rings to overlap angularly.
# This overlap is what allows the kirigami sheet to expand
# significantly out of plane.

ENFORCE_RING_OVERLAP = True

# Desired angular overlap between cuts on neighboring rings after
# the reinforced inner region.
RING_OVERLAP_DEGREES = 15.0

# Use less overlap on the first few rings so their cuts are shorter and
# more material remains near the center. The overlap ramps smoothly
# from this value to RING_OVERLAP_DEGREES across these rings.
INNER_RING_OVERLAP_DEGREES = 2.5
INNER_RING_COUNT = 5
# ============================================================
# ANGULAR GAP BETWEEN CUTS
# ============================================================

# Minimum empty angular gap between adjacent cuts
# Measured in degrees
MIN_GAP_DEGREES = 6

# The first few rings need wider bridges because their smaller radius
# makes a normal angular gap physically very narrow. This is a hard
# minimum: cut length and overlap requests cannot make these gaps
# smaller. The gap ramps from INNER_GAP_DEGREES to MIN_GAP_DEGREES.
INNER_GAP_DEGREES = 20.0
INNER_GAP_RING_COUNT = 5


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def normalized_radius(radius, min_radius, max_radius):
    """
    Converts radius to a value between 0 and 1.
    """

    if max_radius == min_radius:
        return 0

    value = (radius - min_radius) / (max_radius - min_radius)

    return max(0, min(1, value))


def get_ring_radius(index):
    """
    Returns the radius for a given ring.

    Linear mode:
        Equal radial spacing.

    Parabolic mode:
        Ring locations follow a power curve.
    """

    max_cut_radius = OUTER_RADIUS - RIM_WIDTH

    if NUM_RINGS == 1:
        return INNER_RADIUS

    t = index / (NUM_RINGS - 1)

    if RING_SPACING_MODE.lower() == "parabolic":
        t = t ** RING_SPACING_EXPONENT

    radius = INNER_RADIUS + (
        max_cut_radius - INNER_RADIUS
    ) * t

    return radius


def get_cut_length(radius):
    """
    Returns cut length using a parabolic relationship
    based on distance from the center.

    At INNER_RADIUS:
        MIN_CUT_LENGTH

    At OUTER cut radius:
        MAX_CUT_LENGTH
    """

    max_cut_radius = OUTER_RADIUS - RIM_WIDTH

    t = normalized_radius(
        radius,
        INNER_RADIUS,
        max_cut_radius
    )

    # Parabolic / power relationship
    curved_t = t ** PARABOLIC_EXPONENT

    length = (
        MIN_CUT_LENGTH
        + (MAX_CUT_LENGTH - MIN_CUT_LENGTH)
        * curved_t
    )

    return length


def arc_length_to_angle(radius, arc_length):
    """
    Converts arc length into angular length.

    arc_length = radius * angle

    Angle is returned in degrees.
    """

    if radius <= 0:
        return 0

    angle_radians = arc_length / radius

    return math.degrees(angle_radians)


def get_ring_overlap(ring_index):
    """
    Returns the requested overlap for a ring.

    The first INNER_RING_COUNT rings transition from the smaller inner
    overlap to the normal overlap used by the remaining rings.
    """

    if INNER_RING_COUNT <= 1 or ring_index >= INNER_RING_COUNT - 1:
        return RING_OVERLAP_DEGREES

    t = ring_index / (INNER_RING_COUNT - 1)
    return (
        INNER_RING_OVERLAP_DEGREES
        + (RING_OVERLAP_DEGREES - INNER_RING_OVERLAP_DEGREES) * t
    )


def get_ring_gap(ring_index):
    """
    Returns the minimum uncut angular gap for a ring.

    Inner rings start with a wider bridge and transition smoothly to
    the normal minimum gap. Keeping this separate from overlap makes
    the reinforced center a guaranteed constraint instead of a side
    effect of the requested cut length.
    """

    if INNER_GAP_RING_COUNT <= 1 or ring_index >= INNER_GAP_RING_COUNT - 1:
        return MIN_GAP_DEGREES

    t = ring_index / (INNER_GAP_RING_COUNT - 1)
    return (
        INNER_GAP_DEGREES
        + (MIN_GAP_DEGREES - INNER_GAP_DEGREES) * t
    )


# ============================================================
# CREATE DXF
# ============================================================

doc = ezdxf.new("R2010")

# Set document units to inches
doc.header["$INSUNITS"] = 1

msp = doc.modelspace()


# ============================================================
# LAYERS
# ============================================================

doc.layers.add("CUTS", color=1)
doc.layers.add("OUTLINE", color=3)


# ============================================================
# OUTER CIRCLE
# ============================================================

msp.add_circle(
    center=(0, 0),
    radius=OUTER_RADIUS,
    dxfattribs={"layer": "OUTLINE"}
)


# ============================================================
# GENERATE KIRIGAMI CUTS
# ============================================================

max_cut_radius = OUTER_RADIUS - RIM_WIDTH
ring_results = []

for ring_index in range(NUM_RINGS):

    # Get radius and desired arc length for this ring.
    radius = get_ring_radius(ring_index)
    cut_length = get_cut_length(radius)

    # Calculate the cut angle normally from physical arc length.
    requested_cut_angle = arc_length_to_angle(radius, cut_length)
    cut_angle = requested_cut_angle

    # Angular space assigned to each repeated cut.
    segment_angle = 360 / CUTS_PER_RING
    neighbor_offset = segment_angle / 2

    # Use smaller overlaps and wider gaps for the reinforced inner rings.
    requested_overlap = get_ring_overlap(ring_index)
    minimum_ring_gap = get_ring_gap(ring_index)

    # Minimum cut angle needed to create the requested inter-ring overlap.
    required_cut_angle = neighbor_offset + requested_overlap

    # Largest safe angle: leaves this ring's required bridge uncut.
    max_allowed_angle = segment_angle - minimum_ring_gap
    maximum_possible_overlap = max_allowed_angle - neighbor_offset

    if ENFORCE_RING_OVERLAP:
        cut_angle = max(cut_angle, required_cut_angle)

    # FAILSAFE: clamp any unsafe request so same-ring cuts cannot touch.
    cut_angle = min(cut_angle, max_allowed_angle)
    actual_overlap = cut_angle - neighbor_offset
    actual_gap = segment_angle - cut_angle
    actual_cut_length = radius * math.radians(cut_angle)
    actual_gap_length = radius * math.radians(actual_gap)
    overlap_was_clamped = (
        ENFORCE_RING_OVERLAP and required_cut_angle > max_allowed_angle
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

    report_print(f"Ring {ring_index + 1}:")
    report_print(f"  Requested overlap: {requested_overlap:.2f} degrees")
    report_print(f"  Required cut angle: {required_cut_angle:.2f} degrees")
    report_print(f"  Maximum allowed cut angle: {max_allowed_angle:.2f} degrees")
    report_print(f"  Maximum possible overlap: {maximum_possible_overlap:.2f} degrees")
    report_print(f"  Actual cut angle used: {cut_angle:.2f} degrees")
    report_print(f"  Actual overlap achieved: {actual_overlap:.2f} degrees")
    report_print(f"  Minimum gap required: {minimum_ring_gap:.2f} degrees")
    report_print(f"  Actual gap achieved: {actual_gap:.2f} degrees")
    report_print(f"  Actual gap width: {actual_gap_length:.4f} inches")

    if overlap_was_clamped:
        report_print("  WARNING:")
        report_print(
            f"  Requested overlap of {requested_overlap:.2f} degrees "
            f"exceeds the maximum possible overlap of "
            f"{maximum_possible_overlap:.2f} degrees."
        )
        report_print(
            f"  Cut angle was automatically clamped to {cut_angle:.2f} degrees."
        )
        report_print(f"  Actual overlap achieved: {actual_overlap:.2f} degrees.")

    # Center each cut inside its segment
    offset = (segment_angle - cut_angle) / 2

    # Alternate angular offset between rings
    if ring_index % 2 == 0:
        ring_rotation = 0
    else:
        ring_rotation = segment_angle / 2

    # Create cuts
    for cut_index in range(CUTS_PER_RING):

        segment_start = cut_index * segment_angle + ring_rotation
        start_angle = segment_start + offset
        end_angle = start_angle + cut_angle

        # Add circular arc
        msp.add_arc(
            center=(0, 0),
            radius=radius,
            start_angle=start_angle,
            end_angle=end_angle,
            dxfattribs={"layer": "CUTS"}
        )


# ============================================================
# SAVE DXF
# ============================================================

doc.saveas(OUTPUT_FILE)

report_print("Kirigami DXF created successfully!")
report_print(f"File: {OUTPUT_FILE}")
report_print()
report_print("Parameters:")
report_print(f"Diameter: {PARACHUTE_DIAMETER} in")
report_print(f"Rim width: {RIM_WIDTH} in")
report_print(f"Rings: {NUM_RINGS}")
report_print(f"Cuts per ring: {CUTS_PER_RING}")
report_print(f"Ring spacing: {RING_SPACING_MODE}")
report_print(f"Min cut length: {MIN_CUT_LENGTH} in")
report_print(f"Max cut length: {MAX_CUT_LENGTH} in")
report_print(f"Parabolic exponent: {PARABOLIC_EXPONENT}")
report_print(
    f"Inner gap transition: {INNER_GAP_DEGREES:g} to "
    f"{MIN_GAP_DEGREES:g} degrees across {INNER_GAP_RING_COUNT} rings"
)

output_path = Path(OUTPUT_FILE)
text_report_path = output_path.with_suffix(".report.txt")
json_report_path = output_path.with_suffix(".report.json")

report_print(f"Terminal report: {text_report_path}")
report_print(f"Geometry metadata: {json_report_path}")

generation_metadata = {
    "schema_version": 1,
    "report_type": "exact_generator_metadata",
    "source_program": Path(__file__).name,
    "output_file": str(output_path),
    "units": "inches",
    "parameters": {
        "PARACHUTE_DIAMETER": PARACHUTE_DIAMETER,
        "OUTER_RADIUS": OUTER_RADIUS,
        "RIM_WIDTH": RIM_WIDTH,
        "INNER_RADIUS": INNER_RADIUS,
        "NUM_RINGS": NUM_RINGS,
        "RING_SPACING_MODE": RING_SPACING_MODE,
        "RING_SPACING_EXPONENT": RING_SPACING_EXPONENT,
        "CUTS_PER_RING": CUTS_PER_RING,
        "MIN_CUT_LENGTH": MIN_CUT_LENGTH,
        "MAX_CUT_LENGTH": MAX_CUT_LENGTH,
        "PARABOLIC_EXPONENT": PARABOLIC_EXPONENT,
        "ENFORCE_RING_OVERLAP": ENFORCE_RING_OVERLAP,
        "RING_OVERLAP_DEGREES": RING_OVERLAP_DEGREES,
        "INNER_RING_OVERLAP_DEGREES": INNER_RING_OVERLAP_DEGREES,
        "INNER_RING_COUNT": INNER_RING_COUNT,
        "MIN_GAP_DEGREES": MIN_GAP_DEGREES,
        "INNER_GAP_DEGREES": INNER_GAP_DEGREES,
        "INNER_GAP_RING_COUNT": INNER_GAP_RING_COUNT,
    },
    "rings": ring_results,
}

text_report_path.write_text(
    "\n".join(TERMINAL_OUTPUT_LINES) + "\n",
    encoding="utf-8",
)
json_report_path.write_text(
    json.dumps(generation_metadata, indent=2) + "\n",
    encoding="utf-8",
)
