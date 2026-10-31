# Review of the kirigami Cd project

Reviewer notes, 2026-10-31. Nothing billed was run. Everything below comes from the ledger, `runs/*.json`, the CSV trace of design_0043, and two canopies I rebuilt locally
(design_0023, legacy and lattice). The scripts that produce the numbers are in `review/` (`ledger_checks.py`, `facet_stats.py`, `plan_view_solidity.py`).

## The short version

1. The CFD is probably **not predicting your real parachute**. At the CFD Cd the canopy would take about 1.7 s to fall your 4.9 m drop; your drop test took 1.32 s. The CFD drag is about 2.5x what the physical canopy produced.
2. The legacy Cds (0.73-0.79) and the one lattice Cd (0.69) differ by **12% on the same design**. That is bigger than the whole spread between designs (7.5%). Nobody has yet checked whether that is geometry or mesh. Until that is known, no ranking is trustworthy.
3. The optimizer currently has **one** training point (design_0043). It is not doing Bayesian optimisation; it is random sampling at 16 CPU h a sample.
4. The expensive part of the lattice build is very likely the tiny pin facets, and the experiment waiting for your clicks (flush pins) makes those facets **smaller**, so I expect it to fail. Don't spend your clicks on it.
5. The legacy build gives about 4.4 CPU h per valid design in the 27-32 ring band, the lattice about 17. That is 4x more data per CPU hour.

## 1. Where things are going wrong (ranked)

### 1. The CFD level disagrees with your own drop test by 2.5x (confidence: high that the gap exists; medium on the cause)
- Mass 23.8 g (20 g payload + canopy), weight 0.233 N. At the inlet speed the pipeline uses (5.19 m/s) the CFD drag is 0.60 N (Cd 0.786) or 0.53 N (lattice, Cd 0.69). Drag 2.3-2.6x the weight means this canopy would not fall at 5.2 m/s; it would settle near 3.2-3.5 m/s.
- Using the pipeline's own `velocity/estimator.py`: Cd 0.69 -> fall time 1.67 s; Cd 0.786 -> 1.75 s. Measured: 1.322 s (`config.VELOCITY.DROP_TEST_SEED`). Free fall alone would be 1.00 s.
- The "seed Cd 0.307" that sets every inlet speed comes from that one drop. It is a weak number: the drop is mostly acceleration, so +-0.05 s in timing moves Cd between 0.285 and 0.334 (about +-8%). It was also a different design (24 rings, overlap 10), outside the search band.
- Possible causes (I can't separate them from the files): the real canopy does not open into the modelled shape (the code's own comment says so, `velocity/estimator.py`); steady RANS over-predicts drag on this bluff body; the depth model is wrong (see 7); the drop timing is off.
- Why it matters: if the physical canopy behaves 2.5x differently from the model, the "best CFD design" may not be the best real design. Only a physical check can tell.

### 2. The legacy-vs-lattice 12% gap is unexplained, and it decides which results to trust (high)
- design_0023: legacy Cd 0.7863 (1.4 M cells, 58k boundary-layer prisms); lattice Cd 0.6907 (7.3 M cells, 415k prisms). Both settle cleanly (`design_0043_cd_trace_partial.csv`).
- The docs say the builds differ in thickness. I don't find that: median piece thickness is the same in both (rms 0.0075 in, i.e. 0.015 in), surface area is the same (111.7 vs 111.1 in^2). What does differ is the mesh (5x more cells, 7x more boundary-layer prisms) and the pins/cap.
- So the gap is either "the legacy mesh is too coarse and over-predicts Cd" or "the pins/cap really lower Cd". The first would mean all eight legacy Cds are mesh-limited. The cheapest test is in the recommended next step.
- There is no mesh-independence study at all (same design, two mesh sizes, same build). That is the standard first check in CFD and it is missing.

### 3. The optimizer is not optimising (high)
- `Ledger.geometry_method = "lattice"` filters the GP's training data to lattice results. Only design_0043 qualifies (`review/ledger_checks.py`). The eight legacy Cds train nothing; the baseline pair 0002/0013 has `x = null` so it would never train anyway.
- `N_INITIAL_POINTS = 20` means the first 20 samples are space-filling random anyway. At 16-17 CPU h that is 330 CPU h before the model ever sees data.
- Failures are dropped from training (`ARTIFACT_ERRORS`), so the optimizer keeps proposing designs in regions where the mesher fails. About half of the proposals in the band fail.

### 4. The 7.5% spread between designs is not resolved by the data (high)
- Seven distinct shapes have a Cd (0002 and 0013 are the same shape). Their Cds: 0.732, 0.736, 0.743 (mean of the pair), 0.746, 0.751, 0.781, 0.786. Standard deviation 2.9%.
- The only repeat on an identical mesh (0002 vs 0013, same 1,337,562 tets) differs by 2.9% (0.7327 vs 0.7537) just from solver start-up settings. A truly converged steady solution should not depend on that, so "flat in the last 10% of iterations" (window std 0.04%) is not proof of convergence. One pair is a small sample, but nobody has measured the noise better.
- The "best" design 0023 (0.786) is 0.6% above design 0005 (0.781). Not distinguishable.
- A simple predictor from the geometry (share of the plan-view disk blocked by ribbons) correlates with Cd (r = -0.73, n = 7: less blocked area, higher Cd) but its leave-one-out error is 2.7%, no better than guessing the mean (2.9%). It is a hint, not a result. (`review/plan_view_solidity.py`)

### 5. The 5x mesh cost: the pin facets are the best suspect, and the pending experiment goes the wrong way (medium-high)
- My facet statistics for the piece-block STLs of design_0023 (`review/facet_stats.py`):

| | legacy | lattice |
|---|---|---|
| pieces/faces in the import | 511 | 871 |
| triangles | 13.2 k | 30.2 k |
| shortest edge | 0.015 in | 0.0031 in |
| 1st percentile edge | 0.015 in | 0.0069 in |
| edges shorter than 0.01 in | 0% | 25% |

- The face count is about the same (SimScale reports ~950 for the legacy import and ~880 for the lattice), so "number of faces" is not the driver. The size of the smallest facets is: a quarter of the lattice edges are under 0.01 in, and the legacy build has none.
- Those short edges are the pin octagons: octagon edge = 0.765 x pin radius. Pin radius 0.9 t gave 0.0103 in edges; the "flush pins" change (0.6 t) gives 0.0069 in edges. **Smaller pin, smaller facets, so I expect the same or a bigger mesh**, not a smaller one. The pending batch `batch_20261031_164247` is therefore likely to repeat the 7 M-cell result.
- The earlier "small feature tolerance has no effect" test used 0.1 mm. The pin edges are 0.0069 in = 0.175 mm. The tolerance was below the feature size, so the test could not have worked. "Mesh settings do not help" is only established for the fineness slider and the physics toggles.
- Boundary layers off removes ~30% of cells (7.26 M -> 5.15 M), which leaves ~5 M cells that come from the geometry.

### 6. Hidden coupling: ring count is really a depth setting, and the depth is a guess (medium-high)
- With `VERTICAL_DROP = None`, depth = 0.3582 x (rings - 1), fitted on 4 measurements that all have 8 cuts, leave-one-out error 1.27 in (`config.DEPTH_MODEL`). The whole 27-32 ring band spans only 1.8 in of depth.
- 6 of the 9 valid Cds, including the "best" design 0023, are on an extrapolated depth (`depth_extrapolated: true`, cuts never measured). So the search over rings is partly a search over an uncertain depth, and the real printed canopy for 4-7 cuts may hang differently.

### 7. Smaller problems
- Inlet speed: 0002 and 0013 ran at 6.148 m/s, the other six at 5.19 m/s (+18%). The "anchored" velocity mode also makes each design's inlet speed depend on neighbouring results (in practice the ratio was 1.000, so it is harmless here, but it breaks reproducibility).
- Cd definition: consistent. Each run uses its own speed and its own projected area (71.9-73.5 in^2, +-1%), so within a build, Cd and drag force rank the same. Run 11 used a different area (50.27 in^2, Cd 1.087), so don't compare against that number without converting.
- SimScale's pre-run estimate was 5x too high for legacy (18.66 estimated, 3.5 used) and too low for lattice (13.65 estimated, 16.5 used). Skipping the gate was reasonable for legacy; for the lattice it would have predicted the cost correctly.

## 2. Questions from the brief, answered directly

**Is steady RANS appropriate?** For ranking close variants of one shape, it is a standard first choice, and the Cd traces are smooth and symmetric (Cl ~ 1e-4), so it settles. But it resolves differences of a few percent poorly, and the 12% build gap shows the answer still moves with the mesh. A transient solve would not cure the divergence (that follows the mesh, not the time scheme) and would cost several times more per design. Not recommended as a screening tool. At most use it later on the top two or three.

**Is it the thickness?** Partly, indirectly. The legacy shortest edge is exactly the thickness (0.015 in). The lattice edges are half that or less because of the pins. A sheet twice as thick would double every one of those feature sizes. My estimate (not measured) is 2-4x fewer cells. It changes the model, so Cd will shift; the user said the print thickness doesn't matter but the simulated one does. This needs one 1 CPU h mesh-check to know.

**Why 5x more cells on the lattice?** See 1.5: tiny pin facets (25% of edges under 0.01 in) and a cap with 0.003 in edges. Cheapest construction idea (untested): no pins; extend each ribbon end by at least one thickness into its neighbour so joins are blocks of material; cap as a flat disc one thickness thick; target: no edge below 0.015 in.

**Is the search sensible?** Not at the current cost per sample (see 1.3 and 1.4). A defensible "best design" claim needs: (a) a mesh-independence check on one design, (b) the noise level from 3-4 repeats, (c) the winner beating the runner-up by more than about 3x that noise, or a physical drop test confirming it.

## 3. Simpler or cheaper options (at most three)

### Option A: screen with the legacy build, confirm a few (recommended)
- Cost: about 100 CPU h for ~40 legacy attempts (the band gave 26.5 CPU h for 12 shapes = 2.2 CPU h per attempt, ~50% give a Cd), giving ~18 new Cds on top of the current 6 in the band. Plus ~13 CPU h for 4 repeats to measure noise. Then optionally the top 4 on the lattice build: 4 x 17 = 68 CPU h. Total ~110-185 CPU h of the ~700 left.
- Free first step: download the stored Cd traces of legacy runs (e.g. 0023) from SimScale and check Cd at 350 vs 500 iterations (design_0043's trace says 350 is within 0.1%, 300 within 0.2%). If the legacy traces agree, 350 iterations saves ~30% of the solve.
- Risks: the legacy mesh may be too coarse (decided by the recommended step); legacy fails the mesher or diverges for half the designs, so you only learn about the other half through the lattice.
- You do: the flow-volume clicks for each batch (~8-10 designs per sitting), and approve caps (3-5 CPU h per legacy design).

### Option B: make the lattice cheap, then use it for everything
- Cost: two mesh-only checks (~1 CPU h each): (1) pins/cap rebuilt so no edge is under 0.015 in; (2) the same at 0.03 in sheet thickness. Proceed only if a design meshes to 2.5 M cells or fewer (about 5 CPU h per design, ~100 designs in 500 CPU h).
- Risks: needs code changes in `solid/lattice_canopy.py` that I have not made; may still diverge on the one-body import; a thicker sheet changes Cd (acceptable only if you agree).
- You do: flow-volume clicks for each test geometry (2), approve ~2.5 CPU h.

### Option C: let your drop tests decide
- Cost: 0 CPU h. Print the top 3-5 shapes (the `export-print` command exists), drop each 10 times, compare. Also gives the one number the project lacks: how far the CFD is from reality (finding 1).
- Risks: print thickness and stiffness differ from the model; timing noise (use video).
- You do: print and drop.
- This can run in parallel with A or B.

## 4. Recommended next step (one sentence to approve)

> "Run design_0023 on its saved legacy flow volume at mesh fineness 6, 500 iterations, with a cap of 8 CPU h (skip SimScale's estimate gate), and don't run the flush-pin batch."

Why this one: it answers the question that decides everything (do the legacy Cds hold up on a finer mesh, or do they drift toward the lattice 0.69?). The legacy mesh at fineness 6 was about 2.2 M cells with a 4.6 CPU h run in earlier tests (design_0019), so 8 CPU h should be enough. It should need no new clicks because the flow volume is already saved; if SimScale no longer has it, I'll tell you rather than ask for clicks blindly. If Cd stays within about 2% of 0.786, Option A is justified and the lattice offset is geometry; if it moves toward 0.69, the legacy numbers need a finer mesh and the budget plan changes.

## 5. Claims in the existing docs I could not verify or think are wrong

1. "Divergence follows the mesh, not the geometry" (AGENT_HANDOFF 4): within the legacy runs it is only a partial fit (more flat tets predicts divergence with AUC 0.85; one converged run had 81 flat tets, diverged ones had 32-80). Shapes that diverged did so under every setting (the 23-ring shape: 6 attempts). Shape and mesh are confounded. What is solid: the same two shapes (0023, 0026) diverged on all 10 one-face clean/lattice runs, while 0023 converged on legacy and on the lattice piece-block import.
2. "Mesh settings did not change the cost" (AGENT_HANDOFF 6): the small-feature test (0.1 mm) was below the pin edge size (0.175 mm), so it proves nothing.
3. "Surface area and thickness equal the legacy canopy's": the area matches (111.7 vs 111.1 in^2) and the median piece thickness matches; I found no sign of thinner legacy ribbons. "Builds differ by thickness" is therefore an unproven explanation for the 12% Cd gap. Also note the legacy STL is not watertight and has 1,595 crossing triangle pairs, so its quoted volume (0.544 in^3) is not meaningful.
4. "The legacy build fails the mesher for about 1 in 4 designs" (config comment): 6 of 17 distinct legacy shapes (35%) failed the mesher and 4 more diverged. Only 7 of 17 gave a Cd. In the 27-32 band: 6 of 12.
5. "About 97 CPU h billed": the ledger sums to 96.7, but the mesh-only tests (about four at 0.8-0.9 CPU h, plus design_0044's 0.86) are not ledger records, so the real total is probably 100-101. Check SimScale's usage page.
6. "Flush pins may cut the mesh" (AGENT_HANDOFF open decision): see 1.5; I expect the opposite.
7. "Numerics alone shifted Cd by about 3%": confirmed for one pair on an identical mesh (+2.9%), n = 1.
8. design_0043 "settled": confirmed from the trace (drift 0.05% over iterations 350-400). The 400-iteration result is fine to use.
9. `runs/results.csv` (local mirror) still shows design_0043 as `failed` and has no row for design_0018; the ledger is correct.
10. The seed Cd 0.307 (every inlet speed rests on it) is from a single drop of a different design; its uncertainty is about +-8%.

## 6. Process: where CPU hours and time were lost

| What | CPU h | Comment |
|---|---|---|
| 23-ring/6-cut shape, 6 attempts (0007, 0012, 0014, 0017, 0019, 0020) | 23.1 | No Cd. 0020 alone burned 11.6 h (1400 iterations, fineness 6) on a shape that had already crashed 3 times. The runaway guard came afterwards. |
| "clean" build, 7 runs | 8.3 | All diverged; two were at fineness 6 with SimScale's own estimate at 45 CPU h. |
| Lattice one-face import, 4 runs (0036-0039) | 5.8 | All diverged; numerics and inlet speed were varied before checking mesh quality. |
| design_0042 cut by a 5 CPU h cap | 5.5 | SimScale's estimate was 13.65 before the run. Re-run as 0043 cost another 18.4 (cap overshoot of 2.5 h). One lattice Cd cost about 24 CPU h. |
| Mesh-only diagnostics that returned identical cell counts | ~4 (not in the ledger) | Including the tolerance test below the feature size. |

Free checks that should have come first:
1. A facet-size histogram of both builds (took me minutes locally); it predicts the mesh-size gap.
2. Reading SimScale's own estimate before the 5 CPU h runs, and checking it against the 13.65 the lattice then used.
3. Treating "same shape, many settings, always diverges" as shape-bound before spending a fourth and fifth run on it.
4. One change at a time on the same saved flow volume, with the change size checked against the feature size.
5. A mesh-independence check before building a 150-sample optimizer on top of the first working run.
6. Comparing the first CFD force against your drop-test weight balance (0.53-0.60 N vs 0.233 N), which needs no CPU at all.
