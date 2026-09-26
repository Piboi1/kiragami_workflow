"""One full pipeline pass for one design:

    params -> generator (DXF + ring report) -> 3D solid (STL) -> flow domain STL
           -> predicted Uz -> SimScale CFD -> Cd/Cl/Cm -> ledger + CSV + Google Sheet
"""

import json
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import config
from generator import generate_pattern
from optimizer.space import from_params, normalize
from solid import InfeasibleGeometry, build_flow_domain, build_solid, export_stl, write_named_stl
from solid.canopy import DEFAULT_SOLID_PARAMS
from solid.flow_domain import DEFAULT_DOMAIN_PARAMS


@dataclass
class Context:
    ledger: object
    logger: object
    estimator: object
    simscale: object = None          # SimScaleClient, or None for a dry run
    project_id: str = None
    search_space: list = field(default_factory=lambda: config.SEARCH_SPACE)
    log: object = print


def design_params(overrides=None):
    """FIXED_PARAMS with overrides applied (search values or a preset)."""

    params = dict(config.FIXED_PARAMS)
    params.update(overrides or {})
    return params


def search_vector(search_space, params):
    """The optimizer vector for params, or None if a search param is missing."""

    try:
        x = from_params(search_space, params)
    except KeyError:
        return None
    return None if any(v is None for v in x) else x


def build_geometry(params, design_dir, design_id):
    """Generator + solid + flow domain. Returns (report, solid_info, paths)."""

    design_dir = Path(design_dir)
    report = generate_pattern(params, output_dir=design_dir, name=design_id)
    resolved = report["parameters"]

    solid_params = {k: params.get(k, v) for k, v in DEFAULT_SOLID_PARAMS.items()}
    solid_params["CUTS_PER_RING"] = resolved["CUTS_PER_RING"]
    canopy, info = build_solid(report, solid_params)
    canopy_stl = export_stl(canopy, design_dir / f"{design_id}_canopy.stl")

    domain_params = {k: params.get(k, v) for k, v in DEFAULT_DOMAIN_PARAMS.items()}
    fluid, labels, bounds = build_flow_domain(canopy, resolved["PARACHUTE_DIAMETER"], domain_params)
    domain_stl = write_named_stl(fluid, labels, design_dir / f"{design_id}_flow_domain.stl")

    info["domain_bounds_in"] = bounds
    paths = {"dxf": report["files"]["dxf"], "report_json": report["files"]["report_json"],
             "canopy_stl": canopy_stl, "domain_stl": domain_stl}
    return report, info, {**solid_params, **domain_params}, paths


def _row(record):
    """Flat dict for the sheet / CSV (stable, readable column names)."""

    p = record.get("resolved_params", {})
    v = record.get("velocity", {})
    s = record.get("solid", {})
    c = record.get("coefficients", {})
    ids = record.get("simscale", {})
    row = {
        "timestamp_utc": record.get("finished_at"),
        "design_id": record["design_id"],
        "mode": record.get("mode"),
        "status": record.get("status"),
        "Cd": c.get("cd"),
        "Cl": c.get("cl"),
        "Cm": c.get("cm"),
        "Cl_front": c.get("cl_front"),
        "Cl_rear": c.get("cl_rear"),
        "Cd_final_iter": c.get("cd_final"),
        "Cd_window_std": c.get("cd_window_std"),
        "iterations": c.get("n_iterations"),
        "predicted_Uz_mps": v.get("uz_mps"),
        "Cd_used_for_Uz": v.get("cd_used_for_velocity"),
        "Uz_Cd_source": v.get("cd_source"),
        "Uz_nearest_run": v.get("nearest_run"),
        "Uz_nearest_distance": v.get("nearest_distance"),
        "seed_Cd": v.get("seed_cd"),
        "mass_kg": v.get("mass_kg"),
        "ring_count": record.get("ring_count"),
        "clamped_rings": record.get("clamped_ring_count"),
        "projected_diameter_in": s.get("projected_diameter_in"),
        "reference_area_in2": s.get("reference_area_in2"),
        "canopy_depth_in": s.get("canopy_depth_in"),
        "canopy_triangles": s.get("triangle_count"),
    }
    for k, val in p.items():
        row[k] = val
    for k, val in record.get("solid_params", {}).items():
        row[k] = val
    for k in ("RING_OVERLAP_FRACTION", "INNER_OVERLAP_RATIO"):
        row.setdefault(k, record.get("params", {}).get(k))
    row.update({
        "simscale_project_id": record.get("project_id"),
        "simscale_simulation_id": ids.get("simulation_id"),
        "simscale_run_id": ids.get("run_id"),
        "estimated_core_hours": ids.get("estimated_core_hours"),
        "run_duration": c.get("run_duration"),
        "error": record.get("error"),
    })
    return row


def evaluate_design(ctx, params, mode="single", uz_override=None, resume=None):
    """Runs the whole pipeline for one design and returns its ledger record.

    Never raises for a design-level failure: the record gets status
    "failed" and the error text, so the optimizer can keep going.
    """

    ledger = ctx.ledger
    x = search_vector(ctx.search_space, params)
    x_norm = normalize(ctx.search_space, x) if x is not None else None

    if resume:
        design_id = resume["design_id"]
        ctx.log(f"Resuming {design_id} (SimScale state: {sorted(resume.get('simscale', {}))})")
    else:
        design_id = ledger.next_design_id()
        ledger.start(design_id, params, x)
    design_dir = config.RUNS_DIR / design_id
    design_dir.mkdir(parents=True, exist_ok=True)

    record = {"design_id": design_id, "mode": mode, "params": params, "x": x, "x_norm": x_norm,
              "project_id": ctx.project_id}
    try:
        ctx.log(f"[{design_id}] generating pattern + solid")
        report, solid_info, solid_params, paths = build_geometry(params, design_dir, design_id)
        record.update({
            "resolved_params": report["parameters"],
            "solid_params": solid_params,
            "ring_count": report["summary"]["ring_count"],
            "clamped_ring_count": report["summary"]["clamped_ring_count"],
            "solid": solid_info,
            "files": paths,
        })

        if resume and resume.get("velocity"):
            velocity = resume["velocity"]  # the sim already exists with this Uz
        else:
            velocity = ctx.estimator.estimate(x_norm or [], ledger.cfd_history() if x_norm else [],
                                              solid_info["reference_area_in2"])
            if uz_override is not None:
                velocity.update({"uz_mps": -abs(uz_override), "predicted_speed_mps": abs(uz_override),
                                 "cd_source": f"override ({velocity['cd_source']})"})
            ledger.update_pending(velocity=velocity)
        record["velocity"] = velocity
        ctx.log(f"[{design_id}] rings={record['ring_count']} Aref={solid_info['reference_area_in2']:.2f} in^2 "
                f"Uz={velocity['uz_mps']:.3f} m/s (Cd for Uz {velocity['cd_used_for_velocity']:.3f})")

        if ctx.simscale is None:
            record["status"] = "dry_run"
            record["y"] = None
        else:
            sim_state = dict((resume or {}).get("simscale", {}))

            def save_state(state):
                ledger.update_pending(simscale=dict(state))

            coeffs = ctx.simscale.run_design(
                ctx.project_id, design_id, paths["domain_stl"], velocity["uz_mps"],
                solid_info["reference_area_in2"], solid_info["projected_diameter_in"],
                design_dir, sim_state, save_state,
            )
            record["simscale"] = sim_state
            record["coefficients"] = coeffs
            record["cd"] = coeffs["cd"]
            record["status"] = "ok"
            record["y"] = -coeffs["cd"] if x is not None else None
            ctx.log(f"[{design_id}] Cd={coeffs['cd']:.4f} Cl={coeffs.get('cl', float('nan')):.4f} "
                    f"Cm={coeffs.get('cm', float('nan')):.4f}")
    except KeyboardInterrupt:
        raise  # leave the design pending so the next start resumes it
    except InfeasibleGeometry as exc:
        record["status"] = "infeasible"
        record["error"] = str(exc)
        record["y"] = -config.OPTIMIZER["FAILURE_CD"] if x is not None else None
        ctx.log(f"[{design_id}] infeasible (no CFD run): {exc}")
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["traceback"] = traceback.format_exc()
        record["simscale"] = dict((ledger.pending or {}).get("simscale", {}))
        record["y"] = -config.OPTIMIZER["FAILURE_CD"] if x is not None else None
        ctx.log(f"[{design_id}] FAILED: {record['error']}")

    record["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (design_dir / "record.json").write_text(json.dumps(record, indent=2, default=str) + "\n")
    ledger.finish(record)

    record["row"] = _row(record)
    if record["status"] != "dry_run":
        if ctx.logger.log(record["row"]):
            ledger.mark_synced(design_id)
        else:
            ctx.log(f"  (sheet not updated: {ctx.logger.sheet_error}; row kept in {config.LOCAL_RESULTS_CSV})")
    return record


def resume_pending(ctx):
    """Finishes a design that was interrupted mid-run, if there is one."""

    pending = ctx.ledger.pending
    if not pending:
        return None
    return evaluate_design(ctx, pending["params"], mode="resume", resume=pending)


def sync_sheet(ctx):
    """Pushes ledger records that never reached the Google Sheet."""

    n = 0
    for record in ctx.ledger.unsynced():
        if record.get("status") == "dry_run":
            continue
        if ctx.logger.push_to_sheet(_row(record)):
            ctx.ledger.mark_synced(record["design_id"])
            n += 1
    return n
