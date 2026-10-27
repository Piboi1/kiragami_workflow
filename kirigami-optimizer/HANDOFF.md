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
