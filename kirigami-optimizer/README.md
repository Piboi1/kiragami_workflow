# kirigami-optimizer

This pipeline searches for the kirigami parachute pattern with the highest drag coefficient. For each design it:

1. **generator/**: builds the flat cut pattern (DXF) and exact ring geometry (`*.report.json`). This is the refactored `kirigami_generator.py`, and its output is identical to the original.
2. **solid/**: builds the expanded 3D canopy as a watertight solid, then the fluid domain (box minus canopy) with named boundary faces. This is the fixed `kiragami3dcreator.py`.
3. **velocity/**: predicts the inlet speed from `v = sqrt(2mg / (ρ·Cd·A))`. Cd starts from a real drop test and is refined using the CFD Cd of similar designs.
4. **simscale/**: uploads the domain and runs an incompressible external-flow CFD case through the SimScale SDK, mirroring the manual `Kiragami_CFD` setup. It then pulls Cd/Cl/Cm from the force-coefficient plot.
5. **sheets/**: logs one row per design to a Google Sheet and to `runs/results.csv`.
6. **optimizer/**: chooses the next design with `skopt.gp_minimize` (maximizing Cd). Progress is checkpointed to disk after every design.

```
kirigami-optimizer/
├── generator/   generate_pattern(params) -> ring geometry (+ DXF, report files)
├── solid/       build_solid(ring_data, params) -> trimesh solid; build_flow_domain()
├── simscale/    SDK wrapper: upload, import, sim spec, mesh, run, poll, results
├── velocity/    physics-based Uz estimator
├── sheets/      Google Sheets + local CSV logger
├── optimizer/   search space, crash-safe ledger, gp_minimize loop
├── pipeline.py  one full pass for one design
├── main.py      CLI
├── config.py    every setting (fixed params, search space, CFD setup, masses)
├── legacy/      the two original scripts, unmodified, for reference
├── reference/   Onshape STL of the Run 11 canopy (millimetres)
└── tests/       pytest suite (runs offline)
```

## Setup

```bash
cd kirigami-optimizer
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install git+https://github.com/SimScaleGmbH/simscale-python-sdk.git
python -m pytest -q tests                              # should be all green
```

If the SimScale SDK install fails with `AttributeError: install_layout`, retry with
`SETUPTOOLS_USE_DISTUTILS=stdlib pip install git+https://github.com/SimScaleGmbH/simscale-python-sdk.git`.

## Credentials: what goes where

Copy `.env.example` to `.env` in this folder. Both `.env` and `credentials/` are git-ignored.

| What | Where | Needed for |
|---|---|---|
| SimScale API key | `SIMSCALE_API_KEY=` in `.env` | every CFD run |
| SimScale project ID | `SIMSCALE_PROJECT_ID=` in `.env` (**optional**) | leave empty to have a `Kirigami_Optimizer` project created automatically |
| Google service-account key | save the JSON as `credentials/service_account.json` | writing the sheet |
| Google Sheet ID | `GOOGLE_SHEET_ID=` in `.env` | writing the sheet |

**SimScale API key:** in SimScale, go to your avatar, then Account settings, then API keys. API access must be enabled for your account. If you don't see the section, ask SimScale support.

**Google service account:**
1. Go to console.cloud.google.com and create a project (or use an existing one).
2. Enable the **Google Sheets API** and the **Google Drive API**.
3. Under IAM & Admin, open Service Accounts, create one, then go to Keys, Add key, JSON. Save the file as `credentials/service_account.json`.
4. Create a Google Sheet. **Share it with the service account's `client_email`** (found in the JSON) as **Editor**.
5. Copy the sheet ID from its URL (`/spreadsheets/d/<ID>/edit`) into `GOOGLE_SHEET_ID`.

## First single end-to-end test

```bash
python main.py dry-run                  # no network: builds DXF + STLs + Uz for the baseline design
python main.py check                    # confirms the SimScale key and the sheet both work
python main.py inspect-simscale         # optional: dumps Kiragami_CFD's manual sim setup to runs/simscale_template/
python main.py single --preset baseline --uz 6.148
```

`single --preset baseline --uz 6.148` runs the Run 11 design at Run 11's inlet speed and writes one row to the sheet. The resulting Cd should be close to Run 11's **1.083**. It won't match exactly: the rebuilt canopy has narrower ribbons than your Onshape model, and the reference area may differ (see open questions). Run `single --preset baseline` without `--uz` to use the physics-based Uz estimate instead.

Each design's files go to `runs/design_NNNN/`: the DXF, report, canopy STL, flow-domain STL, raw force-coefficient CSV, `record.json`, and SimScale's face mapping.

## Full optimization

```bash
python main.py optimize --n-calls 150 --n-initial 20
python main.py status                  # progress, best design, pending resume
python main.py sync-sheet              # re-send any rows that failed to reach the sheet
```

**Crash safety:** `state/ledger.json` is rewritten atomically after every step. SimScale IDs are saved there the moment each object is created. After a crash or Ctrl-C, run the same `optimize` command again. It finishes the interrupted design by resuming the poll, so nothing is re-meshed and no core hours are billed twice. It then passes every finished design back to `gp_minimize` as `x0`/`y0` and continues. `--n-calls` is the *total* number of designs, so re-running the same command after completion does nothing.

**Search space** (`config.SEARCH_SPACE`), 5 dimensions:

| Parameter | Range | Notes |
|---|---|---|
| `NUM_RINGS` | 16–36 | |
| `CUTS_PER_RING` | 4–12 | |
| `RING_SPACING_EXPONENT` | 0.6–1.5 | |
| `RING_OVERLAP_FRACTION` | 0.2–1.0 | Overlap as a fraction of what the cut count allows: `180/N − MIN_GAP`. This avoids clamped dead zones. |
| `INNER_RADIUS` | 0.5–1.25 in | |

About 0.8% of this space is geometrically impossible. In those designs, sparse inner rings make the ribbons wider than the first ring's radius, so they would cross the center. `solid.canopy.check_feasible` rejects them in milliseconds and logs them as `infeasible`. They are scored as Cd = 0 (`OPTIMIZER["FAILURE_CD"]`) and never reach SimScale.

The cut-length curve (`MIN/MAX_CUT_LENGTH`, `PARABOLIC_EXPONENT`) is left out on purpose. With `ENFORCE_RING_OVERLAP`, the overlap always sets the cut angle, so those parameters never change the geometry. Everything not searched is held at `config.FIXED_PARAMS`. To change the search, move a key between the two.

## How Uz is estimated

- **Mass:** `PAYLOAD_MASS_G` + canopy mass (`SHEET_GSM` × flat disk area, or `CANOPY_MASS_G` if you weigh it).
- **Seed Cd:** comes from the 24-10 drop test, which had the slowest descent: 4.937 m in 1.322 s. A 4.9 m drop is too short to reach terminal velocity, so the fall-from-rest equation with quadratic drag is solved for v_t (5.13 m/s rather than the 3.73 m/s average). Cd is then back-calculated. Set `SEED_CD` to skip this step.
- **Refinement:** CFD Cd values from finished designs are weighted by similarity. The weight is a Gaussian kernel in the normalized search space, with length scale `KERNEL_LENGTH_SCALE`.
  - `CD_REFINEMENT="anchored"` (default) uses the CFD results only for *relative* differences between designs: `Cd = Cd_seed × local_CFD / median_CFD`. The drop test sets the absolute level.
  - `"direct"` blends the seed Cd with the neighbouring CFD Cd values themselves.
  - "anchored" is the default because the drop-test Cd (≈0.3 with a 20 g payload) is well below the CFD Cd (≈1.08). With `"direct"`, the predicted Uz would drift away from real descent speeds.

Every row logs the Cd used for Uz, its source, and the nearest previous run.

## Why the flow domain is built locally

In the manual project, "External flow volume" and "Delete bodies" are SimScale CAD-mode operations, and the public API/SDK doesn't expose them. Instead, `solid/flow_domain.py` builds the same box as your manual setup: X/Y ±2.583 D, Z from −7 D to +3.113 D, which is ±25.83 / −70 / +31.13 in for D = 10 in. It subtracts the canopy and writes a multi-solid ASCII STL. Each boundary is its own `solid` block: `inlet`, `outlet`, `side_*`, `canopy`. The runner maps those names to SimScale face IDs. If SimScale names them differently, the run stops with a clear error and saves the mapping to `runs/design_NNNN/simscale_entities.json`.

## Changes and fixes vs. the original scripts

- `kiragami3dcreator.py`:
  - The `shell` mesh is now actually built before extrusion.
  - The `files.download()` call is removed.
  - The hard-coded 45°/22.5° angles now follow `CUTS_PER_RING`.
  - Two extrusion modes are available:
    - `union` (default): a clean, single-body, watertight solid. Strips get a 0.03 in joint overlap so they fuse. Without it they only touch end-to-end and produce about 480 separate pieces.
    - `legacy`: the original extrusion.
- `kirigami_generator.py`: the math is unchanged. It is verified by a test against the original script's report output.
