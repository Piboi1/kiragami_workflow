"""Plots where SimScale reported the solver diverging, on the canopy that was simulated.

Reads the failed runs of the given ledger designs from SimScale's event log ("... diverging ... near the position: X, Y, Z" in metres),
and draws them on a top view and a side view of the canopy (inches, apex at the origin). Usage:
    python tools_divergence_plot.py OUT.png design_0030:batch_20261029_013921_01 design_0031:batch_20261029_013921_02 ...
(each argument is <ledger design>:<batch design name whose canopy file was simulated>).
"""

import json
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import trimesh

import config
from main import _simscale_client

IN = 0.0254


def stl_blocks(path):
    """Triangles of an ASCII STL with solid blocks (inches)."""

    pts = re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", Path(path).read_text())
    return np.array(pts, dtype=float).reshape(-1, 3, 3)


def divergence_position(client, pid, sim):
    ev = client.runs.get_simulation_run_event_log(pid, sim["simulation_id"], sim["run_id"])
    for e in reversed(ev.entries or []):
        m = re.search(r'"X":(-?[\d.eE-]+),"Y":(-?[\d.eE-]+),"Z":(-?[\d.eE-]+)', e.message)
        if m:
            return np.array([float(v) for v in m.groups()]) / IN, e.message.split(" near")[0][:70]
    return None, "no position reported (guard cancelled the run)"


def main(out, specs):
    client, pid = _simscale_client()
    records = {r["design_id"]: r for r in json.loads(Path(config.LEDGER_PATH).read_text())["records"]}
    groups = {}
    for spec in specs:
        design, batch_name = spec.split(":")
        batch_dir = next(Path(config.RUNS_DIR, "batch").glob(f"*/{batch_name}"))
        groups.setdefault(batch_name, {"dir": batch_dir, "runs": []})["runs"].append(design)
    fig, axes = plt.subplots(len(groups), 2, figsize=(13, 5.5 * len(groups)), squeeze=False)
    colours = ["#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b"]
    for row, (name, g) in enumerate(groups.items()):
        tris = stl_blocks(next(g["dir"].glob("*_canopy_presplit.stl")))
        flat = tris.reshape(-1, 3)
        r_max = np.hypot(flat[:, 0], flat[:, 1]).max()
        top, side = axes[row]
        top.add_collection(matplotlib.collections.PolyCollection(tris[:, :, :2], facecolor="#cfcfcf", edgecolor="#888888", linewidth=0.05))
        centre = tris.mean(axis=1)
        side.scatter(np.hypot(centre[:, 0], centre[:, 1]), centre[:, 2], s=0.4, c="#aaaaaa")
        for k, design in enumerate(g["runs"]):
            pos, note = divergence_position(client, pid, records[design]["simscale"])
            label = f"{design}: {note}"
            if pos is None:
                continue
            r = np.hypot(pos[0], pos[1])
            top.scatter(pos[0], pos[1], s=120, facecolor="none", edgecolor=colours[k], linewidth=2.2, label=f"{label} (r {r:.2f} in, z {pos[2]:.2f} in)")
            side.scatter(r, pos[2], s=120, facecolor="none", edgecolor=colours[k], linewidth=2.2)
        top.set_xlim(-2.6, 2.6)
        top.set_ylim(-2.6, 2.6)
        top.set_aspect("equal")
        top.set_title(f"{name}: top view, inner 2.6 in (canopy radius {r_max:.1f} in)")
        top.set_xlabel("x (in)")
        top.set_ylabel("y (in)")
        top.legend(fontsize=7, loc="lower left")
        side.set_xlim(0, 2.6)
        side.set_ylim(-1.8, 0.1)
        side.set_title("side view: radius vs height (each dot = one triangle)")
        side.set_xlabel("distance from the axis (in)")
        side.set_ylabel("z (in), apex = 0")
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
