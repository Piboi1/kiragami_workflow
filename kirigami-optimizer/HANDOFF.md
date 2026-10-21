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
