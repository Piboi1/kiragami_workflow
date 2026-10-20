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
    return Context(ledger=Ledger(config.LEDGER_PATH), logger=logger, estimator=_estimator(),
                   simscale=client, project_id=project_id)


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
    ctx = _live_context()
    if ctx.ledger.pending:
        print("An interrupted design is pending; finishing it first.")
        resume_pending(ctx)
    record = evaluate_design(ctx, design_params(_preset(args.preset)), mode="single", uz_override=args.uz)
    print(json.dumps(record["row"], indent=2, default=str))
    return 0 if record["status"] == "ok" else 1


def cmd_optimize(args):
    from optimizer.loop import run_optimization

    ctx = _live_context()
    best = run_optimization(ctx, n_calls=args.n_calls, n_initial_points=args.n_initial)
    if best:
        print(f"Best design: {best['design_id']} Cd={best['cd']:.4f}")
        print(json.dumps(best["params"], indent=2))
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
    from solid.step_export import write_step

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
        path = out / "probe_domain.step"
        write_step(fluid, labels, path)
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
}


def cmd_mesh_probe(args):
    """Meshes the baseline design ONLY (no solver) with a settings variant,
    under a hard CPU-hour cap; the mesh is cancelled on SimScale if it trips."""

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
    _, info, _, paths = build_geometry(params, out, f"meshprobe_{args.variant}")
    state = {}
    state_file = out / "simscale_state.json"
    started = time.time()
    summary = {"variant": args.variant, "settings": MESH_PROBE_VARIANTS[args.variant], "cap_cpu_h": args.cap}
    try:
        result = client.run_design(project_id, f"meshprobe_{args.variant}", paths["domain_file"], -6.148,
                                   info["reference_area_in2"], info["projected_diameter_in"], out, state,
                                   lambda s: state_file.write_text(json.dumps(s, indent=2)), mesh_only=True)
        summary.update(status="FINISHED", **result)
    except Exception as exc:
        summary.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
    summary["mesh_core_hours"] = state.get("mesh_core_hours", summary.get("mesh_core_hours"))
    summary["wall_minutes"] = round((time.time() - started) / 60, 1)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary["status"] == "FINISHED" else 1


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

    p = sub.add_parser("single")
    p.add_argument("--preset", default="baseline")
    p.add_argument("--uz", type=float, help="force inlet speed (m/s), e.g. 6.148 to compare with Run 11")
    p.set_defaults(fn=cmd_single)

    p = sub.add_parser("optimize")
    p.add_argument("--n-calls", type=int, default=None)
    p.add_argument("--n-initial", type=int, default=None)
    p.set_defaults(fn=cmd_optimize)

    sub.add_parser("sync-sheet").set_defaults(fn=cmd_sync)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("depth-model").set_defaults(fn=cmd_depth_model)

    p = sub.add_parser("mesh-probe")
    p.add_argument("--variant", choices=sorted(MESH_PROBE_VARIANTS), required=True)
    p.add_argument("--cap", type=float, default=1.0, help="CPU-hour cap; mesh cancelled above it")
    # Run 4 burned ~15 CPU h per hour of meshing (~16 cores), so 4 min of
    # meshing is ~1 CPU h even if SimScale does not report hours live.
    p.add_argument("--timeout", type=int, default=240, help="seconds of meshing before it is cancelled")
    p.add_argument("--stall", type=int, default=180, help="seconds without progress before cancelling")
    p.set_defaults(fn=cmd_mesh_probe)

    p = sub.add_parser("probe-import")
    p.add_argument("--variants", nargs="+", choices=sorted(IMPORT_VARIANTS),
                   help="import forms to try (default: the configured GEOMETRY_FORMAT)")
    p.set_defaults(fn=cmd_probe_import)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
