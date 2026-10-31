# Agent handoff: kirigami parachute CFD pipeline

Read this first. `HANDOFF.md` is the long chronological log (every experiment, newest at the bottom); this file is the current state.
Repo `piboi1/kiragami_workflow`, folder `kirigami-optimizer/`, branch `claude/kirigami-parachute-pipeline-t24c2i`. Do not open a PR unless asked.

## Goal
Find the kirigami parachute design (rings 27-32, cuts per ring 4-8, ring spacing exponent, ring overlap, inner radius) with the highest drag coefficient Cd.
SimScale does the CFD (steady k-omega SST, 500 iterations, inlet speed per design); a Bayesian optimizer (skopt) proposes designs; Cd uses each design's own projected area.
This machine only orchestrates. Results go to `state/ledger.json` (git-ignored; `snapshot/` has copies), a local CSV and the Google Sheet (`runs` tab plus a ranked `results` tab).

## Standing rules from the user (keep following them)
- Every BILLED SimScale run needs the user's explicit approval AND a CPU-hour cap (geometry imports, failing meshes and `--import-only` checks are free). The user gives caps like "5 CPU h each".
  The permission classifier has blocked a 15 CPU h launch once until the user re-approved in plain words; never work around a denial.
- Never print or commit keys; never ask the user to paste credentials into chat.
- Commit and push to the branch every time (a stop hook complains about untracked or unpushed files). Commit messages end with the Co-Authored-By / Claude-Session lines.
- The flow-volume step is manual: the user makes it in the SimScale web app (box X +-25.830, Y +-25.830, Z -70 .. +31.13 in; External flow volume, then Delete bodies, save). Prepared batches write the checklist to `runs/batch/<id>/MANUAL_STEPS.txt`.
- Speak plainly; give recommendations, not menus; report failures with the numbers.

## State (2026-10-31)
- 43 ledger runs on 17 distinct shapes, about 97 CPU h billed of the 800 CPU h budget. 9 valid Cds: eight from the LEGACY build (0.7316 - 0.7863, best design_0023 = 29 rings, 6 cuts, exponent 1.30) and ONE from the new build:
  design_0043 = design_0023's shape on the lattice build, Cd 0.6907 (partial run, stopped by the CPU cap at iteration 400/500, drift 0.05%).
- Old and new builds are NOT comparable (new ribbons are uniformly 0.015 in thick, with joint pins); only results of the same build train the optimizer (`Ledger.geometry_method`, set from `config.FIXED_PARAMS["EXTRUDE_METHOD"]` = "lattice").
- Pretty overview of everything tried: `exports/geometry_error_atlas.pdf` (regenerate: `python tools_error_map.py && python tools_mesh_quality.py && python tools_error_atlas.py`).

## What was learned (in order of importance)
1. Mesher failures ("self intersection", `fault-edge-invalid`, slit faces) came from the OLD builds: slabs thickened along node-averaged normals (up to 87 deg off the ribbon), fused by a boolean, leaving crossing triangles, 1e-7 in edges, 1e-9 in slivers. Fixed by the lattice build.
2. The lattice build (`solid/lattice_canopy.py`, `EXTRUDE_METHOD="lattice"`): legacy hexagon pattern (`build_open_strips`) with each ribbon thickened along its own normals, a small octagonal pin along every node edge, a smooth bell cap, exact union (manifold3d),
   then short edges/sliver triangles merged crossing-safely (`booleans.collapse_short_edges`) and flat slivers flipped (`booleans.flip_slivers`); it raises if any sliver (< 4e-4 in height), crossing or second body is left. Local checker: `solid/selfcheck.py`.
   Pins are now nearly flush (`PIN_RADIUS_T` 0.6, `PIN_EXTENSION_T` 0.2, falls back to bigger pins). Check any design for free: `python main.py build-clean --design-id design_0026 [--step]`.
3. A dome/shell rewrite (plan view lifted onto the paraboloid) was tried and REJECTED by the user: the real model is a honeycomb of radially wide fins, hexagons exist only in 3D. Do not go back to it.
4. Solver divergence (about half of all runs) is NOT caused by geometry cleanliness, inlet speed (2.5 vs 5.2 m/s), or numerics (potential-flow start, relaxation, upwind k/omega all tried): it followed the MESH.
   `tools_mesh_quality.py` shows converged runs had <= 81 tetrahedra with non-orthogonality > 88 deg and 45-109k prism cells; clean/lattice runs imported as ONE face had 190-1082 and diverged every time (10/10).
5. Importing the lattice canopy as separate closed pieces (`LATTICE_PIECES` True, `write_pieces_stl`; ~900 faces, like the legacy pre-split import) gave a better mesh (376-583 flat tets), the solver did NOT diverge, and design_0043 converged -
   but the mesh has 7.3 M cells (legacy: 1.44 M for the same design at the same settings), so one design costs 15-17 CPU h (mesh 0.9, solve ~16). The runner refuses to start the solver above 250 flat tets (`MESH_MAX_BAD_TETS`; use `--max-bad-tets` to lift it).
6. Mesh cost levers tested on the saved 0023 lattice flow volume (`mesh-check`, about 0.9 CPU h each): small-feature tolerance, physics-based off: no effect at all (identical cell counts); boundary layers off: 5.15 M cells; fineness 3: 6.5 M. So the cost is in the canopy geometry as SimScale reads it
   (suspect: the small faces around the joint pins and cap). Surface area (111 in^2) and thickness equal the legacy canopy's.
7. SimScale keeps billing while a cancel propagates (design_0043: 17.5 CPU h billed against a 15 cap); `RUN_CAP_SAFETY` 0.85 now cancels earlier. A run stopped by the cap is accepted automatically if it has >= 300 iterations and its Cd is as settled as a finished run's (`partial_result`; `main.py salvage-run` for old ones).

## Open decision (the user was asked, no answer yet)
Prepared and waiting: `batch_20261031_164247_01` = design_0023 shape with nearly flush pins (884 faces). Needs the user's flow-volume clicks, then `python main.py mesh-check --batch batch_20261031_164247 --name batch_20261031_164247_01` (about 1 CPU h) to see whether flush pins cut the 7.3 M-cell mesh.
Options offered: (A) that test, (B) stop at 350 iterations (the Cd had settled; about 30% cheaper solve; `--iterations 350`), (C) both. Recommended: A first. If the mesh stays near 7 M cells, the realistic fallback is: use the lattice build only where the legacy build fails the mesher, accept about 16 CPU h per design
(about 45 designs in the budget), or investigate the pin/cap faces further (e.g. cap as a flat disc, fewer pin sides).

## Commands (all from `kirigami-optimizer/`)
- `python main.py build-clean --design-id D [--method lattice|union|legacy] [--step]` free local build + checks.
- `python main.py prepare-batch --from-design D1 D2` (or `--n K` for optimizer proposals): free; imports canopies and writes the manual checklist. Re-prepares known designs at their original inlet speed.
- `python main.py run-batch --batch B --max-core-hours CAP --batch-core-hours N --skip-estimate-check [--redo NAME] [--max-bad-tets N] [--mesh-fineness F] [--iterations N] [--gentle] [--uz M/S]` BILLED.
- `python main.py mesh-check --batch B --name NAME [...]` BILLED (~1 CPU h): mesh only on a saved flow volume. `mesh-test` (STEP route, `--import-only` free) is the older automatic rig. `salvage-run`, `sync-results`, `status`, `optimize --clean-step` (STEP route, too expensive).
- `python -m pytest tests -q` (63 tests, about 8 minutes; `tests/test_optimizer_resume.py` pins the unioned slab canopy because the experimental sting route needs it).

## Pitfalls
- `pkill -f "<pattern>"` or `pgrep -f` with a pattern that also appears in your own command kills your own shell; use PIDs (`pgrep -f "[p]attern"` trick) instead.
- Foreground `sleep` is blocked; poll with `timeout N bash -c 'until grep ...; do sleep 15; done'`.
- `run_design` reuses a saved `geometry_id`; saving a flow volume in the web app REPLACES the canopy geometry id (same name). Cleared `pending` ledger entries are the usual way out after killing a run (check SimScale for a still-running job first and cancel it).
- Cd of a run that SimScale reports FINISHED can still be garbage; the plausibility/convergence checks in `simscale/runner.py` and the runaway guard exist for that.
- The STL auto-flow route (sting) and the STEP B-rep route do not work in production (extra bodies / 20x mesh cost); the semi-manual flow volume is the validated route.
- Runs 0001, 0010, 0014-0016, 0019 in the ledger were pipeline/settings problems, not geometry (see the atlas cover page).

## Files worth knowing
`solid/canopy.py` (strip builder, legacy build, dispatch), `solid/lattice_canopy.py`, `solid/booleans.py`, `solid/selfcheck.py`, `pipeline.py` (`build_geometry`, `evaluate_design`), `simscale/runner.py` (import, mesh, quality gate, solve, partial results),
`simscale/spec.py` (solver/mesh settings), `optimizer/batch.py` (prepare/run batches), `optimizer/state.py` (ledger), `config.py` (all defaults), `tools_*.py` (analysis), `exports/` (images, atlas, print model of design_0023), `docs/simscale_support_request.md` (draft to SimScale support).
