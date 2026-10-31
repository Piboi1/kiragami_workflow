"""Free checks behind REVIEW.md (reads state/ledger.json, runs/mesh_quality.json; no SimScale calls).

    python review/ledger_checks.py
"""
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ledger = json.loads((ROOT / "state" / "ledger.json").read_text())["records"]
mesh = json.loads((ROOT / "runs" / "mesh_quality.json").read_text())


def cpu(r):
    s = r.get("simscale") or {}
    return (s.get("run_core_hours") or 0) + (s.get("mesh_core_hours") or 0)


def shape(r):
    p = r["params"]
    return (p["NUM_RINGS"], p["CUTS_PER_RING"], round(p["RING_SPACING_EXPONENT"], 4), p.get("RING_OVERLAP_FRACTION"), round(p["INNER_RADIUS"], 4))


print("CPU h by build/outcome:")
cat = collections.defaultdict(float)
for r in ledger:
    cat[(r["params"]["EXTRUDE_METHOD"], r["status"])] += cpu(r)
for k, v in sorted(cat.items()):
    print(f"  {k}: {v:.1f}")
print(f"  total {sum(cat.values()):.1f}")

by = collections.defaultdict(list)
for r in ledger:
    if r["params"]["EXTRUDE_METHOD"] == "legacy":
        by[shape(r)].append(r)
print("\nlegacy shapes (rings, cuts, exp, overlap, r_in): attempts, CPU h, got a Cd?")
for s, rs in sorted(by.items(), key=lambda kv: -sum(cpu(r) for r in kv[1])):
    print(f"  {s}: {[r['design_id'][-2:] for r in rs]}, {sum(cpu(r) for r in rs):.1f} h, {'Cd' if any(r['status'] == 'ok' for r in rs) else 'none'}")
band = [(s, rs) for s, rs in by.items() if 27 <= s[0] <= 32]
ok = [s for s, rs in band if any(r["status"] == "ok" for r in rs)]
spent = sum(cpu(r) for s, rs in band for r in rs)
print(f"\nrings 27-32, legacy: {len(band)} shapes tried, {len(ok)} gave a Cd, {spent:.1f} CPU h -> {spent / len(ok):.1f} CPU h per valid shape")

conv = ["design_0002", "design_0005", "design_0009", "design_0011", "design_0013", "design_0023", "design_0025", "design_0028"]
div = ["design_0007", "design_0008", "design_0021", "design_0024", "design_0019"]
c = [mesh[k]["tets_over_88"] for k in conv]
d = [mesh[k]["tets_over_88"] for k in div]
auc = sum((x > y) + 0.5 * (x == y) for y in c for x in d) / (len(c) * len(d))
print(f"\nlegacy flat tets: converged {sorted(c)}, diverged {sorted(d)}; AUC of 'more flat tets -> diverges' = {auc:.2f}")

train = [r["design_id"] for r in ledger if r.get("status") in ("ok", "infeasible") and r.get("x") is not None
         and (r.get("solid_params") or {}).get("EXTRUDE_METHOD") == "lattice"]
print(f"optimizer training points with geometry_method=lattice: {train}")
ext = [(r["design_id"], r["solid"].get("depth_extrapolated")) for r in ledger if r["status"] == "ok"]
print(f"valid Cds whose canopy depth is an extrapolation of the depth model: {sum(1 for _, e in ext if e)} of {len(ext)}")
