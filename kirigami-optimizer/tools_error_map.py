"""Collects, for every ledger design, the shape, the canopy build, the outcome and WHERE SimScale said it failed (mesher / solver), and writes runs/error_map.json.

Positions come from SimScale's event logs (metres, apex at the origin) and are converted to inches. Run it again to refresh (cached results are reused)."""

import json
import re
import sys
from pathlib import Path

import config
from main import _simscale_client

OUT = Path(config.RUNS_DIR) / "error_map.json"
POS = re.compile(r'"X":(-?[\d.eE-]+),"Y":(-?[\d.eE-]+),"Z":(-?[\d.eE-]+)')


def position_from(entries):
    for e in reversed(entries or []):
        m = POS.search(e.message)
        if m:
            x, y, z = (float(v) / 0.0254 for v in m.groups())
            return {"x": x, "y": y, "z": z, "r": (x * x + y * y) ** 0.5, "text": e.message.split(" near")[0][:90]}
    return None


def classify(rec):
    err = rec.get("error") or ""
    if rec.get("status") == "ok":
        return "ok"
    low = err.lower()
    if "estimate" in low and "exceeds" in low:
        return "not run (cost estimate over the cap)"
    if "meshing" in low and "cancelled" in low and "cpu h" in low:
        return "mesh cap hit (cancelled)"
    if "meshing" in low and "failed" in low:
        return "mesher failed"
    if "diverging" in low and "cancelled" in low:
        return "solver diverged (stopped by guard)"
    if "implausible" in low or "diverg" in low:
        return "solver diverged (garbage Cd)"
    if "simulation run failed" in low:
        return "solver diverged (crashed)"
    if "not converged" in low:
        return "solver not converged"
    if "math domain" in low or "apiexception" in low or "bug" in low:
        return "pipeline / API error (not the geometry)"
    if rec.get("status") == "cancelled":
        return "cancelled by user"
    return "other: " + err[:60]


def main():
    cache = json.loads(OUT.read_text()) if OUT.exists() else {}
    client, pid = _simscale_client()
    records = json.loads(Path(config.LEDGER_PATH).read_text())["records"]
    out = {}
    for rec in records:
        did = rec["design_id"]
        sim = rec.get("simscale") or {}
        item = cache.get(did) or {}
        kind = classify(rec)
        item.update(design_id=did, x=rec.get("x"), params={k: rec["params"].get(k) for k in ("NUM_RINGS", "CUTS_PER_RING", "RING_SPACING_EXPONENT", "INNER_RADIUS", "RING_OVERLAP_FRACTION")},
                    method=(rec.get("solid_params") or {}).get("EXTRUDE_METHOD", "legacy"), outcome=kind, cd=rec.get("cd"),
                    settings=rec.get("settings"), error=(rec.get("error") or "")[:200],
                    cpu_h=round(float(sim.get("mesh_core_hours") or 0) + float(sim.get("run_core_hours") or 0), 2))
        if "position" not in item and kind not in ("ok",):
            pos = None
            try:
                if kind.startswith("solver diverged") and sim.get("run_id"):
                    ev = client.runs.get_simulation_run_event_log(pid, sim["simulation_id"], sim["run_id"])
                    pos = position_from(ev.entries)
                elif kind == "mesher failed" and sim.get("mesh_operation_id"):
                    ev = client.meshes.get_mesh_operation_event_log(pid, sim["mesh_operation_id"])
                    pos = position_from(ev.entries)
            except Exception as exc:  # event logs expire or are missing for old runs
                item["position_error"] = str(exc)[:80]
            item["position"] = pos
        out[did] = item
        print(did, item["outcome"], item.get("position") and round(item["position"]["r"], 2))
    OUT.write_text(json.dumps(out, indent=2, default=str) + "\n")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
