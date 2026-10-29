"""Batches for the semi-manual flow-volume route.

SimScale's "External flow volume" can only be made in the web app, so designs are processed in rounds:

  1. ``prepare_batch``: ask skopt for K feasible designs, import each canopy into SimScale (free) and write
     one checklist for the person doing the web-app step (state in runs/batch/<batch_id>/batch.json).
  2. the person creates the flow volume for every design in one sitting;
  3. ``run_batch``: match each design to its flow-volume geometry and run the billed mesh + solver, one design
     at a time, under a per-design and an overall CPU-hour cap.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import config
from pipeline import build_geometry, design_params, evaluate_design, start_with_geometry
from solid import InfeasibleGeometry, screen_canopy
from simscale import manual_flow

from .space import dimensions, restrict, to_params

BATCH_DIR = config.RUNS_DIR / "batch"


def _clean(space, x):
    return [int(v) if d["type"] == "int" else float(v) for d, v in zip(space, x)]


def propose_points(ledger, space, k, prepared=()):
    """Up to ``k`` new search vectors from skopt (LHS while there are few results, GP afterwards)."""

    from skopt import Optimizer

    opt = config.OPTIMIZER
    x0, y0 = restrict(space, *ledger.xy())  # results outside the (narrowed) space cannot teach skopt anything
    seen = {tuple(x) for x in x0} | {tuple(x) for x in prepared}
    o = Optimizer(dimensions(space), base_estimator="GP", acq_func=opt["ACQ_FUNC"],
                  n_initial_points=max(opt["N_INITIAL_POINTS"], k + len(x0)),
                  initial_point_generator=opt["INITIAL_POINT_GENERATOR"],
                  random_state=opt["RANDOM_STATE"] + len(x0) + len(prepared))
    if x0:
        o.tell(x0, y0)
    out = []
    for x in o.ask(n_points=max(k * 8, k + 8)):  # the sliver screen rejects more than half of the candidates
        x = _clean(space, x)
        if tuple(x) not in seen:
            seen.add(tuple(x))
            out.append(x)
    return out


def _all_prepared(prefix=BATCH_DIR):
    xs = []
    if Path(prefix).exists():
        for f in Path(prefix).glob("*/batch.json"):
            xs += [tuple(e["x"]) for e in json.loads(f.read_text())["designs"]]
    return xs


def prepare_batch(ctx, k, log=print, from_designs=None):
    space = ctx.search_space
    batch_id = "batch_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = BATCH_DIR / batch_id
    out.mkdir(parents=True, exist_ok=True)
    designs, texts = [], []
    if from_designs:  # re-run known designs (e.g. on the clean canopy), at the speed they were first run with
        records = {r["design_id"]: r for r in ctx.ledger.records}
        proposals = []
        for design_id in from_designs:
            rec = records[design_id]
            proposals.append((rec["x"], dict(rec["params"], EXTRUDE_METHOD=config.FIXED_PARAMS["EXTRUDE_METHOD"]),
                              abs(rec["velocity"]["uz_mps"]) if rec.get("velocity") else None, design_id))
        k = len(proposals)
    else:
        proposals = [(x, design_params(to_params(space, x)), None, None) for x in propose_points(ctx.ledger, space, k, _all_prepared())]
    for x, params, uz, source in proposals:
        if len(designs) >= k:
            break
        i = len(designs) + 1
        name = f"{batch_id}_{i:02d}"
        try:
            _, info, _, paths = build_geometry(params, out / name, name)
        except InfeasibleGeometry as exc:
            log(f"  skipped {x}: infeasible ({exc})")
            continue
        if config.OPTIMIZER.get("SCREEN_SLIVERS", True) and params.get("EXTRUDE_METHOD") != "lattice":  # only the legacy canopy has slab overlaps
            import trimesh

            try:
                ok, stats = screen_canopy(trimesh.load(paths["canopy_stl"], force="mesh"),
                                          config.OPTIMIZER["MIN_PAIR_OVERLAP_IN3"])
            except ImportError:
                ok, stats = True, None
                log("  (sliver screen skipped: manifold3d is not installed)")
            if not ok:
                log(f"  skipped {x}: sliver overlaps ({stats['slivers']} pairs, smallest {stats['min_overlap_in3']:.1e} in^3) "
                    "would fail SimScale's mesher")
                continue
        canopy_name = f"{name}_canopy"
        _, import_id = ctx.simscale.start_import(ctx.project_id, canopy_name, paths["domain_file"], True,
                                                 config.SIMSCALE["IMPORT_IMPROVE"], "STL",
                                                 facet_split=not config.SIMSCALE.get("CANOPY_PRESPLIT"))
        try:
            geometry_id = ctx.simscale.wait_for_import(ctx.project_id, import_id)
        except Exception as exc:  # a design SimScale cannot import is simply not run
            log(f"  skipped {x}: SimScale import failed ({type(exc).__name__})")
            continue
        if not ctx.simscale._all_mappings(ctx.project_id, geometry_id, "region"):
            log(f"  skipped {x}: SimScale imported a sheet body, not a solid")
            continue
        d = ctx.simscale.describe_geometry(ctx.project_id, geometry_id)
        n_faces = sum(len(v) for v in d["groups"].values()) + len(d["unlabeled_faces"])
        entry = {"name": name, "canopy_name": canopy_name, "x": x, "params": params, "uz_override": uz, "source_design": source,
                 "canopy_geometry_id": geometry_id, "canopy_faces": n_faces,
                 "bounds_in": info["domain_bounds_in"], "volume_in3": info["volume_in3"],
                 "projected_diameter_in": info["projected_diameter_in"]}
        designs.append(entry)
        texts.append(manual_flow.instructions(name, canopy_name, info["domain_bounds_in"], ctx.project_id))
        log(f"  prepared {name}: {dict(zip([s['name'] for s in space], x))}, {n_faces} faces")
    (out / "batch.json").write_text(json.dumps({"batch_id": batch_id, "designs": designs}, indent=2, default=str) + "\n")
    (out / "MANUAL_STEPS.txt").write_text("\n\n" + ("=" * 70 + "\n\n").join(texts))
    return batch_id, designs, texts


def _find_flow_volume(simscale, project_id, entry):
    """The geometry the web app saved under the canopy's name (saving replaces the geometry id)."""

    for geo in simscale.list_geometry_ids(project_id):
        if geo.name == entry["canopy_name"] and geo.geometry_id != entry["canopy_geometry_id"]:
            faces = simscale._all_mappings(project_id, geo.geometry_id, "face")
            if manual_flow.is_flow_volume(simscale._labels_of(f) for f in faces):
                return geo.geometry_id
    return None


def run_batch(ctx, batch_id, per_design_cap, batch_cap, skip_estimate_check=False, redo=(), log=print):
    path = BATCH_DIR / batch_id / "batch.json"
    batch = json.loads(path.read_text())
    ctx.simscale.cfg["MAX_CORE_HOURS_PER_RUN"] = per_design_cap
    if skip_estimate_check:
        ctx.simscale.cfg["SKIP_ESTIMATE_GATE"] = True
    start_hours = ctx.ledger.core_hours_used()
    for entry in batch["designs"]:
        if entry.get("design_id"):
            if entry["name"] not in redo:
                continue
            entry.setdefault("previous_design_ids", []).append(entry.pop("design_id"))  # run it again
        used = ctx.ledger.core_hours_used() - start_hours
        if used >= batch_cap:
            log(f"Batch CPU-hour cap reached ({used:.2f} >= {batch_cap:g}); stopping.")
            break
        geometry_id = _find_flow_volume(ctx.simscale, ctx.project_id, entry)
        if geometry_id is None:
            log(f"{entry['name']}: no flow volume saved yet; skipped.")
            continue
        log(f"{entry['name']}: running on flow-volume geometry {geometry_id}")
        resume = start_with_geometry(ctx, entry["params"], geometry_id)
        record = evaluate_design(ctx, entry["params"], mode="optimize", resume=resume, uz_override=entry.get("uz_override"))
        entry.update(design_id=record["design_id"], status=record["status"], cd=record.get("cd"),
                     flow_volume_geometry_id=geometry_id)
        path.write_text(json.dumps(batch, indent=2, default=str) + "\n")
        log(f"{entry['name']}: {record['status']} Cd={record.get('cd')}")
    return batch
