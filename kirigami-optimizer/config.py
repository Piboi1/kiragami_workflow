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
    # Deployed canopy depth (inches). None = predict it per design with
    # DEPTH_MODEL below; a number fixes it for every design.
    "VERTICAL_DROP": None,
    "KERF_FACTOR": 0.85,
    "SHEET_THICKNESS": 0.015,
    # The user's own script output (welded legacy strips, flush hinges): the canopy of Run 11, which
    # SimScale imports as a 948-face solid. The old "union" canopy fails SimScale's import/meshing.
    "EXTRUDE_METHOD": "legacy",
    "WELD_STRIPS": 1,
    "BRIDGE_EXTRA": 0.0,
    "JOINT_OVERLAP": 0.0,
    "MIN_CAP_RADIUS": 0.05,
    "SIMPLIFY_TOLERANCE": 1e-4,   # inches; removes boolean slivers (see solid/canopy.py)
    # flow domain, in multiples of PARACHUTE_DIAMETER (matches Kiragami_CFD box)
    "DOMAIN_LATERAL_HALF_WIDTH_D": 2.583,
    "DOMAIN_UPSTREAM_D": 3.113,
    "DOMAIN_DOWNSTREAM_D": 7.0,
    # Thin support line (inches) from the canopy rim down to the outlet, so
    # SimScale's sewing yields ONE fluid body (see solid/flow_domain.py).
    # Its faces are walls but are excluded from the force coefficients.
    "STING_RADIUS": 0.04,
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
    # 27-32 rings = 9.3-11.1 in deep. Every steady solve that converged was 9.0-10.8 in deep; the 7.9 in (23 rings) and
    # 12.2 in (35 rings) designs ran away exponentially (see HANDOFF.md), so the search is restricted to this band.
    {"name": "NUM_RINGS", "type": "int", "low": 27, "high": 32},
    # 4-8 cuts. With 9 or more cuts the inner rings are so crowded that neighbouring slabs overlap in slivers SimScale's mesher
    # rejects (0 of 18 test designs with 10-12 cuts passed the screen; see solid/screen.py and HANDOFF.md).
    {"name": "CUTS_PER_RING", "type": "int", "low": 4, "high": 8},
    {"name": "RING_SPACING_EXPONENT", "type": "real", "low": 0.6, "high": 1.5},
    {"name": "RING_OVERLAP_FRACTION", "type": "real", "low": 0.2, "high": 1.0},
    {"name": "INNER_RADIUS", "type": "real", "low": 0.5, "high": 1.25},
]

# Deployed-depth model (solid/depth_model.py), used when VERTICAL_DROP is None.
# Fitted to the hanging-depth measurements in DATA_FILE; add a row per
# measured parachute and refit happens automatically.
DEPTH_MODEL = {
    "DATA_FILE": ROOT / "data" / "depth_measurements.csv",
    # Chosen by leave-one-out on the 4 current measurements (all 8 cuts per
    # ring): depth = c * (rings - 1), LOO RMSE 1.27 in. Add e.g.
    # "cuts_per_ring" once measurements with other cut counts exist.
    "FEATURES": ["n_bands"],
    "INTERCEPT": False,
    # Predictions are clamped to this range (inches).
    "CLAMP_IN": (4.0, 14.0),
}

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
        # Measured depth of this exact chute (and the Run 11 geometry), so
        # the validation run is not affected by depth-model error.
        "VERTICAL_DROP": 10.62,
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
    "NEW_PROJECT_NAME": "Kiragami_Optimizer",  # matches the project created in the web app
    # Geometry import. The flow-domain STL has one 'solid' block per
    # boundary; SimScale imports each as a separate sheet body, so sewing
    # must join them into one fluid body (see `main.py probe-import`).
    # "STEP" (default): the flow domain is uploaded as one CAD solid with the
    # canopy as an internal void and the six box walls named + coloured.
    # "STL": multi-solid STL + sewing + sting (kept for reference; SimScale's
    # sewing did not produce a single body with it).
    # "MANUAL_FLOW_VOLUME": canopy-only faceted STL is imported and a person
    # makes SimScale's CAD-mode "External flow volume" in the web app (the
    # pipeline prints the steps, waits for the new geometry, then continues).
    "GEOMETRY_FORMAT": "MANUAL_FLOW_VOLUME",
    "MANUAL_WAIT_S": 6 * 3600,      # how long to wait for the manual step
    "MANUAL_POLL_S": 20,
    # Import the canopy as one named STL block per welded piece (705 faces for the baseline). SimScale's
    # automatic 30-degree facet split fails on many designs (whole canopy = 1 face); this never does.
    "CANOPY_PRESPLIT": True,
    "AUTO_FLOW_SIMPLIFY_TOL": 2e-3,   # inches; union canopy cleanup for the sting-STL route (smaller values fail to import)
    "IMPORT_SEWING": True,   # STL only; needs FIXED_PARAMS["STING_RADIUS"] > 0
    "IMPORT_IMPROVE": True,
    "TURBULENCE_MODEL": "KOMEGASST",
    "END_TIME_S": 500,              # steady-state iterations
    "DELTA_T_S": 1,
    "WRITE_INTERVAL": 10,
    "MAX_RUN_TIME_S": 20000,
    "NON_ORTHOGONAL_CORRECTORS": 2,
    # Solver stabilisers, off by default (the validated Run 11 numerics). `--robust-numerics` turns them on for a run:
    # start from a potential-flow solution and lower the pressure / velocity relaxation (None = SimScale default).
    # First-order bounded upwind for the turbulence transport (k, omega) instead of the unlimited second-order
    # linearUpwindV default (SimScale's API only allows linear / linearUpwindV for the default scheme, but accepts
    # upwind per quantity). More diffusive: it shifts Cd, so only compare Cds computed with the same setting.
    "UPWIND_TURBULENCE_SCHEME": False,
    # Cancel a runaway solve early (the drag history of a running job is polled every 90 s); see results.runaway_reason.
    "RUNAWAY_GUARD": True,
    "POTENTIAL_FOAM_INIT": False,
    "RELAX_PRESSURE": None,
    "RELAX_VELOCITY": None,
    "MESH_FINENESS": 4.5,           # Simmetrix automatic sizing, 0-10
    "MESH_BOUNDARY_LAYERS": True,
    "MESH_PHYSICS_BASED": True,
    "MESH_HEX_CORE": True,
    # The manual Kiragami_CFD mesh (Run 11, 1.58M cells) used 0.000646 in; via the API an unset
    # value arrives as 0, which gave a coarser mesh (1.26M cells) that diverged on the same geometry.
    "MESH_SMALL_FEATURE_TOLERANCE_MM": 0.016413,
    "MESH_MAX_RUN_TIME_S": 18000,   # SimScale-side meshing limit
    # Watchdogs: the job is CANCELLED on SimScale (not just abandoned) when
    # any of these trips. Your manual meshes took 3-5 min / 0.2-0.7 CPU h;
    # run 4's mesh sat at 10% for 35 min and billed 9.42 CPU h.
    "MESH_TIMEOUT_S": 1800,
    "MESH_MAX_CORE_HOURS": 2.0,
    "STALL_TIMEOUT_S": 900,         # progress unchanged this long while RUNNING
    # Per-design CPU-hour cap (mesh + simulation). Checked against SimScale's
    # estimate before starting, AND enforced live: a simulation is cancelled
    # once mesh + run hours pass it. Your manual runs used 0.8-3.0 CPUh per
    # simulation plus 0.2-0.7 per mesh. None disables it.
    "MAX_CORE_HOURS_PER_RUN": 6.0,
    "POLL_SECONDS": 30,
    # Cd objective = mean over this trailing fraction of iterations.
    "AVERAGING_FRACTION": 0.1,
}

# ------------------------------------------------------------------
# Google Sheets
# ------------------------------------------------------------------
SHEETS = {
    "SERVICE_ACCOUNT_FILE": os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(ROOT / "credentials" / "service_account.json")),
    # Alternative to the file: the key JSON's full text in one env var
    # (handy where only environment variables can be set). Takes precedence.
    "SERVICE_ACCOUNT_JSON": os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
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
    # Stop the optimization once this many CPU hours (mesh + simulation,
    # as billed by SimScale) have been used. The organization quota is
    # 1000 h; ~1-3.7 CPUh per design means 300 designs could use all of it.
    # None disables the check.
    "CORE_HOUR_BUDGET": 800.0,
    # Objective value (Cd) reported to the GP when a run fails or the
    # design is geometrically infeasible (see solid.canopy.check_feasible).
    "FAILURE_CD": 0.0,
    # prepare-batch rejects designs whose welded canopy has sliver overlaps between ribbon slabs (SimScale's
    # mesher then fails with "self intersection"); see solid/screen.py for the calibration.
    "SCREEN_SLIVERS": True,
    "MIN_PAIR_OVERLAP_IN3": 1e-6,
}


# ------------------------------------------------------------------
# Onshape (free plan: public documents, ~2,500 API requests per year)
# ------------------------------------------------------------------
ONSHAPE = {
    "DOCUMENT_NAME": "kirigami_optimizer",   # one document, overwritten per design
    "ALLOW_FAULTY_PARTS": True,               # Onshape STL import option (the web UI default may differ)
    "REQUEST_CAP": 2000,                     # soft stop, counted in state/onshape_usage.json
    # Canopy as the manual workflow feeds Onshape: overlapping slabs, flush hinges, no boolean union.
    "CANOPY_OVERRIDES": {"EXTRUDE_METHOD": "legacy", "BRIDGE_EXTRA": 0.0, "JOINT_OVERLAP": 0.0, "WELD_STRIPS": 1},
}
