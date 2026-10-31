# Brief for a reviewing agent: find where this project is going wrong, or find a simpler way

You are NOT continuing the work. Your job is to read what has been done, challenge it, and tell the user (a) where things are going wrong and (b) what simpler or cheaper approach would get them the answer they want. Be sceptical, including of the previous agent's conclusions.

## What the user wants
The kirigami parachute design (a hexagonal honeycomb of ribbons on a paraboloid dome, ring/cut pattern from `generator`) with the highest drag coefficient Cd. Search space: 27-32 rings, 4-8 cuts per ring, ring spacing exponent, ring overlap fraction, inner radius.
They want accurate data, a lot of it, and they say cost is secondary - but the budget is 800 SimScale CPU hours (about 97 used) and every billed run needs their explicit approval with a CPU cap.

## Read first (in this order)
1. `AGENT_HANDOFF.md` - current state, findings, rules, commands.
2. `exports/geometry_error_atlas.pdf` - every run, shape, build and where each failed (regenerate: `python tools_error_map.py && python tools_mesh_quality.py && python tools_error_atlas.py`).
3. `HANDOFF.md` - the full chronological log, newest at the bottom. Skim the experiment headings, then read the last ~10 sections closely.
4. `runs/error_map.json`, `runs/mesh_quality.json`, `state/ledger.json` (records: params, outcome, simscale CPU hours, settings), `runs/design_0043_cd_trace_partial.csv`.
5. Code: `solid/canopy.py`, `solid/lattice_canopy.py`, `pipeline.py`, `simscale/runner.py`, `simscale/spec.py`, `optimizer/batch.py`, `config.py`.

## The story in six lines (verify each claim yourself)
- The legacy build converged for 8 of 16 comparable runs (Cd 0.73-0.79) and failed the SimScale mesher for about 1 in 4 designs.
- The rebuild ("lattice") fixed mesher failures but every run on a one-face import diverged; importing ~900 separate faces fixed divergence and gave the first converged run (Cd 0.6907) - at 7.3 M cells and 15-17 CPU h per design versus 1.4 M cells and about 3 CPU h for the legacy build of the same design.
- Mesh settings did not change the cost; the user's valid Cds so far span only 0.73-0.79 (7.5%), numerics changes alone shifted Cd by about 3%.
- A lot of effort went into geometry cleanliness; the divergence correlates with mesh quality (flat cells), not with the geometry's validity.
- The last open experiment (flush pins -> smaller mesh?) is waiting for the user's manual flow-volume clicks.
- Each design needs a manual web-app flow-volume step by the user.

## Questions I want you to answer (with evidence from the files, not opinion)
1. **Is the physics set up right?** Inlet speed per design comes from `velocity.py` (a mass/Cd/terminal-velocity estimate); Cd uses each design's projected area; k-omega SST steady with a fixed 500 iterations. Is a steady RANS solve even appropriate for a thin-ribbon porous bluff body (massive separation)? Would a transient or a different turbulence model make divergence disappear and give more trustworthy Cd? Is the Cd definition (reference area, speed) consistent across designs?
2. **Why does the same design cost 5x more cells on the new canopy?** The previous agent suspects the joint pins and the cap. Check it: compare the legacy and lattice STL/piece files (facet size distribution, number and size of faces SimScale reports), and decide what the cheapest geometry that keeps the hexagon look is. Is there a much simpler construction (e.g. fewer faces, larger thickness, no pins, different overlap)?
3. **Is the thickness (0.015 in) the real driver of everything?** Thin sheet means tiny cells, bad tets, divergence. What would a 2x thicker sheet do to mesh size, convergence and Cd? Would the user accept that (they said the printed model's thickness does not matter much, but the simulated one affects Cd)?
4. **Is the search itself sensible?** Cds differ by 7.5% across designs; noise from numerics is about 3%; half the runs fail. Is Bayesian optimisation over 5 parameters with this noise and failure rate worth 16 CPU h per sample? Would a coarse screen (fewer iterations, coarser mesh, or an analytic/porosity model) plus a few high-fidelity runs be better? What would a defensible 'best design' claim need?
5. **Simplest route to usable data.** Given everything: is the best plan simply 'use the legacy build wherever it meshes and converges (about 3 CPU h each), use the lattice build only for designs it cannot mesh, and compare only within a build'? Or reduce iterations to 350? Or run a handful of designs at full fidelity and fit a model? Give a concrete plan with CPU-hour numbers.
6. **Process problems.** Where did the previous agent waste CPU hours or time (e.g. diagnostics that produced identical cell counts, runs cut by a cap before they could finish, a 2.5 CPU h cap overshoot)? What checks should have been done for free first?
7. **Anything the user would be surprised by.** Wrong assumptions, hidden coupling, unverified claims in HANDOFF.md, results mixed across builds, ledger records that should not train the optimizer, etc.

## Constraints
- Do NOT start billed SimScale runs, mesh checks or mesh tests. If you need one to answer a question, say which, why, and the CPU-hour cap, and ask the user.
- Free things are fine: reading files, `python main.py build-clean`, `mesh-test ... --import-only`, analysis scripts, local checks, the atlas tools.
- Never print or commit keys. Do not open a PR. Commit analysis files you create to branch `claude/kirigami-parachute-pipeline-t24c2i` (a stop hook requires a clean tree) and push.
- Warning from the last agent's mistakes: `pkill -f` with a pattern that appears in your own command kills your own shell; foreground `sleep` is blocked (poll with `timeout N bash -c 'until ...; do sleep 15; done'`).

## Deliverable
One file `REVIEW.md` in `kirigami-optimizer/` (and a 10-line summary in chat), containing:
1. **Where things are going wrong** - ranked, each with the evidence (file, number, run id) and how sure you are.
2. **Simpler or cheaper options** - at most three, each with expected CPU hours, what it risks, and what the user must do (clicks, approvals).
3. **Your recommended next step**, written so the user can approve it in one sentence.
4. **Claims in the existing docs you could not verify or think are wrong.**
Keep it plain-spoken; the user is not a CFD specialist.
