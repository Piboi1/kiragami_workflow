"""Kirigami parachute Cd optimizer -- command line entry point.

    python main.py dry-run  [--preset baseline]     geometry + Uz only, no network
    python main.py check                            verify SimScale + Google Sheets credentials
    python main.py inspect-simscale [--project-name Kiragami_CFD]
                                                    dump the manual sim setup for comparison
    python main.py single   [--preset baseline] [--uz 6.148]
                                                    ONE full pipeline pass -> one sheet row
    python main.py optimize [--n-calls 150] [--n-initial 20]
                                                    Bayesian optimization (resumable)
    python main.py sync-sheet                       re-send rows that failed to reach the sheet
    python main.py status                           ledger summary / best design
    python main.py depth-model                      show the fitted canopy-depth model
    python main.py manual-step [--preset baseline] [--wait]
                                                    import the canopy, show the web-app flow-volume checklist,
                                                    verify the resulting geometry (not billed)
    python main.py reference-check                  run the pipeline's spec on the manual project's own geometry (billed)
    python main.py canopy-gate --name N [--simplify-tol 1e-3]
                                                    mesh the canopy alone until surface meshing passes/fails (tiny cost)
    python main.py onshape-roundtrip [--preset baseline] [--stl FILE]
                                                    STL -> Onshape import -> Part Studio STL export (free; no SimScale)
    python main.py probe-import [--variants step stl_sting stl_single stl_single_split]
                                                    test SimScale geometry import options (not billed)
"""

import argparse
import json
import sys
import time
from pathlib import Path

import config
from optimizer.state import Ledger
from pipeline import Context, design_params, evaluate_design, resume_pending, sync_sheet
from sheets import RunLogger
from velocity import VelocityEstimator


def _preset(name):
    if name not in config.PRESETS:
        sys.exit(f"unknown preset {name!r}; choose from {sorted(config.PRESETS)}")
    return config.PRESETS[name]


def _estimator():
    return VelocityEstimator(config.VELOCITY, config.FIXED_PARAMS["PARACHUTE_DIAMETER"])


def _simscale_client():
    from simscale.runner import SimScaleClient

    client = SimScaleClient(config.SIMSCALE)
    project_id = client.ensure_project(
        config.SIMSCALE["PROJECT_ID"], config.SIMSCALE["NEW_PROJECT_NAME"], config.STATE_DIR / "simscale_project.json"
    )
    return client, project_id


def _live_context():
    client, project_id = _simscale_client()
    logger = RunLogger(config.LOCAL_RESULTS_CSV, config.SHEETS)
    if logger.sheet is None:
        print(f"WARNING: Google Sheet not available ({logger.sheet_error}). "
              f"Rows go to {config.LOCAL_RESULTS_CSV}; run `python main.py sync-sheet` later.")
    ledger = Ledger(config.LEDGER_PATH)
    ledger.geometry_method = config.FIXED_PARAMS["EXTRUDE_METHOD"]  # results of another canopy construction do not train this one
    return Context(ledger=ledger, logger=logger, estimator=_estimator(), simscale=client, project_id=project_id)


def cmd_dry_run(args):
    ledger = Ledger(config.STATE_DIR / "dry_run_ledger.json")
    ctx = Context(ledger=ledger, logger=RunLogger(config.RUNS_DIR / "dry_run_results.csv", use_sheets=False),
                  estimator=_estimator(), runs_dir=config.RUNS_DIR / "dry_run")
    record = evaluate_design(ctx, design_params(_preset(args.preset)), mode="dry_run", uz_override=args.uz)
    print(json.dumps(record["row"], indent=2, default=str))
    print("\nFiles:", json.dumps(record.get("files", {}), indent=2))
    return 0 if record["status"] == "dry_run" else 1


def cmd_check(args):
    ok = True
    print("SimScale:")
    try:
        from simscale.runner import SimScaleClient

        client = SimScaleClient(config.SIMSCALE)
        cached = config.STATE_DIR / "simscale_project.json"
        project_id = config.SIMSCALE["PROJECT_ID"] or (
            json.loads(cached.read_text())["project_id"] if cached.exists() else "")
        for label, passed, detail in client.check_access(project_id, config.SIMSCALE["NEW_PROJECT_NAME"]):
            ok &= passed
            print(f"  {'OK  ' if passed else 'FAIL'} - {label}: {detail}")
    except Exception as exc:
        ok = False
        print(f"  FAIL - {type(exc).__name__}: {exc}")
    print("Google Sheets:")
    logger = RunLogger(config.LOCAL_RESULTS_CSV, config.SHEETS)
    if logger.sheet:
        print(f"  OK - writing to '{logger.sheet.describe()}'")
    else:
        ok = False
        print(f"  FAIL - {logger.sheet_error}")
    print("Velocity seed:")
    est = _estimator()
    print(f"  seed Cd = {est.seed_cd:.3f} ({est.seed_info}), mass = {est.mass_kg * 1000:.1f} g")
    return 0 if ok else 1


def cmd_inspect(args):
    client, _ = _simscale_client()
    project = client.find_project(args.project_name)
    if not project:
        sys.exit(f"no project named {args.project_name!r}")
    out = config.RUNS_DIR / "simscale_template"
    written = client.dump_project(project.project_id, out)
    print(f"Project {project.name}: {project.project_id}")
    for w in written:
        print("  ", w + ".json / .py")
    return 0


def cmd_single(args):
    if args.auto_flow:  # build the air volume locally (sting STL, facet split) instead of waiting for the web-app step
        config.SIMSCALE["GEOMETRY_FORMAT"] = "STL"
    if args.clean_step:  # clean canopy + B-rep STEP air volume built locally: no manual flow-volume step
        config.SIMSCALE["GEOMETRY_FORMAT"] = "STEP"
        config.SIMSCALE["STEP_CHECK"] = False
        config.SIMSCALE["IMPORT_IMPROVE"] = False
    ctx = _live_context()
    if ctx.ledger.pending:
        print("An interrupted design is pending; finishing it first.")
        resume_pending(ctx)
    params = design_params(_preset(args.preset))
    if args.from_design:  # the shape of an earlier design, e.g. to re-run it on clean geometry
        rec = next((r for r in json.loads(Path(config.LEDGER_PATH).read_text())["records"] if r["design_id"] == args.from_design), None)
        if rec is None:
            sys.exit(f"{args.from_design} is not in the ledger")
        params = dict(rec["params"])
    if args.clean_step:
        params["EXTRUDE_METHOD"] = "lattice"
    if args.simplify_tol is not None:
        params["SIMPLIFY_TOLERANCE"] = args.simplify_tol
    if args.user_style or args.auto_flow:  # same canopy as `manual-step --user-style` (reference area / diameter come from it)
        params.update(config.ONSHAPE["CANOPY_OVERRIDES"])
    for item in args.set or []:
        key, _, value = item.partition("=")
        try:
            params[key] = float(value)
        except ValueError:
            params[key] = value
    if args.max_core_hours is not None:  # this run only; the config default stays
        ctx.simscale.cfg["MAX_CORE_HOURS_PER_RUN"] = args.max_core_hours
        print(f"CPU-hour cap for this design (mesh + run, checked against SimScale's estimate and enforced live): "
              f"{args.max_core_hours:g}")
    if args.robust_numerics:
        ctx.simscale.cfg.update(ROBUST_NUMERICS)
        print(f"Robust solver numerics on: {ROBUST_NUMERICS}")
    if args.stable_scheme:
        ctx.simscale.cfg.update(STABLE_SCHEME)
        print("First-order upwind for the turbulence transport (k, omega): on")
    if args.mesh_fineness is not None:  # Simmetrix automatic sizing 0-10 (default config.SIMSCALE["MESH_FINENESS"])
        ctx.simscale.cfg["MESH_FINENESS"] = args.mesh_fineness
        print(f"Mesh fineness {args.mesh_fineness:g} (default {config.SIMSCALE['MESH_FINENESS']:g}): more cells, more CPU hours")
    if args.no_boundary_layers:
        ctx.simscale.cfg["MESH_BOUNDARY_LAYERS"] = False
        print("Automatic boundary layers: off")
    if args.iterations is not None:  # steady-state iterations (END_TIME_S with a 1 s step); the CPU cost grows in proportion
        ctx.simscale.cfg["END_TIME_S"] = args.iterations
        print(f"Solver iterations: {args.iterations} (default 500)")
    if args.skip_estimate_check:  # only the pre-run estimate gate of the solver; the live cap still cancels the run
        ctx.simscale.cfg["SKIP_ESTIMATE_GATE"] = True
        print("Pre-run solver estimate gate skipped; the cap is still enforced live during the run.")
    resume = None
    if args.from_manual:  # reuse the flow volume made by `manual-step --design-id ...`
        from pipeline import start_with_geometry

        state = json.loads((config.RUNS_DIR / "manual_step" / args.from_manual / "simscale_state.json").read_text())
        resume = start_with_geometry(ctx, params, state["geometry_id"])
        print(f"Using flow-volume geometry {state['geometry_id']} from manual-step {args.from_manual}")
    record = evaluate_design(ctx, params, mode="single", uz_override=args.uz, resume=resume)
    print(json.dumps(record["row"], indent=2, default=str))
    return 0 if record["status"] == "ok" else 1


ROBUST_NUMERICS = {"POTENTIAL_FOAM_INIT": True, "RELAX_PRESSURE": 0.2, "RELAX_VELOCITY": 0.5}
STABLE_SCHEME = {"UPWIND_TURBULENCE_SCHEME": True}
# Everything gentle at once: potential-flow start, low relaxation of p, U, k and omega, first-order upwind for k and omega (shifts Cd; a diagnostic, not a production setting).
GENTLE_NUMERICS = {**ROBUST_NUMERICS, **STABLE_SCHEME, "RELAX_TURBULENCE": 0.5}


def cmd_mesh_test(args):
    """Billed (small): does SimScale's mesher accept this design? Builds the air volume locally (STEP), imports it, starts only the mesher
    and stops at --pass-progress (a pass) or when it fails (free). Needs no manual flow-volume step."""

    from optimizer.meshrig import mesh_test

    records = json.loads(Path(config.LEDGER_PATH).read_text())["records"]
    results = []
    for design_id in args.design_id:
        rec = next((r for r in records if r["design_id"] == design_id), None)
        if rec is None:
            print(f"{design_id} is not in the ledger")
            continue
        params = dict(rec["params"])
        for item in args.set or []:
            key, _, value = item.partition("=")
            try:
                params[key] = float(value)
            except ValueError:
                params[key] = value
        res = mesh_test(params, f"{design_id}_{args.route}{args.tag}", route=args.route, cap=args.cap,
                        pass_progress=args.pass_progress, timeout=args.timeout, import_only=args.import_only,
                        improve=False if args.no_improve else None,
                        presplit=False if args.no_presplit else None, pieces=True if args.pieces else None)
        results.append(res)
        print(f"{design_id}: {res['result']:6s} cpu {res['cpu_hours']} h, {res['seconds']} s  {res.get('reason', '')[:200]}")
    return 0


def cmd_build_clean(args):
    """Free: builds a design with the lattice generator (own-normal ribbons, node pins, flat cap, needles merged) and checks it.

    Writes one watertight STL (and optionally the STEP air volume) and prints the checks: bodies, watertightness, crossing
    triangles, volume, shortest edge. Takes a ledger design or a JSON file of parameters.
    """

    import trimesh

    from pipeline import build_geometry
    from solid.selfcheck import self_intersections

    if args.params_json:
        params = json.loads(Path(args.params_json).read_text())
        name = Path(args.params_json).stem
    else:
        record = next((r for r in json.loads(Path(config.LEDGER_PATH).read_text())["records"] if r["design_id"] == args.design_id), None)
        if record is None:
            print(f"{args.design_id} is not in the ledger")
            return 1
        params, name = dict(record["params"]), args.design_id
    params["EXTRUDE_METHOD"] = args.method
    out = Path(args.out_dir or config.RUNS_DIR / "lattice" / name)
    out.mkdir(parents=True, exist_ok=True)
    config.SIMSCALE["GEOMETRY_FORMAT"] = "STEP" if args.step else "MANUAL_FLOW_VOLUME"
    config.SIMSCALE["STEP_CHECK"] = False
    _, info, _, paths = build_geometry(params, out, name)
    mesh = trimesh.load(paths["canopy_stl"], process=True)
    print(f"{name}: {len(mesh.faces)} triangles, bodies {mesh.body_count}, watertight {mesh.is_watertight}, volume {mesh.volume:.4f} in^3, "
          f"shortest edge {mesh.edges_unique_length.min():.1e} in, crossing triangle pairs {len(self_intersections(mesh))}")
    print(f"canopy STL: {paths['canopy_stl']}")
    if args.step:
        print(f"STEP air volume: {paths['domain_file']}")
    return 0


def cmd_export_print(args):
    """Free: a presentation / 3D-print model of a finished design: one fused solid, thicker ribbons, scaled to a chosen height.

    Not the simulated geometry (that one is 0.015 in thick and built from overlapping slabs); the layout of rings, cuts and
    ribbons is the same. Written in millimetres, rim on the build plate (z = 0), apex up.
    """

    import tempfile

    import trimesh

    from pipeline import build_geometry

    record = next((r for r in json.loads(Path(config.LEDGER_PATH).read_text())["records"]
                   if r["design_id"] == args.design_id), None)
    if record is None:
        print(f"{args.design_id} is not in the ledger")
        return 1
    params = dict(record["params"])
    params.update(SHEET_THICKNESS=args.thickness_in, EXTRUDE_METHOD="union", JOINT_OVERLAP=args.joint_overlap_in,
                  BRIDGE_EXTRA=args.bridge_extra, SIMPLIFY_TOLERANCE=1e-4)
    with tempfile.TemporaryDirectory() as tmp:
        _, info, _, paths = build_geometry(params, Path(tmp), f"{args.design_id}_print")
        mesh = trimesh.load(paths["canopy_stl"], force="mesh")
    scale = args.height_in * 25.4 / float(mesh.bounds[1][2] - mesh.bounds[0][2])  # model inches -> printed mm
    mesh.apply_scale(scale)
    mesh.apply_translation([0.0, 0.0, -mesh.bounds[0][2]])
    out = Path(args.out) if args.out else Path(__file__).parent / "exports" / f"{args.design_id}_print_{args.height_in:g}in_tall_mm.stl"
    out.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(str(out))
    ext = mesh.extents
    print(f"{out}\n  bodies {mesh.body_count}, watertight {mesh.is_watertight}, {len(mesh.faces)} triangles, volume {mesh.volume / 1000:.1f} cm^3\n"
          f"  size {ext[0]:.1f} x {ext[1]:.1f} x {ext[2]:.1f} mm ({ext[0] / 25.4:.2f} x {ext[1] / 25.4:.2f} x {ext[2] / 25.4:.2f} in), "
          f"sheet thickness {args.thickness_in * scale:.2f} mm, scale {scale / 25.4:.3f}")
    return 0 if mesh.is_watertight and mesh.body_count == 1 else 2


def cmd_prepare_batch(args):
    """Free: proposes K designs, imports their canopies and writes one checklist for the web-app step."""

    from optimizer.batch import prepare_batch

    ctx = _live_context()
    batch_id, designs, texts = prepare_batch(ctx, args.n, from_designs=args.from_design)
    out = config.RUNS_DIR / "batch" / batch_id
    print(f"\n{batch_id}: {len(designs)} design(s) ready. Checklist for all of them: {out / 'MANUAL_STEPS.txt'}\n")
    print(("\n" + "=" * 70 + "\n\n").join(texts))
    print(f"\nWhen every flow volume is saved: python main.py run-batch --batch {batch_id} "
          "--max-core-hours 5 --batch-core-hours N --skip-estimate-check   (billed: needs your approval)")
    return 0 if designs else 1


def cmd_run_batch(args):
    """Billed: runs every design of a batch whose flow volume has been saved, one after the other."""

    from optimizer.batch import run_batch

    ctx = _live_context()
    if args.robust_numerics:
        ctx.simscale.cfg.update(ROBUST_NUMERICS)
        print(f"Robust solver numerics on: {ROBUST_NUMERICS}")
    if args.stable_scheme:
        ctx.simscale.cfg.update(STABLE_SCHEME)
        print("First-order upwind for the turbulence transport (k, omega): on")
    if args.gentle:
        ctx.simscale.cfg.update(GENTLE_NUMERICS)
        print(f"Gentle solver numerics on: {GENTLE_NUMERICS}")
    if args.mesh_fineness is not None:  # Simmetrix automatic sizing 0-10 (default config.SIMSCALE["MESH_FINENESS"])
        ctx.simscale.cfg["MESH_FINENESS"] = args.mesh_fineness
        print(f"Mesh fineness {args.mesh_fineness:g} (default {config.SIMSCALE['MESH_FINENESS']:g}): more cells, more CPU hours")
    if args.no_boundary_layers:
        ctx.simscale.cfg["MESH_BOUNDARY_LAYERS"] = False
        print("Automatic boundary layers: off")
    if args.iterations is not None:  # steady-state iterations (END_TIME_S with a 1 s step); the CPU cost grows in proportion
        ctx.simscale.cfg["END_TIME_S"] = args.iterations
        print(f"Solver iterations: {args.iterations} (default 500)")
    batch = run_batch(ctx, args.batch, args.max_core_hours, args.batch_core_hours,
                      skip_estimate_check=args.skip_estimate_check, redo=tuple(args.redo or ()), uz=args.uz)
    for e in batch["designs"]:
        print(f"{e['name']}: {e.get('status', 'not run')} Cd={e.get('cd')}")
    return 0


def cmd_optimize(args):
    from optimizer.loop import run_optimization

    if args.clean_step:  # lattice canopy + local STEP air volume; only lattice-geometry results train the optimizer
        config.SIMSCALE["GEOMETRY_FORMAT"] = "STEP"
        config.SIMSCALE["STEP_CHECK"] = False
        config.SIMSCALE["IMPORT_IMPROVE"] = False
        config.FIXED_PARAMS["EXTRUDE_METHOD"] = "lattice"
    ctx = _live_context()
    if args.clean_step:
        ctx.ledger.geometry_method = "lattice"
    best = run_optimization(ctx, n_calls=args.n_calls, n_initial_points=args.n_initial)
    if best:
        print(f"Best design: {best['design_id']} Cd={best['cd']:.4f}")
        print(json.dumps(best["params"], indent=2))
    return 0


def cmd_sync_results(args):
    """Rebuilds the ranked `results` tab of the Google Sheet from the ledger (the raw `runs` tab is not touched)."""

    from sheets.results import refresh_results_tab

    logger = RunLogger(config.LOCAL_RESULTS_CSV, config.SHEETS)
    if logger.sheet is None:
        print(f"Google Sheet not available: {logger.sheet_error}")
        return 1
    n = refresh_results_tab(logger.sheet.spreadsheet, Ledger(config.LEDGER_PATH).records, config.VELOCITY["AIR_DENSITY"])
    print(f"results tab refreshed: {n} valid design(s) ranked")
    return 0


def cmd_sync(args):
    ctx = Context(ledger=Ledger(config.LEDGER_PATH), logger=RunLogger(config.LOCAL_RESULTS_CSV, config.SHEETS),
                  estimator=None)
    if ctx.logger.sheet is None:
        sys.exit(f"Google Sheet not available: {ctx.logger.sheet_error}")
    print(f"Synced {sync_sheet(ctx)} row(s).")
    return 0


# Geometry-import experiments (imports are not billed). Each builds the
# baseline flow domain in one form and reports what SimScale made of it.
IMPORT_VARIANTS = {
    # One CAD solid, canopy as a void, named walls (imports cleanly, but the
    # canopy arrives as ~22.5k CAD faces and meshing is too expensive).
    "step": {"format": "STEP", "sewing": False},
    # Multi-block STL with the sting joining canopy to outlet, sewn. The
    # canopy stays faceted geometry, like the manual setup (954 faces).
    "stl_sting": {"format": "STL", "sting": True, "sewing": True},
    # AP242 tessellated solid: 6 named wall faces + ONE faceted canopy face,
    # canopy as a void. Aims for STEP's single body + STL's low face count.
    "step_tess": {"format": "STEP", "tessellated": True, "sewing": False},
    # Whole fluid boundary as ONE STL block (box + canopy void), sewn.
    "stl_single": {"format": "STL", "single_block": True, "sewing": True},
    # As stl_single, with SimScale splitting facets into faces by angle.
    "stl_single_split": {"format": "STL", "single_block": True, "sewing": True, "facet_split": True},
}


def _probe_domain(variant, out):
    """Writes the baseline flow domain for an import variant; returns its path."""

    from generator import generate_pattern
    from solid import build_flow_domain, build_solid, write_named_stl
    from solid.canopy import DEFAULT_SOLID_PARAMS
    from solid.step_export import write_step, write_tessellated_step

    params = design_params(_preset("baseline"))
    report = generate_pattern(params, write_files=False)
    solid_params = {k: params.get(k, v) for k, v in DEFAULT_SOLID_PARAMS.items()}
    canopy, info = build_solid(report, solid_params)
    domain_params = dict(params)
    if not variant.get("sting"):
        domain_params["STING_RADIUS"] = 0.0
    fluid, labels, _ = build_flow_domain(canopy, report["parameters"]["PARACHUTE_DIAMETER"], domain_params,
                                         sting_anchor=info["sting_anchor_in"] if variant.get("sting") else None)
    if variant["format"] == "STEP":
        path = out / ("probe_domain_tess.step" if variant.get("tessellated") else "probe_domain.step")
        (write_tessellated_step if variant.get("tessellated") else write_step)(fluid, labels, path)
        return path
    if variant.get("single_block"):
        import numpy as np
        labels = np.full(len(labels), "canopy", dtype=object)  # one block for everything
    return Path(write_named_stl(fluid, labels, out / f"probe_domain_{'single' if variant.get('single_block') else 'named'}.stl"))


def cmd_probe_import(args):
    """Imports the baseline flow domain in one or more forms and reports the
    resulting bodies/faces. Geometry imports are not billed (no mesh/run)."""

    from simscale_sdk import ApiException

    from simscale.runner import REQUIRED_GROUPS, describe_api_error

    client, project_id = _simscale_client()
    out = config.RUNS_DIR / "probe_import"
    out.mkdir(parents=True, exist_ok=True)
    names = args.variants or [config.SIMSCALE["GEOMETRY_FORMAT"].lower()]
    ok_variants = []
    for name in names:
        variant = IMPORT_VARIANTS[name]
        print(f"\n=== import variant {name}: {variant} ===")
        try:
            path = _probe_domain(variant, out)
            _, import_id = client.start_import(project_id, f"probe_{name}", path, variant.get("sewing", False),
                                               True, variant["format"], variant.get("facet_split", False))
            geometry_id = client.wait_for_import(project_id, import_id)
            geo = client.describe_geometry(project_id, geometry_id, out / f"entities_{name}.json")
        except ApiException as exc:
            print(f"  FAILED: {describe_api_error(exc)}")
            continue
        except Exception as exc:
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            continue
        n_faces = sum(len(v) for v in geo["groups"].values()) + len(geo["unlabeled_faces"])
        print(f"  geometry {geometry_id}: {len(geo['bodies'])} body(ies), {n_faces} face(s) in total")
        for b in geo["bodies"]:
            print(f"    body {b['name']}: labels {b['labels'][:6]}")
        for g, members in geo["groups"].items():
            print(f"    {g:10s} {len(members):5d} face(s) on body {geo['group_bodies'][g]}")
        print(f"    unlabeled  {len(geo['unlabeled_faces']):5d} face(s)")
        good = len(geo["bodies"]) == 1 and all(geo["groups"][g] for g in REQUIRED_GROUPS)
        print("  USABLE: one body with every boundary named" if good else
              ("  ONE BODY, but boundaries are not named" if len(geo["bodies"]) == 1 else "  not usable as-is"))
        if good:
            ok_variants.append(name)
    print(f"\nMappings saved in {out}. Usable variants: {ok_variants or 'none'}")
    print("The probe geometries are named probe_* in the project and can be deleted there.")
    return 0 if ok_variants else 1


MESH_PROBE_VARIANTS = {
    # Your manual settings + ignore CAD features under 0.1 mm (the canopy
    # sheet is 0.38 mm thick, so it is still resolved).
    "A": {"MESH_SMALL_FEATURE_TOLERANCE_MM": 0.1},
    # A without boundary layers: tests whether layers on the thin sheet are the cost.
    "B": {"MESH_SMALL_FEATURE_TOLERANCE_MM": 0.1, "MESH_BOUNDARY_LAYERS": False},
    # Your manual Kiragami_CFD mesh settings, unchanged (use with --from-manual).
    "M": {},
}


def cmd_mesh_probe(args):
    """Meshes the baseline design ONLY (no solver) with a settings variant,
    under a hard CPU-hour cap; the mesh is cancelled on SimScale if it trips."""

    if args.auto_flow:
        config.SIMSCALE["GEOMETRY_FORMAT"] = "STL"

    from pipeline import build_geometry
    from simscale.runner import SimScaleClient

    sim_cfg = dict(config.SIMSCALE)
    sim_cfg.update(MESH_PROBE_VARIANTS[args.variant])
    sim_cfg.update({"MESH_MAX_CORE_HOURS": args.cap, "MESH_TIMEOUT_S": args.timeout,
                    "STALL_TIMEOUT_S": args.stall})
    client = SimScaleClient(sim_cfg)
    project_id = client.ensure_project(config.SIMSCALE["PROJECT_ID"], config.SIMSCALE["NEW_PROJECT_NAME"],
                                       config.STATE_DIR / "simscale_project.json")
    out = config.RUNS_DIR / "mesh_probe" / args.variant
    out.mkdir(parents=True, exist_ok=True)
    print(f"Mesh probe {args.variant}: {MESH_PROBE_VARIANTS[args.variant]}; cap {args.cap} CPU h, "
          f"timeout {args.timeout} s, stall {args.stall} s")
    params = design_params(_preset("baseline"))
    if args.auto_flow:
        params.update(config.ONSHAPE["CANOPY_OVERRIDES"])
    _, info, _, paths = build_geometry(params, out, f"meshprobe_{args.variant}")
    state = {}
    if args.from_manual:  # reuse the flow volume made by `manual-step` (skips import and the wait)
        manual_state = json.loads((config.RUNS_DIR / "manual_step" / args.from_manual / "simscale_state.json").read_text())
        state = {"geometry_id": manual_state["geometry_id"]}
        print(f"Using flow-volume geometry {state['geometry_id']} from manual-step {args.from_manual}")
    state_file = out / "simscale_state.json"
    started = time.time()
    summary = {"variant": args.variant, "settings": MESH_PROBE_VARIANTS[args.variant], "cap_cpu_h": args.cap}
    try:
        result = client.run_design(project_id, f"meshprobe_{args.variant}", paths["domain_file"], -6.148,
                                   info["reference_area_in2"], info["projected_diameter_in"], out, state,
                                   lambda s: state_file.write_text(json.dumps(s, indent=2)), mesh_only=True,
                                   manual={"bounds_in": info["domain_bounds_in"]})
        summary.update(status="FINISHED", **result)
    except Exception as exc:
        summary.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
    summary["mesh_core_hours"] = state.get("mesh_core_hours", summary.get("mesh_core_hours"))
    summary["wall_minutes"] = round((time.time() - started) / 60, 1)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary["status"] == "FINISHED" else 1


def cmd_manual_step(args):
    """Free: imports the canopy only, shows the web-app checklist for the flow
    volume, waits for it, and reports how its faces were identified. Never
    meshes or runs anything (nothing is billed)."""

    from pipeline import build_geometry
    from simscale import manual_flow
    from simscale.runner import REQUIRED_GROUPS, SimScaleError

    client, project_id = _simscale_client()
    design_id = args.design_id
    out = config.RUNS_DIR / "manual_step" / design_id
    out.mkdir(parents=True, exist_ok=True)
    state_file = out / "simscale_state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    params = design_params(_preset(args.preset))
    if args.simplify_tol is not None:
        params["SIMPLIFY_TOLERANCE"] = args.simplify_tol
    if args.user_style:  # the user's exact script output (welded legacy strips, flush hinges)
        params.update(config.ONSHAPE["CANOPY_OVERRIDES"])
    for item in args.set or []:  # e.g. --set BRIDGE_EXTRA=0 JOINT_OVERLAP=0
        key, _, value = item.partition("=")
        params[key] = float(value)
    _, info, _, paths = build_geometry(params, out, design_id)
    print(f"canopy: {info['triangle_count']} triangles, volume {info['volume_in3']:.4f} in^3, "
          f"watertight {info['is_watertight']}, bodies {info['body_count']}")
    cfg = dict(config.SIMSCALE, GEOMETRY_FORMAT="MANUAL_FLOW_VOLUME")
    save = lambda s: state_file.write_text(json.dumps(s, indent=2))  # noqa: E731
    if "geometry_id" not in state:
        if "canopy_geometry_id" not in state:
            state["geometries_before"] = [g.geometry_id for g in client.list_geometry_ids(project_id)]
            _, import_id = client.start_import(project_id, f"{design_id}_canopy", paths["domain_file"], True,
                                               cfg["IMPORT_IMPROVE"], "STL",
                                               facet_split=not cfg.get("CANOPY_PRESPLIT"))  # sewing -> solid
            state["canopy_geometry_id"] = client.wait_for_import(project_id, import_id)
            save(state)
            canopy = client.describe_geometry(project_id, state["canopy_geometry_id"])
            n_canopy = sum(len(v) for v in canopy["groups"].values()) + len(canopy["unlabeled_faces"])
            is_solid = bool(client._all_mappings(project_id, state["canopy_geometry_id"], "region"))
            print(f"canopy-only geometry {state['canopy_geometry_id']}: {n_canopy} face(s), "
                  f"{'SOLID volume' if is_solid else 'SHEET body (NOT a solid; External flow volume will fail)'}")
            if not is_solid:
                return 1
        # The web app's CAD edit replaces this geometry with a new one of the same
        # name, so it is only inspected right after import (above).
        text = manual_flow.instructions(design_id, f"{design_id}_canopy", info["domain_bounds_in"], project_id)
        (out / "MANUAL_STEP.txt").write_text(text)
        try:
            state["geometry_id"] = client.wait_for_flow_volume(
                project_id, set(state["geometries_before"]) | {state["canopy_geometry_id"]}, text,
                cfg["MANUAL_WAIT_S"] if args.wait else 0, cfg["MANUAL_POLL_S"])
        except SimScaleError:
            print(f"\nNo flow volume yet. Do the steps above (also in {out / 'MANUAL_STEP.txt'}), "
                  "then re-run this same command.")
            return 2
        save(state)
    groups, body = client.resolve_entities(project_id, state["geometry_id"], out / "simscale_entities.json")
    print(f"flow-volume geometry {state['geometry_id']}: fluid region {body}")
    for g in REQUIRED_GROUPS:
        print(f"  {g:10s} {len(groups[g]):5d} face(s)")
    print("OK: boundaries identified. `main.py single` will now run the same steps and continue to the mesh.")
    return 0


def cmd_onshape_roundtrip(args):
    """Free: sends the (legacy-style) canopy STL through Onshape's import and Part Studio STL export."""

    import numpy as np
    import trimesh
    from onshape import OnshapeClient
    from onshape.roundtrip import round_trip

    out = config.RUNS_DIR / "onshape" / args.name
    out.mkdir(parents=True, exist_ok=True)
    if args.exported_stl:  # already round-tripped: skip Onshape, only inspect/import the result
        res = {"file": args.exported_stl, "requests_used": OnshapeClient(config.STATE_DIR / "onshape_usage.json").requests_used()}
    else:
        stl = Path(args.stl) if args.stl else None
        if stl is None:
            from pipeline import build_geometry

            params = design_params(_preset(args.preset))
            params.update(config.ONSHAPE["CANOPY_OVERRIDES"])
            for item in args.set or []:
                key, _, value = item.partition("=")
                params[key] = float(value)
            _, info, _, paths = build_geometry(params, out, args.name)
            stl = Path(paths["canopy_stl"])
            print(f"canopy: {info['triangle_count']} triangles, {info['body_count']} bodies, volume {info['volume_in3']:.4f} in^3")
        client = OnshapeClient(config.STATE_DIR / "onshape_usage.json", request_cap=config.ONSHAPE["REQUEST_CAP"])
        res = round_trip(client, stl, out / f"{args.name}_onshape.stl", config.STATE_DIR / "onshape_doc.json",
                         doc_name=config.ONSHAPE["DOCUMENT_NAME"], allow_faulty=config.ONSHAPE["ALLOW_FAULTY_PARTS"])
    m = trimesh.load(res["file"])
    comps = trimesh.graph.connected_components(m.face_adjacency, nodes=np.arange(len(m.faces)))
    print(f"exported mesh: {len(m.faces)} triangles, watertight {m.is_watertight}, "
          f"{len(comps)} connected piece(s), volume {m.volume:.4f} in^3")
    print(f"Onshape requests used so far: {res['requests_used']} (cap {config.ONSHAPE['REQUEST_CAP']})")
    if args.simscale_import:  # geometry import only: free, no mesh or solver
        client_s, project_id = _simscale_client()
        _, import_id = client_s.start_import(project_id, f"{args.name}_onshape", res["file"], True,
                                             config.SIMSCALE["IMPORT_IMPROVE"], "STL", facet_split=True)
        geometry_id = client_s.wait_for_import(project_id, import_id)
        d = client_s.describe_geometry(project_id, geometry_id)
        n_faces = sum(len(v) for v in d["groups"].values()) + len(d["unlabeled_faces"])
        is_solid = bool(client_s._all_mappings(project_id, geometry_id, "region"))
        print(f"SimScale geometry {geometry_id}: {n_faces} face(s), {'SOLID volume' if is_solid else 'SHEET body'} "
              "(manual Onshape export: ~948 faces)")
    return 0


def cmd_canopy_gate(args):
    """Billed but tiny: imports the canopy ALONE, starts SimScale's mesher on it
    (no physics, no layers) and stops as soon as surface meshing has passed or
    failed. Tells whether SimScale accepts the canopy surface (the
    self-intersection error of mesh probe M) without a manual flow volume."""

    from pipeline import build_geometry
    from simscale.runner import SimScaleClient

    client = SimScaleClient(config.SIMSCALE)
    project_id = client.ensure_project(config.SIMSCALE["PROJECT_ID"], config.SIMSCALE["NEW_PROJECT_NAME"],
                                       config.STATE_DIR / "simscale_project.json")
    out = config.RUNS_DIR / "canopy_gate" / args.name
    out.mkdir(parents=True, exist_ok=True)
    if args.geometry_id:  # already in the project (e.g. the Onshape-repaired flow volume made in the web app)
        print(f"gate {args.name}: existing geometry {args.geometry_id}, cap {args.cap} CPU h, "
              f"timeout {args.timeout} s, pass at {args.pass_progress:.0%} progress")
        geometry_id = args.geometry_id
        return _canopy_gate_mesh(client, project_id, args, out, geometry_id, time.time())
    overrides = {"SIMPLIFY_TOLERANCE": args.simplify_tol}
    if args.joint_overlap is not None:
        overrides["JOINT_OVERLAP"] = args.joint_overlap
    params = design_params(_preset(args.preset))
    params.update(overrides)
    _, info, _, paths = build_geometry(params, out, args.name)
    print(f"gate {args.name}: canopy {info['triangle_count']} triangles, simplify {args.simplify_tol:g}, "
          f"cap {args.cap} CPU h, timeout {args.timeout} s, pass at {args.pass_progress:.0%} progress")
    _, import_id = client.start_import(project_id, f"{args.name}_gate", paths["canopy_stl"], True,
                                       config.SIMSCALE["IMPORT_IMPROVE"], "STL", facet_split=True)
    geometry_id = client.wait_for_import(project_id, import_id)
    if not client._all_mappings(project_id, geometry_id, "region"):
        print("RESULT: import is a sheet body, not a solid")
        return 1
    return _canopy_gate_mesh(client, project_id, args, out, geometry_id, time.time())


def _canopy_gate_mesh(client, project_id, args, out, geometry_id, started):
    from simscale_sdk import MeshOperation, SimmetrixMeshingFluid, AutomaticMeshSizingSimmetrix, AutomaticCurvature
    from simscale_sdk import AutomaticLayerOff, DimensionalTime
    from simscale.runner import _core_hours

    model = SimmetrixMeshingFluid(
        sizing=AutomaticMeshSizingSimmetrix(fineness=config.SIMSCALE["MESH_FINENESS"], curvature=AutomaticCurvature()),
        automatic_layer_settings=AutomaticLayerOff(), physics_based_meshing=False, hex_core=False,
        max_meshing_run_time=DimensionalTime(value=args.timeout, unit="s"))
    op = client.meshes.create_mesh_operation(project_id, MeshOperation(
        name=f"{args.name} gate", geometry_id=geometry_id, model=model))
    op_id = op.mesh_operation_id
    client.meshes.start_mesh_operation(project_id, op_id)
    run_started, result, reason = None, None, ""
    try:
        while True:
            cur = client.meshes.get_mesh_operation(project_id, op_id)
            hours = _core_hours(cur)
            if cur.status == "RUNNING" and run_started is None:
                run_started = time.time()  # queue time is not billed, so it does not count
            if cur.status == "FAILED":
                entries = client.meshes.get_mesh_operation_event_log(project_id, op_id).entries or []
                reason = " | ".join(e.message for e in entries)
                result = "FAIL"
                break
            if cur.status == "FINISHED" or (cur.status == "RUNNING" and (cur.progress or 0) >= args.pass_progress):
                result = "PASS"
                break
            if hours > args.cap or (run_started and time.time() - run_started > args.timeout):
                result, reason = "INCONCLUSIVE", f"stopped at cap/timeout ({hours:.3f} CPU h, progress {cur.progress})"
                break
            time.sleep(args.poll)
    finally:
        final = client.meshes.get_mesh_operation(project_id, op_id)
        if final.status not in ("FINISHED", "FAILED", "CANCELED"):
            client.meshes.cancel_mesh_operation(project_id, op_id)
            time.sleep(5)
            final = client.meshes.get_mesh_operation(project_id, op_id)
    summary = {"name": args.name, "result": result, "reason": reason, "status": final.status,
               "progress": final.progress, "cpu_hours": _core_hours(final), "geometry_id": geometry_id,
               "mesh_operation_id": op_id, "simplify_tol": args.simplify_tol, "joint_overlap": args.joint_overlap,
               "wall_s": round(time.time() - started)}
    (out / "gate.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(json.dumps(summary, indent=2, default=str))
    return 0 if result == "PASS" else 1


def cmd_reference_check(args):
    """Runs the pipeline's simulation spec on the manual project's OWN flow-volume
    geometry (kirigami_hex_solid, 954 faces). Converges -> the spec is fine and
    the pipeline's canopy mesh is the problem; diverges -> the spec is. Creates
    one simulation named `pipeline_spec_check` in that project (billed)."""

    from simscale_sdk import ProjectPermissionsApi

    from simscale.runner import REQUIRED_GROUPS, SimScaleClient

    sim_cfg = dict(config.SIMSCALE, MAX_CORE_HOURS_PER_RUN=args.max_core_hours)
    client = SimScaleClient(sim_cfg)
    project = client.find_project(args.project_name)
    if not project:
        sys.exit(f"no project named {args.project_name!r}")
    perms = ProjectPermissionsApi(client.api_client).get_requester_project_permissions(project.project_id)
    if not (perms.can_write_project and perms.can_execute_project_billable_action):
        print(f"This API key cannot write / run billable actions in {args.project_name} "
              f"(write={perms.can_write_project}, billable={perms.can_execute_project_billable_action}). "
              "Nothing was created.")
        return 2
    geo = client.find_flow_volume(project.project_id, set())
    if geo is None:
        sys.exit(f"{args.project_name} has no flow-volume geometry")
    groups, body = client.resolve_entities(project.project_id, geo.geometry_id)
    print(f"{args.project_name}: geometry {geo.name!r} {geo.geometry_id}, fluid region {body}, faces " +
          ", ".join(f"{g}={len(groups[g])}" for g in REQUIRED_GROUPS))
    out = config.RUNS_DIR / "reference_check"
    out.mkdir(parents=True, exist_ok=True)
    state_file = out / "simscale_state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    state.setdefault("geometry_id", geo.geometry_id)
    save = lambda st: state_file.write_text(json.dumps(st, indent=2))  # noqa: E731
    # Reference area/length only scale the reported coefficients; they do not affect convergence.
    coeffs = client.run_design(project.project_id, "pipeline_spec_check", None, -abs(args.uz), args.area, args.length,
                               out, state, save)
    print(json.dumps({k: coeffs.get(k) for k in ("cd", "cl", "cm", "n_iterations", "core_hours_used")}, indent=2))
    return 0


def cmd_depth_model(args):
    from solid.depth_model import get_depth_model

    print(get_depth_model(config.DEPTH_MODEL).summary())
    return 0


def cmd_status(args):
    from optimizer.loop import best_so_far

    ledger = Ledger(config.LEDGER_PATH)
    by_status = {}
    for r in ledger.records:
        by_status[r.get("status")] = by_status.get(r.get("status"), 0) + 1
    print(f"{len(ledger.records)} finished designs: {by_status}")
    if ledger.pending:
        print(f"Pending (will resume): {ledger.pending['design_id']} {sorted(ledger.pending.get('simscale', {}))}")
    budget = config.OPTIMIZER.get("CORE_HOUR_BUDGET")
    print(f"SimScale CPU hours used: {ledger.core_hours_used():.1f}" + (f" of {budget:.0f} budget" if budget else ""))
    print(f"Not yet in Google Sheet: {len([r for r in ledger.unsynced() if r.get('status') != 'dry_run'])}")
    best = best_so_far(Context(ledger=ledger, logger=None, estimator=None))
    if best:
        print(f"Best: {best['design_id']} Cd={best['cd']:.4f}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("dry-run")
    p.add_argument("--preset", default="baseline")
    p.add_argument("--uz", type=float, help="force inlet speed (m/s) instead of the estimate")
    p.set_defaults(fn=cmd_dry_run)

    sub.add_parser("check").set_defaults(fn=cmd_check)

    p = sub.add_parser("inspect-simscale")
    p.add_argument("--project-name", default="Kiragami_CFD")
    p.set_defaults(fn=cmd_inspect)

    p = sub.add_parser("mesh-test")
    p.add_argument("--design-id", nargs="+", required=True, help="ledger design(s) to test, e.g. design_0026")
    p.add_argument("--route", choices=("step", "stl", "manual"), default="step", help="how the air volume is built and imported (default: B-rep STEP)")
    p.add_argument("--cap", type=float, default=3.0, help="CPU-hour cap per design")
    p.add_argument("--pass-progress", type=float, default=0.3, help="cancel and report PASS when the mesh reaches this progress")
    p.add_argument("--timeout", type=int, default=1500, help="seconds of meshing before giving up")
    p.add_argument("--set", nargs="+", metavar="KEY=VALUE", help="numeric parameter overrides for the design")
    p.add_argument("--tag", default="", help="suffix for the test name")
    p.add_argument("--pieces", action="store_true", help="lattice canopy: import the closed ribbons/pins/cap as separate STL blocks")
    p.add_argument("--no-presplit", action="store_true", help="manual route: import the whole canopy as one STL block with SimScale's automatic facet split")
    p.add_argument("--no-improve", action="store_true", help="import without SimScale's geometry improvement/healing")
    p.add_argument("--import-only", action="store_true", help="only check that SimScale accepts the geometry (free, no meshing)")
    p.set_defaults(fn=cmd_mesh_test)

    p = sub.add_parser("build-clean")
    p.add_argument("--design-id", help="a design in the ledger, e.g. design_0026")
    p.add_argument("--params-json", help="a JSON file of design parameters instead of a ledger design")
    p.add_argument("--out-dir", help="output folder (default runs/lattice/<design>)")
    p.add_argument("--step", action="store_true", help="also write the STEP air volume for SimScale")
    p.add_argument("--method", choices=("lattice", "union", "legacy"), default="lattice", help="canopy construction (default: the lattice build)")
    p.set_defaults(fn=cmd_build_clean)

    p = sub.add_parser("export-print")
    p.add_argument("--design-id", required=True, help="a design in the ledger, e.g. design_0023")
    p.add_argument("--height-in", type=float, default=6.0, help="printed height, apex to rim, in inches")
    p.add_argument("--thickness-in", type=float, default=0.12, help="sheet thickness before scaling, in model inches (0.12 -> about 1.8 mm at 6 in tall)")
    p.add_argument("--joint-overlap-in", type=float, default=0.12, help="how far neighbouring ribbons overlap (keeps the model one piece)")
    p.add_argument("--bridge-extra", type=float, default=0.0, help="0 = hinges as wide as the ribbons (as simulated), 1 = double-wide hinges (sturdier)")
    p.add_argument("--out", help="output STL (default exports/<design>_print_<h>in_tall_mm.stl)")
    p.set_defaults(fn=cmd_export_print)

    p = sub.add_parser("prepare-batch")
    p.add_argument("--n", type=int, default=4, help="number of designs in the batch")
    p.add_argument("--from-design", nargs="+", metavar="DESIGN_ID",
                   help="prepare these ledger designs again (built with the current canopy method, same inlet speed) instead of new proposals")
    p.set_defaults(fn=cmd_prepare_batch)

    p = sub.add_parser("run-batch")
    p.add_argument("--batch", required=True, help="batch id printed by prepare-batch")
    p.add_argument("--max-core-hours", type=float, required=True, help="CPU-hour cap per design (mesh + run)")
    p.add_argument("--iterations", type=int, default=None,
                   help="steady-state solver iterations (default 500); the CPU cost grows in proportion")
    p.add_argument("--mesh-fineness", type=float, default=None,
                   help="Simmetrix mesh fineness 0-10 (default from config, 4.5); a finer mesh costs more CPU hours")
    p.add_argument("--no-boundary-layers", action="store_true", help="turn the automatic boundary layers off")
    p.add_argument("--stable-scheme", action="store_true",
                   help="first-order upwind for the turbulence transport (k, omega); more diffusive, shifts Cd")
    p.add_argument("--robust-numerics", action="store_true",
                   help="potential-flow initialisation and lower relaxation factors (for designs whose solver diverged)")
    p.add_argument("--uz", type=float, default=None, help="force the inlet speed (m/s) of every design of this run instead of the stored/estimated one (diagnostic)")
    p.add_argument("--gentle", action="store_true",
                   help="every stabiliser at once: potential-flow start, relaxation p 0.2 / U 0.5 / k,omega 0.5, upwind k/omega (diagnostic; shifts Cd)")
    p.add_argument("--redo", nargs="+", metavar="NAME", help="design names (e.g. batch_..._02) to run again even though they have a result")
    p.add_argument("--batch-core-hours", type=float, required=True, help="stop starting new designs once the batch has used this many")
    p.add_argument("--skip-estimate-check", action="store_true",
                   help="do not refuse a solver run because SimScale's worst-case estimate exceeds the cap; the cap is still enforced live")
    p.set_defaults(fn=cmd_run_batch)

    p = sub.add_parser("single")
    p.add_argument("--preset", default="baseline")
    p.add_argument("--from-design", metavar="DESIGN_ID", help="take the parameters of a ledger design")
    p.add_argument("--clean-step", action="store_true",
                   help="lattice canopy generator + locally built STEP air volume (no manual flow-volume step)")
    p.add_argument("--uz", type=float, help="force inlet speed (m/s), e.g. 6.148 to compare with Run 11")
    p.add_argument("--from-manual", metavar="DESIGN_ID",
                   help="use the flow volume made by `manual-step --design-id DESIGN_ID`")
    p.add_argument("--max-core-hours", type=float, default=None,
                   help="per-design CPU-hour cap for this run (default: config MAX_CORE_HOURS_PER_RUN)")
    p.add_argument("--simplify-tol", type=float, default=None, help="canopy simplification (inches)")
    p.add_argument("--user-style", action="store_true", help="build the canopy like `manual-step --user-style`")
    p.add_argument("--iterations", type=int, default=None,
                   help="steady-state solver iterations (default 500); the CPU cost grows in proportion")
    p.add_argument("--mesh-fineness", type=float, default=None,
                   help="Simmetrix mesh fineness 0-10 (default from config, 4.5); a finer mesh costs more CPU hours")
    p.add_argument("--no-boundary-layers", action="store_true", help="turn the automatic boundary layers off")
    p.add_argument("--stable-scheme", action="store_true",
                   help="first-order upwind for the turbulence transport (k, omega); more diffusive, shifts Cd")
    p.add_argument("--robust-numerics", action="store_true",
                   help="potential-flow initialisation and lower relaxation factors (for designs whose solver diverged)")
    p.add_argument("--auto-flow", action="store_true",
                   help="EXPERIMENTAL, currently fails SimScale's mesh setup check (extra canopy/sting solid bodies without material); see HANDOFF.md. Builds box - canopy - sting locally and imports it as STL; implies --user-style")
    p.add_argument("--skip-estimate-check", action="store_true",
                   help="do not refuse the solver run because SimScale's (worst-case) estimate exceeds the cap; "
                        "the cap is still enforced live")
    p.add_argument("--set", nargs="+", metavar="KEY=VALUE", help="numeric overrides, e.g. BRIDGE_EXTRA=0 JOINT_OVERLAP=0")
    p.set_defaults(fn=cmd_single)

    p = sub.add_parser("optimize")
    p.add_argument("--n-calls", type=int, default=None)
    p.add_argument("--n-initial", type=int, default=None)
    p.add_argument("--clean-step", action="store_true",
                   help="lattice canopy generator + locally built STEP air volume (no manual flow-volume step)")
    p.set_defaults(fn=cmd_optimize)

    sub.add_parser("sync-sheet").set_defaults(fn=cmd_sync)
    sub.add_parser("sync-results").set_defaults(fn=cmd_sync_results)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("depth-model").set_defaults(fn=cmd_depth_model)

    p = sub.add_parser("manual-step")
    p.add_argument("--preset", default="baseline")
    p.add_argument("--design-id", default="manual_baseline")
    p.add_argument("--simplify-tol", type=float, default=None,
                   help="canopy mesh simplification in inches (default: config, 1e-4)")
    p.add_argument("--set", nargs="+", metavar="KEY=VALUE",
                   help="numeric design/solid overrides, e.g. BRIDGE_EXTRA=0 JOINT_OVERLAP=0")
    p.add_argument("--user-style", action="store_true",
                   help="build the canopy exactly like the user's script (config.ONSHAPE CANOPY_OVERRIDES: welded legacy "
                        "strips, flush hinges); SimScale imports it as a 948-face solid without the Onshape round trip")
    p.add_argument("--wait", action="store_true",
                   help="keep waiting (up to MANUAL_WAIT_S) for the flow volume; default: check once")
    p.set_defaults(fn=cmd_manual_step)

    p = sub.add_parser("reference-check")
    p.add_argument("--project-name", default="Kiragami_CFD")
    p.add_argument("--uz", type=float, default=6.148)
    p.add_argument("--area", type=float, default=72.9, help="reference area in^2 (scales coefficients only)")
    p.add_argument("--length", type=float, default=9.634, help="reference length in")
    p.add_argument("--max-core-hours", type=float, default=18.0)
    p.set_defaults(fn=cmd_reference_check)

    p = sub.add_parser("onshape-roundtrip")
    p.add_argument("--preset", default="baseline")
    p.add_argument("--name", default="onshape_design")
    p.add_argument("--simscale-import", action="store_true",
                   help="then import the exported STL into SimScale with facet split (free; reports face count)")
    p.add_argument("--exported-stl", help="skip Onshape; use this already-exported Part Studio STL")
    p.add_argument("--stl", help="send this STL instead of building one from the preset")
    p.add_argument("--set", nargs="+", metavar="KEY=VALUE", help="numeric design overrides")
    p.set_defaults(fn=cmd_onshape_roundtrip)

    p = sub.add_parser("canopy-gate")
    p.add_argument("--preset", default="baseline")
    p.add_argument("--name", required=True)
    p.add_argument("--simplify-tol", type=float, default=1e-4, help="inches (the default is the current setting)")
    p.add_argument("--joint-overlap", type=float, default=None)
    p.add_argument("--geometry-id", help="mesh this geometry already in the project instead of importing the canopy")
    p.add_argument("--cap", type=float, default=0.3, help="CPU-hour cap")
    p.add_argument("--timeout", type=int, default=180, help="seconds")
    p.add_argument("--pass-progress", type=float, default=0.3,
                   help="progress fraction that counts as surface meshing passed")
    p.add_argument("--poll", type=int, default=5)
    p.set_defaults(fn=cmd_canopy_gate)

    p = sub.add_parser("mesh-probe")
    p.add_argument("--variant", choices=sorted(MESH_PROBE_VARIANTS), required=True)
    p.add_argument("--cap", type=float, default=1.0, help="CPU-hour cap; mesh cancelled above it")
    # Run 4 burned ~15 CPU h per hour of meshing (~16 cores), so 4 min of
    # meshing is ~1 CPU h even if SimScale does not report hours live.
    p.add_argument("--timeout", type=int, default=240, help="seconds of meshing before it is cancelled")
    p.add_argument("--stall", type=int, default=180, help="seconds without progress before cancelling")
    p.add_argument("--from-manual", metavar="DESIGN_ID",
                   help="mesh the flow volume made by `manual-step --design-id DESIGN_ID`")
    p.add_argument("--auto-flow", action="store_true",
                   help="EXPERIMENTAL, currently fails SimScale's mesh setup check (extra canopy/sting solid bodies without material); see HANDOFF.md")
    p.set_defaults(fn=cmd_mesh_probe)

    p = sub.add_parser("probe-import")
    p.add_argument("--variants", nargs="+", choices=sorted(IMPORT_VARIANTS),
                   help="import forms to try (default: the configured GEOMETRY_FORMAT)")
    p.set_defaults(fn=cmd_probe_import)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
