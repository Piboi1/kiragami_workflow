"""Exports every ledger run (all builds, meshes and outcomes) to exports/all_runs.csv and exports/all_runs.xlsx.

    python review/export_all_runs.py
"""
import csv
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sheets.results import outcome_of  # noqa: E402

ledger = json.loads((ROOT / "state" / "ledger.json").read_text())["records"]

# design_id -> batch name (runs/ first, snapshot/ as fallback)
batch_of = {}
for pattern in ("runs/batch/*/batch.json", "snapshot/batch/*/batch.json"):
    for f in sorted(glob.glob(str(ROOT / pattern))):
        for e in json.loads(Path(f).read_text()).get("designs", []):
            for d in [e.get("design_id")] + e.get("previous_design_ids", []):
                if d:
                    batch_of.setdefault(d, e["name"])

physical = {  # drop tests reported by the team (ranking only; times are in the team's sheet)
    "design_0057": "1st of the three (best)", "design_0049": "2nd, close behind 0057", "design_0065": "3rd, by far the worst tested",
}


def fineness_numerics(r):
    s = r.get("settings") or "fineness 4.5; 500 it; default numerics"  # runs before the settings column existed
    parts = [p.strip() for p in s.split(";")]
    numerics = "; ".join(parts[2:]) if len(parts) > 2 else ""
    return parts[0].replace("fineness ", ""), "" if numerics == "default numerics" else numerics


def shape_key(r):
    p = r["params"]
    return (p["NUM_RINGS"], p["CUTS_PER_RING"], round(p["RING_SPACING_EXPONENT"], 4), round(p["INNER_RADIUS"], 4),
            round(p.get("RING_OVERLAP_FRACTION") or -1, 4))


first_seen = {}
for r in ledger:
    first_seen.setdefault(shape_key(r), r["design_id"])

header = ["design_id", "batch", "outcome", "build", "mesh fineness", "solver numerics", "rings", "cuts per ring", "spacing exponent",
          "overlap fraction", "outer overlap (deg)", "inner overlap (deg)", "inner radius (in)", "canopy depth (in)", "ref area (in2)",
          "inlet speed (m/s)", "Cd", "Cl", "Cd noise (last 10%)", "Cd drift", "iterations", "CPU h (mesh+solve)", "same shape as",
          "physical drop test", "error / note"]
rows = []
for r in ledger:
    p, rp, s, v = r["params"], r.get("resolved_params") or {}, r.get("solid") or {}, r.get("velocity") or {}
    c, sim = r.get("coefficients") or {}, r.get("simscale") or {}
    f, n = fineness_numerics(r)
    first = first_seen[shape_key(r)]
    rows.append([r["design_id"], batch_of.get(r["design_id"], "(before batches)"), outcome_of(r), p["EXTRUDE_METHOD"], f, n or "default",
                 p["NUM_RINGS"], p["CUTS_PER_RING"], round(p["RING_SPACING_EXPONENT"], 4),
                 round(p["RING_OVERLAP_FRACTION"], 4) if p.get("RING_OVERLAP_FRACTION") is not None else "",
                 round(rp.get("RING_OVERLAP_DEGREES", p.get("RING_OVERLAP_DEGREES") or 0), 2),
                 round(rp.get("INNER_RING_OVERLAP_DEGREES", p.get("INNER_RING_OVERLAP_DEGREES") or 0), 2),
                 round(p["INNER_RADIUS"], 4), round(s["canopy_depth_in"], 2) if s.get("canopy_depth_in") else "",
                 round(s["reference_area_in2"], 2) if s.get("reference_area_in2") else "",
                 round(abs(v["uz_mps"]), 3) if v.get("uz_mps") else "",
                 round(r["cd"], 4) if r.get("cd") is not None and outcome_of(r) == "valid" else "",
                 round(c["cl"], 5) if c.get("cl") is not None and outcome_of(r) == "valid" else "",
                 round(c["cd_noise_rel"], 5) if c.get("cd_noise_rel") is not None else "",
                 round(c["cd_drift_rel"], 5) if c.get("cd_drift_rel") is not None else "",
                 c.get("n_iterations", ""), round((sim.get("mesh_core_hours") or 0) + (sim.get("run_core_hours") or 0), 2),
                 first if first != r["design_id"] else "", physical.get(r["design_id"], ""),
                 ((r.get("error") or "") + (" | " + r["note"] if r.get("note") else ""))[:200].replace("\n", " ")])

out = ROOT / "exports"
out.mkdir(exist_ok=True)
with open(out / "all_runs.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(header)
    w.writerows(rows)

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Font, PatternFill  # noqa: E402

wb = Workbook()
ws = wb.active
ws.title = "all runs"
ws.append(header)
for row in rows:
    ws.append(row)
for cell in ws[1]:
    cell.font = Font(bold=True)
ws.freeze_panes = "B2"
ws.auto_filter.ref = ws.dimensions
green = PatternFill("solid", fgColor="D9EAD3")
for i, row in enumerate(rows, start=2):
    if row[2] == "valid":
        for cell in ws[i]:
            cell.fill = green
for col, width in zip("ABCDEFGHIJKLMNOPQRSTUVWXY", [12, 26, 22, 9, 8, 22, 6, 6, 9, 9, 9, 9, 9, 9, 9, 9, 8, 9, 10, 9, 9, 9, 12, 26, 60]):
    ws.column_dimensions[col].width = width

ws2 = wb.create_sheet("ranking (like for like)")
ws2.append(["Only fineness-6, default-numerics runs are comparable with each other (fineness 4.5 over-predicted Cd by 4-10%)."])
ws2.append(["rank", "design_id", "Cd", "rings", "cuts per ring", "spacing exponent", "overlap fraction", "inner radius (in)", "physical drop test"])
good = sorted([x for x in rows if x[2] == "valid" and x[4] == "6" and x[5] == "default"], key=lambda x: -x[16])
for i, x in enumerate(good, 1):
    ws2.append([i, x[0], x[16], x[6], x[7], x[8], x[9], x[12], x[23]])
ws2["A1"].font = Font(bold=True)
for cell in ws2[2]:
    cell.font = Font(bold=True)
wb.save(out / "all_runs.xlsx")
print(len(rows), "runs;", sum(1 for x in rows if x[2] == "valid"), "valid;", len(good), "in the like-for-like ranking")
