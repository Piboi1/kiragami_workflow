"""Mesh statistics SimScale logs for every ledger run (cells, prisms = boundary-layer cells, tetrahedra with non-orthogonality above 88 degrees)
next to the run's outcome. Writes runs/mesh_quality.json; prints a table."""

import json
import re
from pathlib import Path

import config
from main import _simscale_client


def stats(messages):
    text = "\n".join(messages)
    def num(pattern):
        m = re.search(pattern, text)
        return float(m.group(1)) if m else None
    return {"tets": num(r"Number of tetrahedra:\s*([\d.]+)"), "prisms": num(r"Number of prisms:\s*([\d.]+)"), "hexes": num(r"Number of hexahedra:\s*([\d.]+)"),
            "tet_nonorth_max": num(r"Tetrahedra Non-orthogonality \(Max\.\):\s*([\d.]+)"), "tets_over_88": num(r"Number of Tetrahedra with Non-orthogonality > 88:\s*([\d.]+)") or 0,
            "pyr_over_88": num(r"Number of Pyramids with Non-orthogonality > 88:\s*([\d.]+)") or 0}


def main():
    out_path = Path(config.RUNS_DIR) / "mesh_quality.json"
    cache = json.loads(out_path.read_text()) if out_path.exists() else {}
    client, pid = _simscale_client()
    emap = json.loads((Path(config.RUNS_DIR) / "error_map.json").read_text())
    rows = {}
    for rec in json.loads(Path(config.LEDGER_PATH).read_text())["records"]:
        did, sim = rec["design_id"], rec.get("simscale") or {}
        if not sim.get("mesh_operation_id"):
            continue
        item = cache.get(did)
        if not item:
            try:
                ev = client.meshes.get_mesh_operation_event_log(pid, sim["mesh_operation_id"])
                item = stats([e.message for e in ev.entries or []])
            except Exception as exc:
                item = {"error": str(exc)[:80]}
        item.update(outcome=emap.get(did, {}).get("outcome"), method=(rec.get("solid_params") or {}).get("EXTRUDE_METHOD", "legacy"), settings=rec.get("settings"))
        rows[did] = item
    out_path.write_text(json.dumps(rows, indent=2) + "\n")
    print(f"{'run':12s} {'build':8s} {'outcome':38s} {'prisms':>8s} {'tets>88deg':>10s} {'pyr>88':>6s} {'max nonorth':>11s}")
    for did, r in rows.items():
        if "error" in r:
            continue
        print(f"{did:12s} {r['method']:8s} {str(r['outcome']):38s} {r['prisms'] or 0:8.0f} {r['tets_over_88']:10.0f} {r['pyr_over_88']:6.0f} {r['tet_nonorth_max'] or 0:11.1f}")


if __name__ == "__main__":
    main()
