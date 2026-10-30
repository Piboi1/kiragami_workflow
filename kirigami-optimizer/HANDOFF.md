# Handoff: kirigami parachute Cd optimizer (SimScale)

Branch `claude/kirigami-parachute-pipeline-t24c2i`. Code is in `kirigami-optimizer/` (README.md explains the pipeline).
Tests: `python -m pytest -q tests` (43 pass). Install: `pip install -r requirements.txt`, then
`pip install --no-build-isolation git+https://github.com/SimScaleGmbH/simscale-python-sdk.git`
(needs `pip install -U --ignore-installed setuptools wheel` first on this image).

## Rules from the user
- Cost-sensitive: get explicit approval, with the CPU-hour cap, before ANY billed SimScale mesh/solver run.
  Geometry imports are free. Total spent so far is about 15 CPU h (conservative count) of the 800 CPU h budget.
- Never print or commit keys. Never ask the user to paste a key into chat.
- Commit and push to the branch above; the stop hook wants a clean tree.

## What is proven
- The pipeline's SimScale setup (spec.py numerics, mesh settings) reproduces the manual **Run 11** on the user's own
  geometry: pipeline force 0.789 N vs Run 11 0.797 N (1%). `main.py reference-check` did this inside Kiragami_CFD
  (simulation `pipeline_spec_check`, 3.5 CPU h). Run 11 used reference area **50.27 in^2** and length **8 in**
  (not the projected area my code uses) -> Cd 1.087. With 50.27 in^2 the pipeline's Cd is 1.076.
- Solver numerics must be the manual runs' (PBICGStab+DILU velocity solver, BOUNDED_GAUSS_UPWIND convection,
  GAUSS_LINEARUPWINDV_UNLIMITED default); SimScale's defaults diverge (manual Runs 1-4 did too). Now in spec.py.
- Semi-manual flow volume works: `main.py manual-step` imports the canopy only (STL, sewing on = solid), the user makes
  External flow volume + Delete bodies in the SimScale web app (box X/Y +-25.83 in, Z -70..+31.13 in), the pipeline
  finds the new geometry by its `Max Z@Flow region` face labels. Saving in the web app replaces the geometry ID.
- Network: `www.simscale.com` (results download) and `cad.onshape.com` are allowed now. Results parse fixed.

## The open problem: my canopy geometry diverges, the user's does not
Runs on my canopy (flush hinges, no joint overlap) diverged at 24-52% in rings 7-8 (r 1.5-1.65 in) every time,
deterministically (same cell, same mesh 1,264,478 cells). Findings:
- Volume matches (0.900 vs 0.8826 in^3), so thickness/widths are right. Hinge must be flush (BRIDGE_EXTRA=0).
- My union-of-slabs mesh has hinge-corner near-contacts (~6e-4 in gaps), sliver triangles, and SimScale's
  automatic facet split fails (1 face; the user's has 948). A pre-split STL import is rejected with
  `fault-face-self_int` / `fault-face-slit` at those spots. Overlapping the joints makes it worse.
- The user's real pipeline: script STL (721 overlapping slabs, `legacy/kiragami3dcreator.py`, no union; the saved
  script is missing the `shell = trimesh.Trimesh(...)` line) -> **Onshape import** (Part 1 + 704 surfaces) ->
  **export entire Part Studio as STL, basic settings** -> SimScale import (auto facet split ~948 faces) -> flow volume.
  The Onshape round trip repairs/re-tessellates; sending the raw overlapping-slab STL straight to SimScale does not
  import in 30+ min.

## Next steps
1. A new session should see `ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY` (check presence only). The user's Onshape is a
   FREE plan: documents are public and the API limit is 2,500 requests/year (budget ~10-30 per design; add a counter
   that stops near 2,000). Reuse one document and overwrite it.
2. First ask the user to put their Onshape-exported STL on Google Drive (a connector exists) to compare with mine
   (sliver counts, gaps, face structure) before building automation.
3. Then build an Onshape step: upload legacy-style STL (flush hinges, `EXTRUDE_METHOD="legacy"`), import, export Part
   Studio STL, feed that to SimScale (import with facet split) and compare face count to 948.
4. Ask the user which Cd convention the optimizer should use: fixed reference area 50.27 in^2 (Run 11) or per-design
   projected area. Then set it in `pipeline.py` / config.
5. Other open questions: payload 20 g vs 50 g; depth measurements for other cut counts; weigh the real canopy.

## Useful commands
`main.py check`, `manual-step --design-id X [--simplify-tol 3e-3 --set BRIDGE_EXTRA=0 JOINT_OVERLAP=0]`,
`single --from-manual X --uz 6.148 --max-core-hours N`, `reference-check`, `mesh-probe`, `canopy-gate` (weak),
`inspect-simscale`. Logs of every run are in `logs/`.

## Update: Onshape step built (next session)
- `main.py onshape-roundtrip --name N [--simscale-import]` builds the legacy-style canopy (config.ONSHAPE CANOPY_OVERRIDES),
  imports it into Onshape document `kirigami_optimizer` (ids in state/onshape_doc.json), exports the Part Studio STL
  (basic settings, via async translation because the direct /stl redirect to cad-usw2.onshape.com is blocked by the sandbox
  proxy) and optionally imports that into SimScale with facet split (free). Counter: state/onshape_usage.json (cap 2000).
- Result for the baseline: Onshape imported 753 parts (user's manual: Part 1 + 704 surfaces); exported STL 18,336 tris, same
  volume (0.9142 in^3). SimScale import with facet split FINISHED as a SOLID with **2718 faces** (manual: ~948;
  my union mesh: 1 face / rejected). Geometry fde1fdf5-81de-459a-974f-a2cf426ca20b in the project. Not yet meshed.
- Onshape key cannot DELETE elements (403): old blobs/Part Studios pile up in the document until the key scope is widened.
- Next: create the flow volume on that geometry (manual-step flow) and run the canopy-gate mesh test (needs user approval + CPU-h cap).

## Update: flow volume on the Onshape canopy + gate run
- User made the flow volume in the web app; the geometry ID changed `fde1fdf5...` -> **`d17a9d17-b17d-4a28-b051-b5d01ab0fb44`** (`rt1_onshape`): one fluid region (`B2_TE5`), 8 face groups, no unlabeled faces.
- Decision: Cd uses the **per-design projected area** (`solid/canopy.py` `reference_area_in2`, already the code's behavior), not the fixed 50.27 in^2 of Run 11.
- `main.py canopy-gate` got `--geometry-id` (mesh a geometry already in the project; no import). Ran
  `canopy-gate --name onshape_rt1 --geometry-id d17a9d17-... --cap 0.3 --timeout 180` (user-approved 0.3 CPU h cap):
  **INCONCLUSIVE** - stopped at the cap (0.31 CPU h) at 10% progress, no FAILED status and no self-intersection error
  (previous union-mesh canopies failed at surface meshing; see `logs/canopygate_onshape/`). Total spent now about 15.3 of 800 CPU h.
- Next (needs user approval + cap): either a longer gate (e.g. `--cap 1 --timeout 600`) to see whether meshing passes 30%, or go straight to
  `mesh-probe`/`single` on this geometry. Then check the solver does not diverge in rings 7-8 as it did on the union-mesh canopy.

## Update: mesh-probe on the Onshape flow volume FAILED (0 CPU h billed)
- `mesh-probe --variant M --from-manual onshape_rt1 --cap 1.0 --timeout 240` (user-approved 1 CPU h), geometry `d17a9d17-...`
  (made by writing `runs/manual_step/onshape_rt1/simscale_state.json` = `{"geometry_id": "d17a9d17-..."}`; `runs/` is gitignored).
  Failed at 10% progress in surface meshing, 0.0 CPU h billed: **self intersection between faces B2_TE11477 and B2_TE45089
  at (0.0407, 0.0022, -0.0328) m = (1.60, 0.088, -1.29) in, r = 1.60 in, angle 3.1 deg** - the same ring 7-8 zone (r 1.5-1.65 in)
  where the earlier canopies diverged. So the Onshape round trip + 2718-face split did NOT fix SimScale's surface mesher there,
  and the earlier 0.3 CPU h gate run was just slow (10%), the failure came after.
- Logs: `logs/meshprobe_onshape/`. Total spent still about 15.3 of 800 CPU h.
- Open: the user's manual geometry meshes fine. Differences to examine at r~1.6 in: hinge/slab overlap structure there vs the manual
  Onshape-exported STL (the Google Drive comparison was skipped by the user's choice), Onshape export settings
  (the user used "basic settings"; check whether chord/angle tolerance in the translation matches), or increase SimScale
  geometry import tolerances. Another option: get the user to export the geometry from the web app (mesh fine) and compare face names.

## Update: ROOT CAUSE FOUND - the canopy STL, not Onshape (supersedes the "open problem" above)
The user uploaded (to piboi9740@gmail.com's Drive; the Drive connector reads that account) their Onshape-exported STL
(`kirigami_hex_solid (3).stl`, in **mm**; the earlier `(1)` is an older, denser design and is NOT the Run 11 geometry).
Analysis (scripts were scratch, results below):
- It is almost a pass-through of the script STL: 15360 tris, 705 watertight pieces (111 with inverted orientation), volume
  0.7744 in^3, centre cap 0.0296 in^3, **neighbouring parts share exact vertices**. My earlier STL (`process=False` shell)
  had 18400 tris, 721 separate bodies and 0.001-0.01 in gaps between hinge and slab corners (the near-contacts that SimScale
  rejects as self-intersections at r~1.6 in).
- The user's script builds `shell = trimesh.Trimesh(verts, faces)` with the DEFAULT `process=True` (welds coincident vertices, so
  strips share vertices and the +-t/2 offsets use averaged normals) and its trimesh also ends up with a consistent winding
  (the cap fan is wound opposite to the hinges; `trimesh.repair.fix_winding(shell)` reproduces it). With flush hinges
  (BRIDGE_EXTRA=0) the result matches the user's Onshape export vertex-for-vertex (max 3e-6 in).
- Now implemented: `WELD_STRIPS=1` in `solid/canopy.py` (legacy method) and in `config.ONSHAPE["CANOPY_OVERRIDES"]`;
  `main.py manual-step --user-style` builds it. Test: `test_welded_legacy_matches_users_onshape_export`.
- **SimScale imports the generator's welded STL directly as a SOLID with 948 faces (same as the user's file): geometry
  `c1110008-6e9b-4e8e-b3eb-9794d96fa9a1` (`weld2_raw_direct`). The Onshape round trip is NOT needed** (it even fragments the
  mesh through the API: 715 parts, 56 one-triangle pieces, and the SimScale import then yields 1 face). Onshape request counter
  reads ~64 (state lost between containers); a stray public Onshape doc `kirigami_optimizer` was created in this container's run.
- Volume of this canopy is 0.774 in^3 vs 0.8826 in^3 reported for Run 11 - the cause is unexplained (different ring data or
  thickness in the Run 11 script version?). Worth asking the user which script/version produced Run 11.
- Next: the user makes the External flow volume + Delete bodies on `c1110008...` in the web app (box X/Y +-25.83 in, Z -70..+31.13 in),
  then `mesh-probe --variant M --from-manual <id>` (needs `runs/manual_step/<id>/simscale_state.json` with the new geometry id) under an
  approved CPU-h cap, then the solver. Total spent still about 15.3 of 800 CPU h.

## Update: mesh-probe on the welded canopy FINISHED
- User saved the flow volume on `weld2_raw_direct`: new geometry **`c9f66466-2163-4b95-98fd-e68d2cf95bca`** (954 faces, 1 fluid region `B2_TE5`).
  `runs/manual_step/weld2/simscale_state.json` = `{"geometry_id": "c9f66466-..."}` (runs/ is gitignored; recreate it if missing).
- `mesh-probe --variant M --from-manual weld2 --cap 1.0 --timeout 240` (user-approved 1 CPU h): **FINISHED**, no self-intersection.
  1,683,594 cells, 444,326 nodes, 0.275 CPU h, 2.7 min wall. (The union-mesh canopies meshed to 1,264,478 cells.) Logs: `logs/meshprobe_weld2/`.
  Total spent about 15.6 of 800 CPU h.
- Next (needs user approval + CPU-h cap; the reference check cost ~3.5 CPU h): `main.py single --from-manual weld2 --uz 6.148 --max-core-hours N`
  to check the solver does not diverge in rings 7-8, then compare force/Cd with Run 11 (0.797 N). Cd uses the per-design projected area.
- Still open: canopy volume 0.774 in^3 (script) vs 0.8826 in^3 reported for Run 11.

## Update: `single --from-manual weld2 --user-style --uz 6.148 --max-core-hours 5` refused before the solver ran
- Mesh finished (0.26 CPU h billed). The solver was NOT started: SimScale's pre-run estimate was **18.66 CPU h > cap 5.0**
  (`_estimate_ok` in `simscale/runner.py`). The same estimate (18.66) blocked the earlier reference-check at cap 18; that run was then
  allowed to go and actually used only 3.5 CPU h, so the estimate is a worst-case bound (max run time), not the expected cost.
- The live enforcement is separate: during the run, `run_cap = MAX_CORE_HOURS_PER_RUN - mesh hours`, and the run is cancelled if exceeded.
- Options awaiting the user: (a) keep the 5 CPU h limit enforced live but skip the pre-run estimate gate (new flag), or (b) raise the cap to ~20.
- Total spent about 15.9 of 800 CPU h.

## Update: SOLVER RUN ON THE WELDED CANOPY SUCCEEDED (design_0002)
- `main.py single --from-manual weld2 --user-style --uz 6.148 --max-core-hours 5 --skip-estimate-check` (user-approved: skip only the
  pre-run estimate gate, 5 CPU h enforced live). New flag `--skip-estimate-check` (`SKIP_ESTIMATE_GATE` in `simscale/runner.py`).
- Result: **FINISHED, no divergence** (500 iterations, 12 min run): **Cd = 0.7327** (final iter 0.7332, window std 0.0003), Cl -0.0002, Cm 0.0003,
  Aref 72.90 in^2 (per-design projected area, as the user chose). Force scales with Cd: about 0.779 N vs the reference check on the user's
  manual geometry 0.789 N (Cd 0.742) and Run 11 0.797 N -> 1.3% / 2.3% lower. The canopy volume differs from Run 11 (0.774 vs 0.8826 in^3, thickness
  was forgotten by the user; accepted - the comparison is canopy-to-canopy with the same pipeline).
- Run used 3.51 CPU h (estimate was 18.66, the real cost matches the reference run's 3.5); with meshes this attempt about 3.8 CPU h.
  Total spent about 19.7 of 800 CPU h. The pipeline is now validated end to end on a script-generated canopy with a manual flow volume.
- The flow volume is still made by hand in the web app (one per geometry; saving replaces the geometry ID). Per-design automation of that step is the
  remaining blocker for `optimize`.

## Update: flow-volume automation (local box - canopy, STEP import) + Run 11 volume mystery solved
- **Volume solved:** the union of the 705 welded pieces (each oriented outward; 111 are stored inside-out so the signed mesh volume
  0.774 understates it) is **0.8826 in^3 = exactly the Run 11 volume**. The canopy IS Run 11's; the thickness was fine. (`solid.weld_union`)
- SimScale's public API/SDK/OpenAPI spec has CAD schemas (`CadFeatureRequest`, `CadQueryRequest`, ...) but NO endpoints for them, so
  the web app's "External flow volume" cannot be called. Instead: `GEOMETRY_FORMAT="STEP"` builds box - union(canopy) locally with float64
  manifold3d booleans (`solid/flow_domain.py`) and writes a B-rep STEP (`solid/step_export.py`).
- **Free import test passed:** SimScale imported that STEP (21 MB, 19,581 faces) as ONE flow region (`B1_TE77947`) with inlet/outlet/
  side_xmin/xmax/ymin/ymax = 1 face each, canopy = 19,575 faces, no unlabeled faces (geometry `12da3752-924d-488d-83c6-602e8fbe5347`,
  `auto_flow_step`). The manual route gives 954 faces (facet-split groups); the STEP route has one face per canopy triangle (the ribbon
  triangles are non-planar so nothing merges), which may make meshing slower/heavier. NOT meshed yet.
- Wired in: `main.py mesh-probe --auto-flow ...` and `main.py single --auto-flow ...` (implies the user-style canopy; `pipeline.build_geometry`
  unions the legacy pieces for STEP). Test `test_weld_union_gives_the_run11_volume`.
- Next (needs user approval + CPU-h cap): `mesh-probe --variant M --auto-flow --cap 1.0` to see whether the 19.6k-face STEP meshes like the
  manual flow volume (reference: 1,683,594 cells, 0.275 CPU h); then `single --auto-flow --uz 6.148 --max-core-hours 5 --skip-estimate-check`
  and compare Cd with the manual route (0.7327). If meshing is too heavy, merge canopy triangles into larger faces (like SimScale's facet split).
- Git tag `pipeline-works-cd0.7327` exists locally on 8ad6655 but pushing it failed twice ("unexpected disconnect"); branch commits push fine.

## Update: mesh-probe on the automated STEP flow volume was TOO HEAVY (0.61 CPU h billed, cancelled)
- `mesh-probe --variant M --auto-flow --cap 1.0 --timeout 240` (user-approved 1 CPU h): geometry import fine, but meshing was still at **10%** when the
  240 s timeout cancelled it; **0.61 CPU h billed** (the manual 954-face flow volume meshes completely in 2.7 min for 0.275 CPU h).
  Cause (most likely): the STEP B-rep has one planar face per canopy triangle (19,575 faces vs 954 from SimScale's facet split), which the
  surface mesher handles slowly. Logs: `logs/meshprobe_autoflow/`. Total spent about 20.3 of 800 CPU h.
- The manual route (`manual-step --user-style` + web app flow volume, then `single --from-manual`) remains the validated one (Cd 0.7327).
- Ideas to make the automated route cheap (all free to test up to the import): group canopy triangles into ~1000 faces like SimScale's facet split
  (needs a face representation that is not "one planar triangle each", e.g. the tessellated-STEP writer `write_tessellated_step`), or import
  the canopy as the 948-face solid (works) and find another way to get box - canopy (the web-app CAD edit is not in the public API).

## Update: other local flow-volume routes tested (all free imports) - none works yet
- Lesson I missed before spending the 0.61 CPU h: `solid/step_export.py` already said the one-planar-face-per-triangle B-rep is too heavy to mesh.
- AP242 tessellated STEP (`write_tessellated_step`, 7 faces): SimScale rejects it (`invalid-file`, both with and without facet split).
- Multi-solid STL with sting (box - union canopy - sting, sewing + facet split): SimScale import FAILS (`unexpected-error`). The union has ~1000
  triangles with altitude < 1e-3 in (48 < 1e-4); a sting would also change the physics slightly versus the validated runs.
- Recommendation: keep the validated semi-manual route (`manual-step --user-style`, one web-app flow-volume step, then `single --from-manual
  <id> --user-style --uz 6.148 --max-core-hours 5 --skip-estimate-check`). Only remaining automation ideas: remesh/clean the union (decimate slivers) before the
  STL import, or ask SimScale support whether the CAD-feature endpoints (schemas exist in the OpenAPI spec) can be enabled for API keys.

## Update: sliver cleanup WORKED - automated flow volume (sting STL route) imports as ONE flow region with ~960 faces
- Sweep `logs/sliver_sweep.log` (union of the welded pieces, `booleans.simplify` tolerance -> box - canopy - sting STL -> SimScale import with
  sewing + facet split): tolerance 1e-4 and 5e-4 in -> SimScale `unexpected-error`; **2e-3 in -> imports** as one flow region (`B9_TE6`),
  961 faces: inlet/outlet/4 sides = 1 face each, canopy 950 (manual route: 948-954), 5 unlabeled (the sting; the `sting` group name is not
  recognised, so those faces get SimScale's default boundary - to be checked). Volume stays 0.8826 in^3 (= Run 11). Geometry `762944c1-5dee-4c3d-b232-cd4ffab2dd31`.
- Pipeline: `weld_union(mesh, simplify_tolerance)`; `config.SIMSCALE["AUTO_FLOW_SIMPLIFY_TOL"]=2e-3`; `GEOMETRY_FORMAT="STL"` now imports with facet split;
  `--auto-flow` (single / mesh-probe) now selects this route (the STEP route is too heavy: 19.6k faces, 0.61 CPU h at 10% meshed).
- Caveats to check before trusting Cd: (1) the 0.04 in sting rod in the wake (rim -> outlet) is not in the validated manual geometry; (2) its 5 unlabeled
  faces must act as no-slip walls. Compare Cd with the manual result 0.7327.
- Next (needs approval + cap): `mesh-probe --variant M --auto-flow --cap 1.0`, then `single --auto-flow --uz 6.148 --max-core-hours 5 --skip-estimate-check`.

## Update: sting-STL auto flow volume FAILS SimScale's mesh setup check (0 CPU h billed)
- `mesh-probe --variant M --auto-flow --cap 1.0` (user-approved 1 CPU h): import fine (fluid region, ~960 faces) but the mesh setup check rejects it:
  `missing-material-assignment` for entities **canopy (B7)** and **sting (B8)** - SimScale's sewing leaves the canopy block and the sting block as separate SOLID
  bodies next to the fluid region; the simulation must assign a material to every body. Incompressible only accepts fluid materials (`IncompressibleFluidMaterials`),
  and giving the slab/sting interiors air would be physically wrong (internal fluid regions behind the "canopy" wall faces). The manual web-app flow volume avoids this with
  "Delete bodies". Same finding as the old `stl_single` probes (logs/importprobe2: box and canopy arrive as separate solids).
- Billed: 0 (setup check fails before meshing). Total spent still about 20.3 of 800 CPU h. `--auto-flow` is now marked EXPERIMENTAL in `--help`.
- Summary of the automation attempts: STEP B-rep (single fluid body, 19.6k faces, too heavy: 0.61 CPU h at 10%), tessellated STEP (rejected), sting STL (extra solid bodies),
  all need either SimScale's CAD "Delete bodies" feature or a cheaper face structure. The validated semi-manual route remains the way to run designs.
- Remaining ideas: (1) ask SimScale support to enable the CAD-feature API (OpenAPI schemas `CadFeatureRequest`/`CadQueryRequest` exist with no paths);
  (2) try the STEP B-rep with a bigger MESH_SMALL_FEATURE_TOLERANCE_MM and longer timeout (expensive, unproven); (3) a human-in-the-loop flow volume per design (current).

## Update: first design batch + root cause of the mesh failures + sliver screen (supersedes earlier "next steps")
- Workflow built: `prepare-batch --n K` (free: propose K designs, screen, pre-split canopy import) -> user does the web-app flow-volume step per geometry ->
  `run-batch --batch ID --max-core-hours 5 --batch-core-hours 20 --skip-estimate-check` (billed). Canopy STL is imported **pre-split** (one named `solid pNNNN` block per
  welded piece; `CANOPY_PRESPLIT`): SimScale's 30-degree automatic facet split silently fails on many designs (whole canopy = 1 face), the pre-split import never does.
- Batch `batch_20261027_003945` (4 designs): **design_0005 (31 rings, 4 cuts) meshed and solved: Cd 0.7812** (baseline 0.7327), 0.12 + 2.78 CPU h. The other three
  (design_0003/0004/0006) failed in meshing with SimScale "self intersection" at 0 CPU h. Total spent about 23.2 of 800 CPU h. Ledger snapshot: `snapshot/`.
- **Root cause of those failures:** neighbouring ribbon slabs overlap by tiny slivers (pair overlap volume 1e-8 .. 1e-7 in^3 vs about 1e-5 normally), measured with
  manifold3d (`solid/screen.py`). Smallest pair overlap: baseline 1.2e-5 (meshed), design_0005 4.9e-6 (meshed), 0006 1.7e-7 / 0003 5.4e-8 / 0004 1.6e-8 (failed).
- **Screen:** `prepare-batch` now rejects designs with a pair overlap below `OPTIMIZER["MIN_PAIR_OVERLAP_IN3"]` = 1e-6 (calibrated on only 5 designs; re-check with more
  data). Of 30 proposed designs 13 pass (43%); designs with >= 9 cuts per ring nearly all fail, <= 6 nearly all pass, 7-8 mixed. So the searchable space is smaller than
  `SEARCH_SPACE` suggests until the canopy model avoids the slivers. Mesh-failed designs are excluded from the optimizer's training data (`Ledger.xy`).
- **Onshape retry of designs 01/02 (user request) did not fix it:** the exports (`runs/onshape/d01|d02`) keep 40-50 open fragment pieces each; SimScale then imports
  d01 as NOT a solid (sheet bodies left; raw STL import fails with unexpected-error) and d02 as a solid with leftover sheet bodies. Not a usable repair. 86 Onshape requests used.
- Jetson: `jetson/setup_jetson.sh` + `jetson/README.md` (untested on hardware). Restore state with `cp snapshot/ledger.json state/ && cp -r snapshot/batch runs/`.
- Auto-clicking the flow-volume step needs one recorded Playwright session from the user (`playwright codegen`, log in first with --save-storage, then record with --load-storage
  so the password is not recorded); not built yet. Asking SimScale support to enable the CAD-feature API would remove the step.

## Update: design_0007 diverged; solver-stabiliser options added (defaults unchanged)
- Batch `batch_20261027_035855` design 01 (23 rings, 6 cuts; design_0007): meshed fine (the sliver screen worked) but the SOLVER diverged at 93%
  (pressure -1e15 Pa in the air ~0.2 in below the rim, (1.84, -4.21, -7.86) in); 2.58 CPU h billed. It used the same numerics as the baseline.
- New opt-in flags (defaults off, so validated runs are unchanged): `single --robust-numerics`, `run-batch --robust-numerics` = potential-flow initialisation +
  pressure relaxation 0.2 + velocity relaxation 0.5 (`config.SIMSCALE` POTENTIAL_FOAM_INIT / RELAX_PRESSURE / RELAX_VELOCITY). Proposed test (needs user approval and a cap):
  re-run design_0007's geometry with `--robust-numerics`, then re-run the baseline with it to check Cd stays about 0.733. If divergence persists: finer mesh near the rim/wake.
- Solver divergences are now excluded from the optimizer's training data like mesh failures (`Ledger.xy`), instead of being scored Cd = 0.

## Update: second round of batches (4 designs) and two bugs found
Results (ledger `snapshot/ledger.json`, results in `runs/results.csv`):
- design_0009 (31 rings, 6 cuts): **Cd 0.7364** (ok, 3.24 CPU h).
- design_0007 (23 rings, 6 cuts): solver diverged (2.58 CPU h wasted).
- design_0008 (35 rings, 4 cuts): SimScale said FINISHED but the coefficients were garbage (**Cd = -1.8e6**): another diverged solve that the pipeline accepted as "ok" (3.17 CPU h).
  Invalidated in the ledger/record.json/CSV (status failed, raw numbers kept as `cd_raw`).
- design_0010 (26 rings, 8 cuts): failed with `math domain error` at 0 CPU h, BEFORE any run: the speed estimator scaled a neighbouring Cd, and the garbage negative Cd of
  design_0008 made `sqrt()` fail. It is not a design failure (its `y` was cleared); it still has to be run.
- Fixes: `runner.py` now rejects any Cd outside 0.05..4 as a diverged solve (`implausible Cd`, status failed); `Ledger.cfd_history` / `Ledger.xy` ignore implausible Cds and
  artifact failures ("meshing FAILED", "simulation run FAILED", "implausible Cd"). Test `test_implausible_cd_is_not_used_for_velocity_or_training`.
- Valid Cds so far: baseline 0.7327, design_0005 (31r/4c) 0.7812, design_0009 (31r/6c) 0.7364. All three converged designs have 30-31 rings (depth 10.6-10.7 in);
  both diverged ones have 23 and 35 rings (7.9 and 12.2 in). n = 5, a hint only: check whether depth/ring count is what the mesh settings are tuned for.
- Total billed so far about 29 CPU h of 800. Open questions: re-run design_0010 (needs approval + cap), the `--robust-numerics` experiment (needs approval + cap).

## Update: re-runs (design 10, robust-numerics test) - results and what they mean
- **design_0011 (= batch design 10, 26 rings, 8 cuts): Cd 0.7507** (ok, 3.56 CPU h). The earlier failure was my estimator bug, not the design.
- **design_0012 (= design_0007 geometry, 23r/6c) with `--robust-numerics` (potential-flow init, relaxation p 0.2 / U 0.5): diverged AGAIN**, earlier (62% vs 93%) and in a different
  place: velocity 1e7 m/s at (-1.74, 0.15, -1.16) in (r 1.75, near the apex) instead of pressure -1e15 at the rim (r 4.6). A moving fault points at poor mesh/scheme robustness for that design,
  not at one bad spot. 2.02 CPU h.
- **design_0013 = baseline with `--robust-numerics`: Cd 0.7537 vs 0.7327 with the validated numerics (+2.9%).** The stabilisers do NOT cure the divergence and DO shift the result. Do not use them
  for optimization runs; Cds from the two settings are not comparable. Differences between designs seen so far (0.73-0.78) are about the size of this numerical sensitivity: treat small Cd
  differences with caution (re-check promising designs, ideally with a finer mesh).
- Checked as divergence predictors (n = 6 designs, none works): pair-overlap slivers (they predict MESH failures, not divergence: the design that solved with the smallest overlaps is design_0011),
  narrowest slit between ribbons, overlap fraction, cut count. The only pattern is canopy depth: diverged 7.90 and 12.19 in; solved 8.97, 10.63, 10.76, 10.76 in. Hint only.
- Valid Cds (default numerics): baseline 0.7327, design_0005 (31r/4c) 0.7812, design_0009 (31r/6c) 0.7364, design_0011 (26r/8c) 0.7507. Diverged: 23r/6c, 35r/4c.
- **Spend corrected: about 41 of 800 CPU h** (ledger 24.7 + about 16.5 outside it: earlier sessions 15.3, canopy gate 0.31, mesh-probes 0.28 + 0.61). The earlier note "about 29" after round 2 was an undercount (about 32).
- `run-batch --redo NAME...` runs a design again. Ideas not yet tried: finer mesh (MESH_FINENESS) for diverging designs; first-order upwind for the default divergence scheme (more diffusive, will shift Cd);
  restricting the search to depth 9-11 in (about 27-32 rings) so far fewer designs diverge.

## Update: why does the solver diverge? (traces of failed runs, free)
SimScale keeps results of failed runs (`FORCE_COEFFICIENTS_PLOT`, `RESIDUALS_PLOT`). Cd traces (scratch analysis): every run, including the diverged ones, is normal for the first ~100 iterations
(Cd 0.68-0.75); then an error grows exponentially (about x1000 per 100 iterations; Cd -1.6 at it 200 -> -1654 at 300 -> -1e6 at 400): a numerical instability of the steady solve, onset at iteration
206 (23r/6c), 174 (23r/6c robust), 261 (35r/4c). The residual plot is useless here (normalised by the blown-up field). Converged runs differ in late Cd wobble: baseline 0.5%, 31r/6c 0.2%,
26r/8c 0.6%, **31r/4c 2.7%** (marginal), baseline with robust numerics 4.5% (and an early overshoot to Cd 0.94 at it 100 - the potential-flow start hurts). Both 4-cut designs are the least stable
(one marginal, one diverged), which suggests large open passages -> intrinsically unsteady separation that a steady solver cannot settle, but that is a hypothesis.
- The momentum flux already uses first-order bounded upwind. All other transported quantities (k, omega, ...) use the unlimited second-order `for_default` scheme (linearUpwindV): the prime suspect.
- New opt-in flag `--stable-scheme` (`FIRST_ORDER_DEFAULT_SCHEME`): first-order upwind for `for_default`. More diffusive; shifts Cd, so it needs a baseline re-anchor. Proposed test (needs approval and caps):
  design_0007 geometry, design_0008 geometry and the baseline, each with `--stable-scheme`, 5 CPU h cap each.
- `docs/simscale_support_request.md`: draft message asking SimScale to enable the CAD-feature API for API keys (removes the manual flow-volume step, the real limit on data volume).

## Update: stable-scheme test on design 7 FAILED at the SAME spot -> the divergence is a MESH problem, not the numerics
- `--stable-scheme` (first-order upwind for k/omega; SimScale's API refuses first-order for `for_default`, accepts it per quantity): design_0017 (= design 7 geometry) diverged again:
  pressure -1.05e15 Pa at **(0.04682, -0.1069, -0.1996) m, the identical position and magnitude of the original failure** (design_0007: -1.02e15 at the same point), blow-up onset iteration 209
  (206 before), 2.19 CPU h billed. Same mesh each time (same geometry) -> a fixed bad cell/region in the volume mesh, ~0.22 in from the nearest canopy piece (in the air below the rim).
- Only the potential-flow-init + low-relaxation run moved the failure (to r 1.75 in, velocity blow-up): the mesh has more than one weak spot; numerics only change which is hit first.
- So: numerics options (`--robust-numerics`, `--stable-scheme`) are not the fix; keep the validated defaults. Remaining ideas, mesh side: higher `MESH_FINENESS`, boundary layers off, small-feature tolerance,
  or jittering the design (e.g. depth +-0.05 in) so the mesher produces different cells (cheap retry for diverged designs).
- design_0018 (35r/4c, stable scheme) and a baseline stable-scheme run were still queued/running when this was written.

## Update: stable-scheme test cancelled; mesh options added
- User cancelled the remaining stable-scheme runs: design_0018 (35r/4c, stable scheme) was stopped at 0% solver progress (0.41 CPU h billed, status `cancelled`, closed in the ledger);
  the queued baseline stable-scheme run never started. Total spent about 43.5 of 800 CPU h (design_0017 2.3, design_0018 0.4 on top of 41.2).
- New opt-in mesh flags for `single` and `run-batch`: `--mesh-fineness X` (default 4.5, Simmetrix 0-10) and `--no-boundary-layers`. Defaults unchanged.
- Next (needs user approval + cap): re-run the design 7 geometry (`run-batch --batch batch_20261027_035855 --redo batch_20261027_035855_01 --mesh-fineness 6 ...`). A finer mesh costs more:
  expect roughly 1.5-2x the cells, so about 4-6 CPU h for the solver; suggest a cap of 8. If it converges, finer meshes for every design halve the number of designs the budget buys.

## Update: fineness-6 run of design 7 FINISHED but is NOT CONVERGED (and the plausibility check was too weak)
- `run-batch --redo batch_20261027_035855_01 --mesh-fineness 6 --max-core-hours 8`: design_0019 (23r/6c): 2,311,622 cells (1.7x the 1,358,927 at fineness 4.5), 0.20 + 4.39 = 4.6 CPU h,
  SimScale status FINISHED, reported Cd 1.1875, Cl 0.042, Cm -0.12. **But the Cd trace never settled: 0.71 (it 100) -> 0.75 (200) -> 0.84 (300) -> 1.00 (400) -> 1.27 (500), +27% over the last
  100 iterations, noise 3.9%.** The same instability as the diverged runs, in slow motion: the finer mesh only delayed the blow-up, it did not remove it. The reported number is not a result.
- The old check (Cd in 0.05..4) accepted it. New check in `simscale/runner.py`: reject runs with Cd noise > 1% or drift > 1% (mean of last 10% vs the previous 10%) as `not converged`
  (`results.py` adds `cd_noise_rel`, `cd_drift_rel`). Calibration: baseline 0.04% / +0.12%, 31r/4c 0.04% / +0.14%, 31r/6c 0.03% / +0.11%, 26r/8c 0.05% / +0.16%, baseline robust 0.02% / +0.03%
  (all accepted); design_0019 3.89% / +11.3% (rejected). design_0019 invalidated in ledger/record/CSV (status failed, raw numbers kept). Test `test_convergence_measures_...`.
- Reading: the flow around this canopy (and the 35r/4c one) is not steady enough for the steady RANS solve: coarse mesh -> blow-up at the same cell, finer mesh -> slower drift, other numerics -> blow-up elsewhere;
  Cl and Cm grow too (asymmetric wake). Both unsteady designs are the extremes of depth (7.9 and 12.2 in); all five converged runs are 8.97-10.76 in deep (26-31 rings). Hypothesis: the wake of shallow/deep
  canopies is intrinsically unsteady; fixing it needs a transient (URANS) run or a much longer run (unknown cost, likely 3x+).
- Total spent about 48 of 800 CPU h. Valid Cds (default numerics, fineness 4.5): baseline 0.7327, 31r/4c 0.7812, 31r/6c 0.7364, 26r/8c 0.7507.

## Update: design 7 at 1400 iterations, fineness 6 (design_0020): RUNAWAY, no valid Cd
- 2.3M-cell mesh, 1400 iterations, 0.21 + 11.35 = 11.6 CPU h. SimScale: FINISHED; the pipeline rejected it (`implausible Cd 33.2`). The trace is a clean exponential from iteration ~300:
  Cd 0.84 (300), 1.00 (400), 1.27 (500), 1.76, 2.56, 3.81, 5.69 (800), 8.5, 12.9, 19.3 (1200), 28.9, 43.2 (1400); about x1.5 per 100 iterations, Cl grows too (4.1 at the end). No plateau: the earlier
  "Cd about 1.19, 60% above the baseline" reading was a point on a runaway curve, NOT a result. Design 7 (23r/6c, 7.9 in deep) cannot be simulated with this steady solve. Do not spend more on it.
- Total spent about 59.6 of 800 CPU h; about 27 CPU h of that (45%) went into runaway/diverged solves (design_0007, 0008, 0012, 0017, 0018, 0019, 0020).
- The same traces are available mid-run (`FORCE_COEFFICIENTS_PLOT` of a RUNNING run returns the iterations so far), so runaways can be cancelled early (see below).

## Update: runaway guard (cancels diverging solves early)
- `results.runaway_reason(cd)` + `SimScaleClient.runaway_check(...)`: while a solve runs, the drag history so far is polled every 90 s (`RUNAWAY_GUARD`); the run is cancelled if Cd leaves -0.2..3 or
  rises more than 8% over the last 100 iterations (from iteration 300). On the 10 recorded traces no converged run is flagged and all runaways are flagged between iteration 70 and 300
  (design_0020 would have stopped at 300 of 1400, saving about 9 of its 11.6 CPU h). Checked live against finished runs through the real API (converged -> None, runaways -> reason);
  not yet exercised inside a live run. Error text `diverging: ...` is excluded from the optimizer's training data like the other artifact failures.
- Test `test_runaway_guard_flags_exponential_growth_but_not_a_settling_run`. All 53 tests pass.
- Recommended next steps: keep the validated defaults (500 iterations, fineness 4.5); restrict the search to the depths that converge (about 27-32 rings, 9.3-11.1 in) or accept ~1/3 failures,
  now cheap thanks to the guard; then more screened batches (`prepare-batch`, flow volumes by hand, `run-batch`). Valid Cds so far: baseline 0.7327, 31r/4c 0.7812, 31r/6c 0.7364, 26r/8c 0.7507.

## Update: search restricted to 27-32 rings (user decision)
- `config.SEARCH_SPACE` NUM_RINGS 16-36 -> **27-32** (9.3-11.1 in deep; all converged solves were 9.0-10.8 in, the unsimulable ones 7.9 and 12.2 in). `optimizer.space.restrict` keeps results outside
  the space (e.g. design_0011, 26 rings, Cd 0.7507) out of skopt's training data (skopt rejects points outside its bounds); they stay in the ledger.
- Sliver screen pass rate in the new space: 12 of 30 candidates (40%); cuts per ring decides it: >= 9 reject almost always, <= 6 pass almost always, 7-8 mixed. Narrowing CUTS_PER_RING to 4-8 would
  lift the pass rate to about 75% and stop the optimizer proposing regions that can never be run (not done; user decision).

## Update: batch_20261028_032516 (first batch in the 27-32 ring band) and the first live use of the runaway guard
- design_0023 (29 rings, 6 cuts, exp 1.30, overlap 0.38, inner 0.74): **Cd 0.7863** (ok, 2.93 CPU h) - best valid result so far.
- design_0021 (29r/5c): the runaway guard cancelled it at Cd -62.6 (1.40 CPU h instead of about 3). design_0024 (30r/4c): cancelled at Cd 10.98 (2.21 CPU h). The guard works live.
- design_0022 (32r/7c): passed the sliver screen but SimScale's mesher failed with a self-intersection NEAR THE APEX (r about 0.24 in, z -0.14 in), i.e. in the centre cap region,
  which the pair-overlap screen does not look at. 0 CPU h.
- Total spent about 66.8 of 800 CPU h (ledger 50.3 + about 16.5 outside it).
- Valid Cds (default numerics, fineness 4.5, 500 iterations): baseline 0.7327 (30r/8c), 26r/8c 0.7507, 31r/6c 0.7364, 31r/4c 0.7812, 29r/6c 0.7863.
- Convergence is NOT predicted by any single geometric measure (checked on 9 designs: depth, ring spacing, overlap and cut angle at the rim, gaps, radius ratio). By cuts per ring:
  6 cuts 2/2 converged in the 27-32 band (31r, 29r; the 23r one was outside the band), 8 cuts 2/2 (baseline, 26r), 4 cuts 1/3 (31r ok; 35r, 30r ran away), 5 cuts 0/1. Small numbers, but
  cuts 6-8 look reliable and the sliver screen passes 6-cut designs almost always, so restricting CUTS_PER_RING to 6-8 is the obvious next narrowing (not done; user decision).
- Cost model with the guard: a converging design about 3 CPU h, a runaway about 1.4-2.6 CPU h (was 3-11).

## Update: Google Sheet ("Kirigami CFD runs", tab `runs`) cleaned up and given a settings column
- Every finished run is pushed to the sheet automatically (`RunLogger`; credentials in the environment). Found and fixed (user approved): design_0008 and design_0019 were still `ok` with Cd -1,843,280 and 1.1875
  (invalidated locally earlier) - now `failed`, Cd/Cl/Cm cleared, reason in `error`. Backup of the sheet before the edit: scratchpad `sheet_backup_before_edit.csv` (not in the repo).
- New column `solver_settings` (e.g. "fineness 4.5; 500 it; default numerics"): back-filled for this session's 23 rows (rows 11+; rows 2-10 are an earlier session whose ids repeat design_0001-0005 and are left as they were), design_0012/0013
  = "potential-flow init; relax p 0.2 U 0.5", 0017 = "upwind k/omega", 0019 = "fineness 6", 0020 = "fineness 6; 1400 it", 0014-0016 = never ran. From now on `pipeline._row` fills it from `simscale.spec.describe_settings`.
- Only compare Cds with the same `solver_settings`.

## Update: can cuts 9-12 be made simulatable? (user: "restrict cuts to 4-12 if possible") - no, not with the present 3D model
- `SEARCH_SPACE` CUTS_PER_RING is already 4-12 (unchanged). Sliver screen pass rate by cuts (random designs, 27-32 rings): 6 cuts nearly all, 7-8 about half, 9 about 1/6, 10-12 0/18.
- Tried to rescue 9-12 cuts: narrower ribbons (KERF_FACTOR 0.85 -> 0.72) does not remove the slivers; the largest inner radius (1.25 in) does not either (9 cuts 1/6, 10-12 cuts 0/6).
  Slivers are ribbon-ribbon (and hinge-ribbon) wedges of 1e-8..1e-7 in^3 in the crowded inner rings (r 0.6-1.2 in), an artifact of building the canopy from overlapping thin slabs.
  A real fix needs a different canopy construction (single clean fused solid / smooth CAD, cf. `solid/smooth_cad.py`), not a parameter tweak.
- Open decision for the user: also restrict CUTS_PER_RING to 6-8? Caveat: design_0005 (31r/4c, Cd 0.7812, 2nd best) has 4 cuts; 4-5 cut designs ran away 2 of 3 times but are good when they converge.

## Update: search space is now rings 27-32, cuts 4-8 (user decision: keep 4 cuts in)
- `SEARCH_SPACE`: NUM_RINGS 27-32, CUTS_PER_RING **4-8** (was 4-12), RING_SPACING_EXPONENT 0.6-1.5, RING_OVERLAP_FRACTION 0.2-1.0, INNER_RADIUS 0.5-1.25. This is the region we can simulate, NOT a proven location of the optimum:
  designs outside it (9+ cuts, other ring counts) cannot be meshed / converged with the present canopy model, so they are unexplored.
- Size: 6 ring counts x 5 cut counts = 30 discrete combinations x 3 continuous parameters (about 43,000 grid points at steps of 0.1 / 0.1 / 0.05); the optimizer needs 50-100 evaluations; the budget allows
  about 150-200 valid simulations (about 730 CPU h left at about 3.5 CPU h per valid result including failures).
- The valid results so far all lie inside this region except design_0011 (26 rings), which is kept in the ledger but out of the optimizer's training data.

## Update: batch_20261028_192829 (rings 27-32, cuts 4-8) - 2 valid, 2 mesh failures that passed the sliver screen
- design_0025 (27r/4c, exp 1.14, ov 0.52, inner 0.57): **Cd 0.7457** (2.71 CPU h). design_0028 (32r/8c, exp 1.42, ov 0.84, inner 0.60): **Cd 0.7316** (3.89 CPU h) - converged despite the high overlap fraction (so "high overlap = unstable" was wrong).
- design_0026 (30r/6c, inner 0.69) and design_0027 (30r/6c, inner 1.16): SimScale meshing FAILED (self-intersection) at 0 CPU h although both passed the sliver screen. Positions: design_0026 r 0.74 in z -0.28; design_0027 r 0.21 z -0.13;
  with design_0022 (r 0.24, z -0.14) that makes 3 of 12 screen-passing designs failing the mesher, all within r < 0.75 in of the apex (centre cap / first ring), which the pair-overlap screen does not model.
  Local test (distance between non-adjacent cap triangles, 10 designs): no difference between failing and working designs (all about the sheet thickness, 0.0147 in), so no cheap local predictor found.
- Idea (needs approval + cap): a mesher pre-gate. `canopy-gate --geometry-id <canopy-only geometry from prepare-batch>` fails in seconds at 0 CPU h when the mesher rejects the surface; a pass would be cancelled at 30% progress
  (about 0.05-0.3 CPU h). Run inside `prepare-batch` it would spare the user the manual flow-volume step for designs that cannot be meshed. Test first on designs 25/28 (ok) vs 26/27 (fail).
- Valid Cds so far (default numerics, fineness 4.5, 500 it): 0.7863 (29r/6c), 0.7812 (31r/4c), 0.7507 (26r/8c), 0.7457 (27r/4c), 0.7364 (31r/6c), 0.7327 (baseline 30r/8c), 0.7316 (32r/8c). All within 7.5%.
- Total spent about 73.4 of 800 CPU h.

## Update: results sheet reorganised (user request: make the designs that went through easy to find among the failed ones)
- Raw log (tab `runs`) is untouched apart from: new column **`outcome`** right after `status` (valid / mesh failed / runaway (stopped early) / solver diverged / not converged / never ran (setup error) /
  refused (over the CPU cap) / cancelled by user / earlier session), frozen header + id columns, a filter, valid rows green and bold, all other rows grey text.
- New tab **`results`**: valid designs ranked by Cd with predicted fall speed, parameters, depth, Cd x area, solver settings, CPU hours; valid runs with other solver settings listed below (not comparable).
  Rebuilt from the ledger after every logged run (`pipeline.evaluate_design`, best effort) and on demand with `python main.py sync-results`. Code: `sheets/results.py` (`outcome_of`, `results_table`, `refresh_results_tab`).
- Backup of the `runs` tab before this clean-up: scratchpad `sheet_backup_before_cleanup.csv` (outside the repo). The `outcome` column is filled by `pipeline._row` for new rows. 58 tests pass.

## Update: why designs fail the mesher, and the clean generator (EXTRUDE_METHOD=clean)
- **Root cause found.** The legacy canopy thickens every strip along node-averaged vertex normals. Strips only touch at nodes (never along an edge), so those normals
  sit up to 87 degrees off a ribbon at hinge/diagonal junctions: slabs thin to slivers or fold through themselves (14-18 of 529 pieces self-intersect in design_0026).
  `solid/selfcheck.py` finds crossing triangles locally: design_0026's welded canopy has 4 crossings at r 0.75 in, z -0.29 in, exactly where the mesher failed (r 0.74, z -0.28).
  SimScale's STEP import also rejects that geometry (`fault-face-bad_face_face`).
- **designs 0027 / 0022 fail only on the pre-split STL route** (SimScale's own union of the pieces). Through our exact `weld_union` + STEP air volume (`mesh-test`, no manual
  flow-volume step) design_0027, 0025 and 0028 all PASS the mesher (2.29 / 1.71 / 1.48 CPU h to 30% progress; costs are billed, cap 3).
- **Clean generator**: each ribbon is thickened along its OWN normals, ribbon ends overlap 0.01 in so the slabs fuse into one body, then needle edges (< 1e-3 in) and sliver
  triangles (height < 5e-4 in) are merged and the result is re-checked (1 body, watertight, no crossing triangles; `CLEAN_SIMPLIFY_TOL` 0.002 keeps the face count low).
  Ribbon layout is unchanged (same `build_open_strips`); thickness is a true 0.015 in everywhere, so volume is about 5% higher than the thinned legacy weld (0.729 vs 0.696 in^3 for design_0026).
  Cds from clean and legacy geometry are therefore NOT directly comparable; re-run a known design (e.g. design_0023) on clean before mixing them.
- **design_0026 on clean: mesher PASS** (2.22 CPU h to 30%). Steps that did not work: raw union (SimScale `fault-edge-invalid`, edges to 1e-7 in), edge collapse only (mesher: element size too
  close to CAD tolerance), more than ~20k B-rep faces (SimScale then drops the box-wall names, so keep the STEP below that).
- Commands (free): `python main.py build-clean --design-id design_0026 [--step]`; mesh rig (billed, cap per design): `python main.py mesh-test --design-id design_0026 --set EXTRUDE_METHOD=clean --no-improve`;
  free geometry-acceptance check: add `--import-only`.
- Not done: running the solver on clean geometry, wiring clean + STEP into prepare-batch/run-batch (would remove the manual flow-volume step), OpenCascade fuse route (prototype ran 15+ min on design_0022 with no result).

## Update: first solver attempt on clean geometry - the STEP route is too expensive to mesh (2.02 CPU h billed, no Cd)
- `single --from-design design_0023 --clean-step --uz 5.187 --max-core-hours 5` (user-approved, 5 CPU h) = design_0029: STEP air volume imported fine, boxes named, meshing passed 30%
  but the pipeline's mesh cap (`MESH_MAX_CORE_HOURS` 2.0) cancelled it at 2.02 CPU h billed. Failed with no Cd. The mesh of the legacy semi-manual route for the same design cost 0.12 CPU h,
  so the automatic STEP route costs 20x or more per mesh (one CAD face per triangle: 24k faces). design_0030 (0026 clean) was stopped before any SimScale call (0 billed).
- STL auto route (clean 0026, `mesh-test --route stl`): rejected by the mesh setup check (`missing-material-assignment`, canopy and sting arrive as extra bodies), same as in the earlier auto-flow probes.
- New commands (free to build, billed to run): `single --from-design D --clean-step`, `optimize --clean-step` (the optimizer is trained only on clean-geometry designs; `Ledger.geometry_method`).
- Open decision: (A) raise `MESH_MAX_CORE_HOURS` (about 6+ CPU h per mesh estimated, 10 CPU h per design) and run clean designs fully automatically, or (B) use the clean canopy with the semi-manual flow volume (mesh about 0.1-0.3 CPU h, needs the web-app clicks; untested for clean canopies).

## Update: the clean generator is now the default canopy build
- `config.FIXED_PARAMS["EXTRUDE_METHOD"] = "clean"` (and the user-style CANOPY_OVERRIDES): every new design (prepare-batch, single, optimize) is built with own-normal ribbons, 0.01 in end overlap,
  `CLEAN_SIMPLIFY_TOL` 0.002 in, then needle edges (< 1e-3 in) and slivers (height < 1.5e-3 in) are merged cluster by cluster, undoing any merge that would create a crossing triangle. The legacy build is still
  available (`EXTRUDE_METHOD=legacy`). `Ledger.geometry_method` is set from the config, so only results built the same way train the optimizer (the 28 legacy records stay in the ledger and sheet but no longer count).
- Manual flow-volume route: the clean canopy is written as ONE closed STL block (full precision). Splitting it into open smooth patches crashes SimScale's importer (`simgeo-crashed`) and the float32 binary STL
  is not used. `mesh-test --route manual --import-only` (free) imports it the way prepare-batch does: **design_0026, 0027, 0022 and 0023 all IMPORT OK** (one solid, one face; 0026/0027/0022 failed before).
  The sliver screen is skipped for clean canopies (no slab overlaps). Unknown until a flow volume is made and meshed: how much the meshed one-face canopy costs (legacy semi-manual mesh about 0.12 CPU h).
- Needs `rtree` (added to requirements.txt) for the local crossing check; `fast-simplification` was tried and dropped (it breaks watertightness).

## Update: batch_20261029_013921 = design_0023 and design_0026 on the clean canopy (prepared, waiting for the flow-volume clicks)
- `python main.py prepare-batch --from-design design_0023 design_0026` (new option): re-prepares ledger designs with the current canopy method and the inlet speed they first ran at
  (5.187 and 5.1808 m/s, stored as `uz_override` in batch.json, honoured by run-batch), so only the geometry differs from the legacy runs (legacy Cd of 0023: 0.7863; 0026 never ran).
- Canopies imported (one solid, one face each): `batch_20261029_013921_01_canopy` (design_0023 shape) and `_02_canopy` (design_0026 shape). Checklist: `runs/batch/batch_20261029_013921/MANUAL_STEPS.txt`.
- Approved (user): 5 CPU h cap per design. After the clicks: `python main.py run-batch --batch batch_20261029_013921 --max-core-hours 5 --batch-core-hours 10 --skip-estimate-check`.

## Update: clean-geometry batch (batch_20261029_013921): both solver runs DIVERGED; the mesh was cheap, the clean canopy does not cure solver divergence
- Mesh on the semi-manual route: design_0030 (= design_0023 shape, clean, Uz 5.187): **0.096 CPU h** (legacy 0.12), meshing finished. design_0031 (= design_0026 shape, clean, Uz 5.181): also meshed cheaply. So the manual route handles the clean one-face canopy.
- design_0030: runaway guard cancelled the steady solve at Cd -254 (1.05 CPU h solver + 0.10 mesh). The LEGACY build of this very design converged to Cd 0.7863, so the clean build changes the mesh enough to land on a different weak spot.
- design_0031: solver FAILED right at the start (9%): "velocity field started diverging to 1.2e7 m/s near [0.01945, -0.01721, -0.01102] m" = about 1 in from the apex, in the cap region. 0.1 CPU h mesh, ~0 solver.
- Total billed this round about 1.25 CPU h (cap was 5 each). Both records are failed, so they do not train the optimizer.
- Reading: the clean generator removes the MESHER failures (0022/0026/0027 now import and mesh) but the steady-solver divergence (about half of all designs) is a separate, still open mesh-quality problem; here it hits the apex cap region of a design that had no apex problem before.
- Ideas (need approval + caps): finer mesh or boundary layers off for the cap region; re-run 0030/0031 with `--mesh-fineness 6`; or unify both builds' apex (e.g. coarser cap fan / cap_overlap) since both failures are within 1 in of the apex.

## Update: step B (sliver-free clean canopy) done; batch_20261029_192800 prepared; step A (finer mesh) follows
- Hypothesis from the data: the clean build left a few sliver triangles (height 3.5e-4..1.4e-3 in) where a merge would have pushed a triangle through a neighbour; for design_0026 they sat at r 0.80 in and
  r 1.04 in, and the solver blew up at r 1.02 in. `collapse_short_edges` now retries a blocked merge at the cluster's end vertices instead of the midpoint before giving up: design_0026 clean has 3 slivers
  left (was 21), design_0027 and design_0023 none. Not yet proven to affect divergence (design_0030 = 0023 clean ran away with no slivers left to blame).
- batch_20261029_192800: design_0023 shape (`_01`) and design_0026 shape (`_02`) on the sliver-free canopy, inlet speeds as before (5.187 / 5.1808 m/s). Waiting for the flow-volume clicks; then
  `run-batch --batch batch_20261029_192800 --max-core-hours 5 --batch-core-hours 10 --skip-estimate-check`. Step A (user approved option C): any design of it that still diverges is re-run with `--redo ... --mesh-fineness 6`
  on the same flow volumes (no new clicks), 5 CPU h cap.

## Update: sliver-free clean canopy and fineness 6 BOTH still diverge (options B and C of the clean-geometry plan failed)
- batch_20261029_192800 (sliver-free clean canopy, fineness 4.5): design_0032 (0023 shape) solver FAILED after 1.02 CPU h, velocity 1.2e8 m/s near r 1.59 in, z -1.26 in; design_0033 (0026 shape) runaway guard
  cancelled at Cd 3927 (0.83 CPU h). Fineness 6 re-runs on the same flow volumes: design_0034 (0023 shape) cancelled at Cd 92123 (2.75 CPU h of solver, 0.12 mesh); design_0035 (0026 shape) FAILED at the start,
  velocity 1.1e7 m/s near r 2.30 in, z -2.38 in (0.12 CPU h). Total this round about 5.0 CPU h; clean-geometry attempts so far about 6.3 CPU h; none produced a Cd.
- Where it diverges (exports/divergence_clean_runs.png, made by `tools_divergence_plot.py`): always in the open ribbon web of rings 1-2.5 in from the axis, on a different ribbon each run
  (r 1.02 / 1.59 / 1.85 / 2.30 in), never on the apex cap. Finer mesh and slivers removed: it just moves. So it is neither the apex nor a specific bad triangle.
- Reading: the clean generator fixes the MESHER failures only. The steady-state solver diverges for the thin-ribbon web whatever we do to the surface; legacy designs that converged did so by luck of the mesh.
  Remaining ideas: transient (pseudo-time) or lower-CFL solver settings, a coarser/steadier physics (laminar-ish / lower Re), a lower inlet speed, or SimScale support.

## Update: generator rewritten from scratch - EXTRUDE_METHOD "lattice" (solid/lattice_canopy.py) is the default
- First attempt was a "dome" sheet (plan view lifted onto the paraboloid). User review: layers looked thick, the rim was pointed instead of flat, gaps were not hexagons. Reason: the real web is a
  honeycomb of RADIALLY wide fins (hinge bands flat, diagonal ribbons steep); its hexagons only exist in 3D, the plan view is not the shape. Dome dropped (code removed). The "clean" fused-slab build was also dropped (superseded).
- What the error data said (mesher self-intersections, slit faces, `fault-edge-invalid`, divergence on a different ribbon every run, never at the apex): every old build fused hundreds of slabs by a boolean
  along node-averaged normals (up to 87 deg off the ribbon), leaving crossing triangles, needle edges (1e-7 in), slivers (1e-9 in) and a 30-wedge fan at the apex.
- Lattice build, SAME pattern as the legacy (`build_open_strips`: flat hinge bands, steep diagonal ribbons, hexagon cells, flat rim band, user-style flush hinges):
  1. each ribbon thickened along ITS OWN normals, ending exactly at its node edges (uniform 0.015 in sheet);
  2. an octagonal PIN along every node edge (radius 0.9 x thickness, sticks out 0.3 x thickness past the ribbon edges: <= 0.006 in proud of the sheet): both ribbon ends lie inside it, so ribbons fuse through material, not a hair contact;
  3. centre cap = smooth bell, flat at the apex and flat at the rim (no fan, no point), 1.5 x thickness so it never shares a face plane with the first ring; depth/diameter unchanged (10.41 in / 9.58 in for design_0026, legacy 10.40 / 9.58);
  4. exact union (manifold3d), then triangles lower than 1.5e-3 in and edges shorter than 1e-3 in are merged (a merge that would cross a neighbour is retried at the cluster's end vertices, then skipped) and
     remaining flat slivers are re-triangulated in place by edge flips (`booleans.flip_slivers`); the build RAISES if a triangle thinner than 4e-4 in or any crossing / second body is left, retrying with slightly larger pins (1.0/0.5, 1.2/1.0) first.
- Local results: designs 0026, 0027, 0022, 0023, 0025, 0028, 0005, 0009 all build: 1 body, watertight, 0 crossing triangles, shortest edge 1.5e-3 in, 28-54k triangles (old builds: 3.5e-4 in sliver heights, crossings, 1e-7 in edges).
  SimScale manual-route import: design_0026 and 0027 OK in about 2 min each (one solid). Volume about 0.75-0.95 in^3 (legacy weld 0.7-0.9: legacy was thinned by the bad normals).
- Joints: flush up to the pins; the pins are the only deviation from the legacy look (exports/joint_closeup.png). A hair-thin flush contact is exactly what produced the slivers, so a joint is always a block of material.
- Cds from the lattice build are NOT comparable with legacy results (different thickness/joints); only lattice results train the optimizer (`Ledger.geometry_method`, set from config). The 28 legacy records stay in the ledger and sheet.
- Prepared (free): batch_20261029_224604 = design_0023 shape (`_01`) and design_0026 shape (`_02`), inlet speeds as before; waiting for the flow-volume clicks and an approved CPU cap. Earlier batches 013921, 192800, 211430, 220106 are obsolete.
- Not yet shown: that the lattice build converges in the solver (earlier solver runs on the "clean" build diverged for both designs; divergence is also common with legacy). That is the test.

## Update: lattice build, first solver runs (batch_20261029_224604): BOTH diverged and were cancelled by the runaway guard
- design_0036 (0023 shape, Uz 5.187): cancelled at Cd 8e14 after 1.52 CPU h solver + 0.12 mesh; SimScale reported the blow-up near r 2.97 in, z -3.98 in (a ribbon in the web, not the apex).
- design_0037 (0026 shape, Uz 5.181): cancelled at Cd 824 after 1.30 CPU h solver + 0.13 mesh; no position reported. SimScale's solver estimate for these meshes was 13 core hours (cap 5, gate skipped on request).
- Total this round about 3.1 CPU h. Neither produced a Cd; both records are failed (they do not train the optimizer).
- Reading: seven geometry variants of the same two designs (legacy, clean, sliver-free clean, fineness 6, lattice) now all diverge in the steady solve although the old legacy build of design_0023 converged once (Cd 0.7863).
  The mesher problem is fixed; the steady-solver divergence is NOT a geometry-cleanliness problem. Next ideas are solver-side (transient / pseudo-transient start, lower CFL or relaxation, bounded schemes, lower inlet speed) or SimScale support.

## Update: options A (gentle solver) and B (low inlet speed) both diverged; the mesh-quality data points at the canopy IMPORT, not the geometry or the solver
- A: design_0038 (0023 lattice, flow volume of batch_20261029_224604, `run-batch --gentle`: potential-flow start, relax p 0.2 / U 0.5 / k,omega 0.5, upwind k/omega): ran away at Cd 625 after 1.29 CPU h (+0.11 mesh).
- B: design_0039 (same geometry, default numerics, `run-batch --uz 2.5`): velocity blow-up 2e7 m/s near r 3.99 in, z -7.1 in, Cd 620, 1.25 CPU h (+0.11 mesh). Divergence does NOT follow numerics or inlet speed.
- Mesh log data (tools_mesh_quality.py -> runs/mesh_quality.json, plotted in the atlas): tetrahedra with non-orthogonality > 88 deg: converged legacy runs 1-81 (prisms 45k-109k); diverged legacy 32-80; every clean/lattice run 190-1082
  with only 22-47k prisms (boundary-layer cells). Those meshes were built from the canopy imported as ONE face (38k triangles, no feature edges); legacy meshes came from ~700-950 faces (pre-split pieces).
- New: `MESH_MAX_BAD_TETS` 250 (config.SIMSCALE): the runner reads the mesher log and refuses to start the solver on a worse mesh (saves 1-3 CPU h per doomed run). `LATTICE_PIECES` True: the lattice canopy is written as its
  closed ribbons/pins/cap blocks (`write_pieces_stl`) so SimScale gets ~900 faces again (design_0026 import: 1 solid, 902 faces). Free import check: `mesh-test --route manual --import-only --pieces`.
- Prepared (free): batch_20261030_021503 = design_0023 shape (`_01`, 872 faces) and design_0026 shape (`_02`, 902 faces) with the piece-block import. Waiting for the flow-volume clicks. The run starts with the mesh and stops there
  if the bad-cell gate trips (about 0.1 CPU h), otherwise it solves (cap 5 CPU h each, user-approved style).
- Atlas: exports/geometry_error_atlas.pdf (tools_error_map.py -> tools_error_atlas.py): every run, shape, build, outcome and failure position.

## Update: piece-block import fixes the divergence-prone mesh only partly, and makes the mesh 4x bigger (batch_20261030_021503)
- design_0040 (0023 shape) and design_0041 (0026 shape): mesh quality gate tripped (583 and 376 nearly-flat tetrahedra, limit 250; one-face import had 1082 / 955; legacy converged runs <= 81), solver not started, 0.1 CPU h each.
- Gate lifted (`--max-bad-tets 1000`): design_0042 (0023 shape) did NOT diverge, but the 5 CPU h cap cut it (4.61 CPU h solver billed + 0.87 mesh, progress still reported 0%). The piece-block mesh is 7.3 M cells
  (5.95 M tets, 0.59 M pyramids, 415 k prism layers, 0.31 M hexes) against 1.7 M for the legacy runs, so SimScale's solver estimate is 13.65 CPU h. Design_0043 (0026) was stopped before any SimScale call (0 billed) and its pending entry cleared.
- Reading: with face structure the mesher refines around every feature edge, pin and merged sliver (about 900 faces plus 1.5e-3 in features), giving a 4x larger mesh; the solver did not blow up in 4.6 CPU h, which is the first time a clean-geometry run survived that long, but it cannot finish inside 5 CPU h.
- Options: (1) raise the cap to about 15 CPU h and re-run design_0042's geometry (`run-batch --redo ... --max-bad-tets 1000 --max-core-hours 15`); (2) coarser mesh (`--mesh-fineness 3`) so cells drop towards the legacy 1.7 M; (3) both together on one design first.

## Update: FIRST VALID Cd on the new (lattice, piece-block) build: design_0043 = design_0023 shape, Cd 0.6907 (partial run, 400 of 500 iterations)
- `run-batch --redo ..._01 --max-bad-tets 1000 --max-core-hours 15` (user-approved 15 CPU h): the solver did NOT diverge, progress reached 77% and the CPU cap cancelled it. SimScale billed **17.46 CPU h** in total (0.93 mesh + 16.5 solver):
  2.5 CPU h over the 15 cap because the run keeps billing while the cancel propagates. Now `RUN_CAP_SAFETY` 0.85 cancels earlier.
- The drag history (runs/design_0043_cd_trace_partial.csv, 400 iterations) had settled: Cd 0.6893 (it 300), 0.6904 (350), 0.6909 (400); last-10% noise 0.02%, drift 0.05%. New: a run stopped by the cap is kept if it has >= 300 iterations and passes the same
  plausibility/noise/drift limits as a finished run (`SimScaleClient.partial_result`, also automatic inside run_design); `main.py salvage-run --design-id design_0043` applied it to this run (ledger status ok, note "partial run", sheet row and results tab updated).
- Comparison: legacy build of the same design: Cd 0.7863 (1.7 M cells). The lattice build: 0.6907 (7.3 M cells), -12%: thicker uniform ribbons, pins and a different mesh; builds are NOT comparable (only lattice results train the optimizer).
- design_0044 (0026 shape, fineness 3, cap 5): the fineness change barely shrank the mesh (6.5 M cells vs 7.3 M; mesh size is driven by the ~900 face boundaries and features, not the fineness slider), SimScale's estimate was again 13.65 CPU h, so the run was
  cancelled right after it started (solver 0.00 CPU h, mesh 0.86 CPU h); no ledger record.
- Cost note: each lattice design needs about 15-17 CPU h with the piece-block import. At 800 CPU h that is about 40 designs. Options to cut it: a coarser canopy (bigger MIN edge / fewer pins), or a one-face import (cheaper but its mesh diverged).
