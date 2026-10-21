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
