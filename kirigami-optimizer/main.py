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
"""

import argparse
import json
import sys

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
                  estimator=_estimator())
    record = evaluate_design(ctx, design_params(_preset(args.preset)), mode="dry_run", uz_override=args.uz)
    print(json.dumps(record["row"], indent=2, default=str))
    print("\nFiles:", json.dumps(record.get("files", {}), indent=2))
    return 0 if record["status"] == "dry_run" else 1


def cmd_check(args):
    ok = True
    print("SimScale:")
    try:
        client, project_id = _simscale_client()
        projects = client.list_projects(limit=100)
        print(f"  OK - API key works, {len(projects)} project(s) visible; pipeline project id = {project_id}")
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


def cmd_status(args):
    from optimizer.loop import best_so_far

    ledger = Ledger(config.LEDGER_PATH)
    by_status = {}
    for r in ledger.records:
        by_status[r.get("status")] = by_status.get(r.get("status"), 0) + 1
    print(f"{len(ledger.records)} finished designs: {by_status}")
    if ledger.pending:
        print(f"Pending (will resume): {ledger.pending['design_id']} {sorted(ledger.pending.get('simscale', {}))}")
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

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
