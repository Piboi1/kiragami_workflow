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

## 7. Addendum: the approved test ran (design_0044 = design_0023 at fineness 6)

Run `batch_20261028_032516_03` redone on its saved legacy flow volume. Same shape, same inlet speed (5.187 m/s), same 500 iterations; only the mesh fineness changed (4.5 -> 6). Record: `review/design_0044_fineness6_record.json`.

| | fineness 4.5 (design_0023) | fineness 6 (design_0044) | lattice, 7.3 M cells (design_0043) |
|---|---|---|---|
| Cd | 0.7863 | **0.7111** | 0.6907 |
| cells | ~1.4 M | ~2.35 M (1.73 M tets, 0.53 M hexes, 83 k prisms) | 7.3 M |
| flat tets (> 88 deg) | 1 | 0 | 583 |
| CPU h | 2.9 | 6.4 (0.17 mesh + 6.25 solve) | 17.5 |
| settling | drift 0.12% | noise 0.03%, drift 0.08% | drift 0.07% |

What it means:
- **The legacy Cds are mesh-dependent by about 10%.** A 1.7x finer mesh moved Cd by -9.6%, with no divergence and a cleaner mesh (0 flat tets). The solver was well settled in both, so "settled" does not mean "mesh-converged".
- **That explains most of the 12% legacy-vs-lattice gap** (finding 2). On a finer mesh the legacy build lands within 2.9% of the lattice value (0.711 vs 0.691), which is about the numerics noise. The "builds differ in thickness/pins" explanation is not needed; I now think the lattice and legacy builds give nearly the same physics and the legacy 4.5 mesh was too coarse. (Medium confidence: one design, one refinement step.)
- **0.711 is probably not mesh-converged either**: going 4.5 -> 6 changed Cd by 10%. A third level (fineness 7.5, roughly 3.5 M cells, estimate 10-12 CPU h) would show whether it levels off near 0.70.
- **The ranking of the eight legacy designs is now unproven.** If the mesh error is a similar +10% for every design, the order holds; if it depends on shape (denser or finer-ribbon designs lose more), it may not. This was the open risk in Option A.
- **Cost:** a legacy design at fineness 6 is ~6.4 CPU h versus ~17 for the lattice, so it is still ~2.7x cheaper per design, but about twice the 3 CPU h assumed in Option A. Total ledger now ~103 CPU h.

Updated recommendation (replaces section 4):
> "Run design_0028 (the lowest legacy Cd, 0.7316) at fineness 6, same settings, cap 8 CPU h."

If 0028 drops by about the same ~10% (to ~0.66), the legacy ordering survives mesh refinement and Option A should be done at fineness 6 (about 6.4 CPU h per design: roughly 25 shapes for ~200 CPU h). If it drops by a clearly different amount, the Cd values are not rankable at this mesh, and the better plan is Option B (fix the lattice cost) or Option C (physical drops). Do not run the 4.5-fineness screening of Option A as written.

## 8. Addendum: design_0028 at fineness 6 (design_0045) - the ranking does not survive

Same procedure as section 7 (saved legacy flow volume, inlet 5.186 m/s, 500 iterations, cap 8 CPU h). It ran all 500 iterations and billed 7.79 CPU h (0.44 mesh + 7.35 solve). Record: `review/design_0045_fineness6_record.json`.

| design | Cd at fineness 4.5 | Cd at fineness 6 | change | cells 4.5 -> 6 |
|---|---|---|---|---|
| 0023 (29 rings, 6 cuts) | 0.7863 | 0.7111 | **-9.6%** | 1.40 M -> 2.35 M |
| 0028 (32 rings, 8 cuts) | 0.7316 | 0.7021 | **-4.0%** | 1.69 M -> 2.69 M |
| gap between them | 0.055 (7.5%) | **0.009 (1.3%)** | | |

- Both runs settled cleanly (drift 0.06-0.08%), 0-5 flat tets, no divergence.
- The mesh error is **shape dependent** (10% for one design, 4% for the other). So the pre-agreed test came out the "different amount" way: the legacy fineness-4.5 Cds cannot be ranked.
- On the finer mesh the two designs, which were the best and the worst legacy results, differ by 1.3%, below the ~3% numerics noise. The 7.5% design-to-design spread that the whole search rests on is mostly a coarse-mesh effect. After refinement, differences between designs may be 1-2% or less. This is two designs only, so I'd call it likely, not proven.
- The lattice value for 0023 (0.691, 7.3 M cells) sits close to the legacy fineness-6 value (0.711), which fits the picture of Cds drifting down toward a common level near 0.69-0.70 as the mesh gets finer.

Updated recommendation (replaces sections 4 and 7's):
1. **Stop spending CPU hours on screening.** At the mesh sizes affordable (6-7 CPU h per design) CFD does not separate these designs, and the finer meshes that might cost 15+ CPU h each, so Option A is dead and Option B only helps if differences are bigger than the mesh noise.
2. **Go to Option C**: print designs 0023 and 0028 (the old best and worst) plus one or two very different shapes, and drop-test them. That tests, for free, whether the design space matters at all, and it addresses the 2.5x CFD-vs-drop-test gap (finding 1).
3. If you still want one more CFD data point: design_0023 at fineness 7.5 (my estimate 10-12 CPU h, cap 14) to see whether Cd levels off near 0.70. I would not run it before the drop tests.
Ledger total is now about 111 CPU h of the 800.

## 9. Addendum: the 10-design screening batch at fineness 6 (batch_20261101_012513)

Legacy build, fineness 6, 500 iterations, cap 8 CPU h per design. Data: `review/fineness6_results.csv`. The batch itself used about 52 CPU h; the ledger total is now about 164 CPU h of 800.

| design | rings | cuts | exp | overlap | r_in | Cd | CPU h |
|---|---|---|---|---|---|---|---|
| 0049 | 29 | 5 | 1.30 | 0.38 | 0.74 | **0.7312** | 6.6 |
| 0044 (= 0023) | 29 | 6 | 1.30 | 0.38 | 0.74 | 0.7111 | 6.4 |
| 0052 | 28 | 5 | 1.21 | 0.62 | 0.95 | 0.7053 | 7.5 |
| 0050 | 30 | 7 | 1.43 | 0.56 | 0.72 | 0.7040 | 6.9 |
| 0045 (= 0028) | 32 | 8 | 1.42 | 0.84 | 0.60 | 0.7021 | 7.8 |
| 0053 | 28 | 5 | 1.00 | 0.80 | 0.51 | 0.6961 | 6.2 |
| 0047 | 32 | 6 | 1.49 | 0.92 | 1.09 | 0.6895 | 7.2 |
| 0046 | 29 | 4 | 1.41 | 0.72 | 0.67 | 0.6878 | 6.2 |
| 0055 | 32 | 7 | 1.16 | 0.37 | 1.25 | 0.6837 | 6.8 |
| 0048, 0051 | | | | | | mesher failed | 0 |
| 0054 | 30 | 5 | 1.09 | 0.99 | 0.55 | solver diverged | 5.5 |

- **Yield:** 9 of 12 attempts gave a Cd (75%), at 6.2-7.8 CPU h each. The sliver screen removes most mesher failures before they cost anything; the two that got through cost 0, the one divergence cost 5.5 CPU h.
- **Spread:** 0.684-0.731 (6.8% from lowest to highest, standard deviation 2.1%). This passes the pre-agreed test only narrowly: it is driven by one pair. Seven of the nine sit in a 0.684-0.711 band that is inside the ~3% numerics noise, so those seven cannot be ranked.
- **The one signal:** the two highest results (0049: 0.731 and 0044: 0.711) are neighbours. They share rings 29, exponent 1.30, overlap 0.38, inner radius 0.74 and differ only in cuts (5 vs 6). A neighbouring pair both coming out on top is more convincing than one lucky run. Low overlap (0.37-0.38) is the common feature of the top pair, but design 0055 also has 0.37 overlap and is the lowest, so overlap alone does not explain it.
- **Parameter trends** (n = 9, weak): fewer rings, lower overlap and smaller inner radius each correlate with higher Cd (r about -0.4); cuts and spacing exponent show nothing. Nine points cannot support more than hints.
- **Caveats unchanged:** the CFD level is still about 2.5x the drop-test drag (finding 1), and one run per design means the 3% numerics noise is not averaged out. 0049's Cd drift was 0.3% (the others 0.02-0.09%), still acceptable.

Conclusion: a search is justified but should be local and cautious. The GP now has 9 same-fineness training points. Note that `propose_points` still uses `N_INITIAL_POINTS = 20`, so it would keep proposing space-filling points until 20 results exist; lower it to about 9 to get model-driven proposals.

## 10. Addendum: second screening batch (model-driven) and the combined fineness-6 picture

Batch `batch_20261101_163242`: 10 designs proposed by the GP from the first nine results (`N_INITIAL_POINTS` 9, so proposals were model-driven). Seven gave a Cd, three failed (two mesher failures and one solver crash: 0058, 3.8 CPU h). Batch cost 45.1 CPU h. Ledger total about 209 CPU h of 800. Data: `review/fineness6_results.csv`.

| design | rings | cuts | exp | overlap | r_in | Cd |
|---|---|---|---|---|---|---|
| 0057 | 28 | 4 | 1.19 | 0.20 | 0.69 | 0.7154 |
| 0064 | 29 | 8 | 1.27 | 1.00 | 0.53 | 0.7096 |
| 0061 | 27 | 5 | 1.14 | 0.48 | 0.69 | 0.7081 |
| 0060 | 27 | 4 | 0.87 | 0.51 | 0.50 | 0.7003 |
| 0062 | 28 | 4 | 0.93 | 1.00 | 0.72 | 0.6999 |
| 0063 | 27 | 4 | 1.08 | 0.27 | 1.18 | 0.6958 |
| 0065 | 28 | 4 | 1.12 | 0.94 | 1.11 | 0.6867 |

All 16 valid fineness-6 results together: mean 0.7017, standard deviation 1.7%, range 0.684-0.731.

- **The search found nothing better.** No model-proposed design beat design_0049 (0.7312). The best new one is 0.7154. The model-driven batch landed in the same 0.69-0.71 band as the space-filling one.
- **The design space is nearly flat in this model.** 15 of 16 results lie within 0.684-0.715, a 4.5% range, and the run-to-run numerics noise measured earlier is ~3%. Parameter correlations with Cd are weak (inner radius r = -0.47, overlap r = -0.36, rings r = -0.25, cuts and exponent about 0). The overlap-0.20 and overlap-1.00 designs score alike (0.715 and 0.710), which contradicts a simple "less overlap is better" story.
- **Only 0049 stands out** (+4.2% above the mean, about 2.4 standard deviations), and it has the largest Cd drift of the set (0.3% vs 0.02-0.11%). Its neighbour 0044 (same shape with 6 cuts) is only +1.3% above the mean, so the "neighbouring pair on top" signal from section 9 was weaker than I said. 0049 is a candidate, not a result, until it is repeated.
- **Cost of finding this out:** 112 CPU h at fineness 6 for 16 valid Cds. More batches of the same kind would most likely return more numbers in the same band.

Recommended next steps (cheapest first):
1. **Repeat 0049** with a different numerics path (e.g. potential-flow init, `--robust-numerics`, cap 8 CPU h). If it lands near 0.70 it was noise; if it stays near 0.73 it is a real outlier. About 7 CPU h.
2. **Drop-test three printed designs** (0049, 0057, and a typical one such as 0065). 0 CPU h; this is the only check that says whether the CFD differences mean anything on the real canopy, and it addresses the 2.5x drag gap from finding 1.
3. Only if 1 and 2 show real differences: another model-driven batch (about 55 CPU h per 10 designs).

## 11. Addendum: repeat of design_0049 with robust numerics (design_0066)

Same shape, same saved flow volume, same mesh fineness (6) and inlet speed (5.171 m/s); only the solver start changed (potential-flow initialisation, relaxation p 0.2 / U 0.5). 6.4 CPU h; ledger total about 215 CPU h.

| run | numerics | Cd | last-10% noise | drift |
|---|---|---|---|---|
| design_0049 | default | 0.7312 | 0.08% | +0.30% |
| design_0066 | robust | **0.7392** | 0.16% | -0.16% |

- **0049 is a real outlier in this CFD model.** The repeat agrees within 1.1% and is slightly higher. Both values are 4-5% above the mean of the other 15 designs (0.700), while those 15 have a standard deviation of about 1.4%. The large drift in the first run did not mean it was unconverged.
- **The robust numerics push Cd up.** The same switch raised design_0013 vs design_0002 by 2.9% on an identical mesh (section 2) and 0049 by 1.1% here. So robust-numerics Cds must not be compared with default-numerics ones; compare 0049 default (0.7312) with the population, which still puts it on top. Bookkeeping note: the ledger filter only checks fineness, so design_0066 now also trains the optimizer as a second point at the same x; it is harmless for a proposal batch but should be excluded from any quoted statistics.
- **What it still does not tell us:** whether the real canopy behaves like this (finding 1: the CFD drag is ~2.5x the drop-test drag), and what in the shape makes it better. Its closest neighbour (0044, same shape with 6 cuts) is only +1.3% above the mean, so the effect may be tied to 5 cuts with this ring/overlap/inner-radius combination, or may be a narrow sweet spot.

Next step options:
1. **Drop-test 0049 against 0057 and 0065** (0 CPU h). Tells whether the CFD ranking means anything physically.
2. **A small local batch around 0049** (about 6 designs, ~40 CPU h): vary overlap, exponent and inner radius by modest steps at 5 cuts, to see if the high Cd is a sweet spot or a plateau.

## 12. Addendum: first physical drop tests of the bottom two sheet rows (2026-11-03)

Source: the "geo testing" tab of the team spreadsheet (5 trials each, 4.937 m drop). Equivalent Cd comes from the pipeline's own fall-from-rest model (23.8 g, 75 in^2 reference).

| design | avg time | std | equivalent Cd |
|---|---|---|---|
| 24-10 (24 rings, overlap 10) | 1.322 s | 0.049 | 0.31 |
| 4cpr 31 r (new) | 1.154 s | 0.101 | 0.15 |
| 6 cpr 24 r (new) | 1.144 s | 0.090 | 0.14 |
| 24-14 (24 rings, overlap 14) | 1.150 s | 0.023 | 0.14 |
| CFD (Cd 0.70-0.74) would give | 1.69-1.73 s | | 0.70-0.74 |

- The new pair differ by **0.010 s** (not 0.001 s). With 5 trials each the standard error of the difference is 0.060 s, which is about +-40% in Cd. The test can only say "similar"; it could not have detected a difference of the size the CFD predicts (1-4%). It neither confirms nor refutes the CFD.
- The two rows are not designs from the CFD batches (one is 24 rings, outside the 27-32 band), so this is not a check of the CFD predictions.
- The strongest physical signal in the sheet is 24-10 (1.322 s) against 24-14 (1.150 s): same rings and spacing, overlap 10 vs 14 degrees, 0.172 s apart = about 7 standard errors. The CFD screening never covered 24 rings, and its overlap parameter is a fraction rather than degrees.
- Measured Cds (0.14-0.31) are 2.3-5x below the CFD's 0.70.
- Short drops resolve Cd poorly (about 7% Cd per 0.01 s near 1.15 s, because the canopy never reaches terminal speed in 1.0-1.3 s).
