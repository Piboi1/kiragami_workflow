"""All pipeline settings in one place.

Secrets (API keys, sheet IDs, service-account path) are NOT here -- they come
from environment variables / a .env file. See .env.example.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional; plain env vars also work
    load_dotenv = None

ROOT = Path(__file__).resolve().parent
if load_dotenv:
    load_dotenv(ROOT / ".env")

# ------------------------------------------------------------------
# Paths
# ------------------------------------------------------------------
RUNS_DIR = ROOT / "runs"              # one folder per design: DXF, reports, STLs, raw CFD CSV
STATE_DIR = ROOT / "state"            # optimizer ledger (source of truth for resume)
LEDGER_PATH = STATE_DIR / "ledger.json"
LOCAL_RESULTS_CSV = RUNS_DIR / "results.csv"  # always-written mirror of the Google Sheet

# ------------------------------------------------------------------
# Design parameters
# ------------------------------------------------------------------
# Everything the generator and solid builder take. Parameters listed in
# SEARCH_SPACE override these; everything else is held fixed.
FIXED_PARAMS = {
    # generator (inches / degrees) -- defaults of the original generator
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
    # Inner overlap follows the searched outer overlap (2.5/15 in the original).
    "INNER_OVERLAP_RATIO": 2.5 / 15.0,
    # 3D solid (inches)
    "VERTICAL_DROP": 10.62,       # measured deployed depth of r30_open_s115 under 20 g
    "KERF_FACTOR": 0.85,
    "SHEET_THICKNESS": 0.015,
    "EXTRUDE_METHOD": "union",
    "JOINT_OVERLAP": 0.03,
    "MIN_CAP_RADIUS": 0.05,
    # flow domain, in multiples of PARACHUTE_DIAMETER (matches Kiragami_CFD box)
    "DOMAIN_LATERAL_HALF_WIDTH_D": 2.583,
    "DOMAIN_UPSTREAM_D": 3.113,
    "DOMAIN_DOWNSTREAM_D": 7.0,
}

# The 4-6 parameters Bayesian optimization searches over.
# type: "int" or "real". Bounds are inclusive.
#
# Why these: the 3D model depends on ring radii (NUM_RINGS,
# RING_SPACING_EXPONENT, INNER_RADIUS), cut count and bridge gap per ring.
# With ENFORCE_RING_OVERLAP the gap is set by the overlap, so the cut-length
# curve (MIN/MAX_CUT_LENGTH, PARABOLIC_EXPONENT) never wins and is left out.
# Overlap is searched as a fraction of what the cut count allows so there
# are no dead zones where the generator would clamp it.
SEARCH_SPACE = [
    {"name": "NUM_RINGS", "type": "int", "low": 16, "high": 36},
    {"name": "CUTS_PER_RING", "type": "int", "low": 4, "high": 12},
    {"name": "RING_SPACING_EXPONENT", "type": "real", "low": 0.6, "high": 1.5},
    {"name": "RING_OVERLAP_FRACTION", "type": "real", "low": 0.2, "high": 1.0},
    {"name": "INNER_RADIUS", "type": "real", "low": 0.5, "high": 1.25},
]

# Named designs for `main.py single --preset NAME`.
PRESETS = {
    # The design in Kiragami_CFD Run 11 (Cd 1.083 at Uz = -6.148 m/s).
    # Reproduces the ring table hard-coded in legacy/kiragami3dcreator.py.
    "baseline": {
        "NUM_RINGS": 30,
        "CUTS_PER_RING": 8,
        "RING_SPACING_EXPONENT": 1.15,
        "INNER_RADIUS": 0.75,
        "RING_OVERLAP_DEGREES": 16.0,
        "INNER_RING_OVERLAP_DEGREES": 14.0,
        "INNER_RING_COUNT": 6,
        "INNER_GAP_DEGREES": 6.0,
        "RING_OVERLAP_FRACTION": None,
        "INNER_OVERLAP_RATIO": None,
    },
    # Generator defaults (kirigami_Q(0.75)_OL(2.5-15)_IG(20-6)_R24).
    "generator_default": {
        "RING_OVERLAP_FRACTION": None,
        "INNER_OVERLAP_RATIO": None,
    },
}

# ------------------------------------------------------------------
# Inlet velocity estimate
# ------------------------------------------------------------------
VELOCITY = {
    "AIR_DENSITY": 1.196,          # kg/m^3, SimScale Air (4.323e-5 lb/in^3)
    # --- CONFIRM THESE -------------------------------------------------
    "PAYLOAD_MASS_G": 20.0,        # "Vertical height prediction" sheet uses 20 g
    "SHEET_GSM": 75.0,             # canopy sheet areal density (printer paper = 75)
    "CANOPY_MASS_G": None,         # set to a weighed value to override SHEET_GSM
    # Best real drop test (slowest average descent in "geo testing").
    "DROP_TEST_SEED": {
        "name": "24-10 (24 rings, s1.15, overlap 10)",
        "drop_height_m": 4.937,
        "descent_time_s": 1.322,
        # Reference area of the physical canopy, same convention as CFD
        # (disk of the deployed canopy's max radius, ~4.88 in).
        "reference_area_in2": 75.0,
    },
    # Average speed (height/time) understates terminal velocity on a 4.9 m
    # drop; True solves the fall-from-rest equation for v_t instead.
    "DROP_TEST_CORRECT_FOR_ACCELERATION": True,
    "SEED_CD": None,               # set a number to bypass the drop-test calculation
    # -------------------------------------------------------------------
    "CD_REFINEMENT": "anchored",   # "anchored" or "direct" (see velocity/estimator.py)
    "KERNEL_LENGTH_SCALE": 0.2,    # similarity radius in the [0,1]-normalized search space
    "SEED_WEIGHT": 1.0,
    "UZ_CLAMP_MPS": (1.0, 20.0),
}

# ------------------------------------------------------------------
# SimScale setup (mirrors the manual Kiragami_CFD "Incompressible" sim)
# ------------------------------------------------------------------
SIMSCALE = {
    "API_URL": os.getenv("SIMSCALE_API_URL", "https://api.simscale.com"),
    "API_KEY": os.getenv("SIMSCALE_API_KEY", ""),
    # Leave empty to have the pipeline create a new project (its ID is printed
    # and saved to state/simscale_project.json so later runs reuse it).
    "PROJECT_ID": os.getenv("SIMSCALE_PROJECT_ID", ""),
    "NEW_PROJECT_NAME": "Kirigami_Optimizer",
    "TURBULENCE_MODEL": "KOMEGASST",
    "END_TIME_S": 500,              # steady-state iterations
    "DELTA_T_S": 1,
    "WRITE_INTERVAL": 10,
    "MAX_RUN_TIME_S": 20000,
    "NON_ORTHOGONAL_CORRECTORS": 2,
    "MESH_FINENESS": 4.5,           # Simmetrix automatic sizing, 0-10
    "MESH_BOUNDARY_LAYERS": True,
    "MESH_PHYSICS_BASED": True,
    "MESH_HEX_CORE": True,
    "MESH_MAX_RUN_TIME_S": 18000,
    # Abort a design if SimScale's estimate exceeds this many core hours
    # (None disables the check).
    "MAX_CORE_HOURS_PER_RUN": None,
    "POLL_SECONDS": 30,
    # Cd objective = mean over this trailing fraction of iterations.
    "AVERAGING_FRACTION": 0.1,
}

# ------------------------------------------------------------------
# Google Sheets
# ------------------------------------------------------------------
SHEETS = {
    "SERVICE_ACCOUNT_FILE": os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(ROOT / "credentials" / "service_account.json")),
    "SHEET_ID": os.getenv("GOOGLE_SHEET_ID", ""),
    "WORKSHEET": os.getenv("GOOGLE_WORKSHEET_NAME", "runs"),
}

# ------------------------------------------------------------------
# Optimizer
# ------------------------------------------------------------------
OPTIMIZER = {
    "N_CALLS": 150,                 # total designs, including ones already in the ledger
    "N_INITIAL_POINTS": 20,         # space-filling designs before the GP takes over
    "INITIAL_POINT_GENERATOR": "lhs",
    "ACQ_FUNC": "gp_hedge",
    "RANDOM_STATE": 42,
    # Objective value (Cd) reported to the GP when a run fails or the
    # design is geometrically infeasible (see solid.canopy.check_feasible).
    "FAILURE_CD": 0.0,
}
