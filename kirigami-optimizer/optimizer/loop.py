"""Bayesian optimization loop (scikit-optimize gp_minimize), resumable.

Resume model: every finished design is in the ledger (state/ledger.json)
before gp_minimize ever sees its value, so after a crash we
  1. finish any design that was mid-CFD (its SimScale IDs are in the ledger),
  2. hand all finished (x, y) pairs back to gp_minimize as x0 / y0,
  3. ask only for the remaining number of calls.
skopt's own CheckpointSaver also writes state/skopt_result.pkl after each
call for inspection (skopt.load), but the ledger is what resuming uses.
"""

from skopt import gp_minimize
from skopt.callbacks import CheckpointSaver

import config
from pipeline import design_params, evaluate_design, resume_pending

from .space import dimensions, to_params


def run_optimization(ctx, n_calls=None, n_initial_points=None):
    opt = config.OPTIMIZER
    n_calls = n_calls or opt["N_CALLS"]
    n_initial_points = opt["N_INITIAL_POINTS"] if n_initial_points is None else n_initial_points
    space = ctx.search_space

    resumed = resume_pending(ctx)
    if resumed:
        ctx.log(f"Finished interrupted design {resumed['design_id']}: {resumed['status']}")

    x0, y0 = ctx.ledger.xy()
    remaining = n_calls - len(x0)
    ctx.log(f"{len(x0)} designs already evaluated, {max(remaining, 0)} to go (target {n_calls}).")
    if remaining <= 0:
        return best_so_far(ctx)

    n_init = min(max(0, n_initial_points - len(x0)), remaining)
    if n_init == 0 and not x0:
        n_init = 1

    def objective(x):
        x = [int(v) if d["type"] == "int" else float(v) for d, v in zip(space, x)]
        cached = ctx.ledger.lookup(x)
        if cached:  # integer dims can make the GP re-propose a finished design
            ctx.log(f"Design {x} already evaluated as {cached['design_id']}; reusing its result.")
            return cached["y"]
        record = evaluate_design(ctx, design_params(to_params(space, x)), mode="optimize")
        best = best_so_far(ctx)
        if best:
            ctx.log(f"Best so far: {best['design_id']} Cd={best['cd']:.4f}")
        return record["y"]

    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    gp_minimize(
        objective,
        dimensions(space),
        x0=x0 or None,
        y0=y0 or None,
        n_calls=remaining,
        n_initial_points=n_init,
        initial_point_generator=opt["INITIAL_POINT_GENERATOR"],
        acq_func=opt["ACQ_FUNC"],
        random_state=opt["RANDOM_STATE"] + len(x0),
        callback=[CheckpointSaver(str(config.STATE_DIR / "skopt_result.pkl"), store_objective=False)],
    )
    return best_so_far(ctx)


def best_so_far(ctx):
    ok = [r for r in ctx.ledger.records if r.get("status") == "ok" and r.get("cd") is not None]
    return max(ok, key=lambda r: r["cd"]) if ok else None
