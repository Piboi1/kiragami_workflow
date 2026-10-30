"""PDF atlas of every geometry tried so far and where SimScale reported each failure (reads runs/error_map.json; run tools_error_map.py first).

One page per distinct shape: plan view and side profile of the pattern, a marker for every failure position, and a table of the runs on that shape.
"""

import json
import sys
from collections import OrderedDict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.collections import PolyCollection
from matplotlib.lines import Line2D

import config
from generator import generate_pattern
from solid.canopy import build_open_strips, normalize_ring_data

STYLE = {  # outcome prefix -> (colour, marker, label)
    "mesher failed": ("#d62728", "X", "mesher refused the geometry (self-intersection)"),
    "solver diverged (crashed)": ("#ff7f0e", "o", "solver blew up (run failed)"),
    "solver diverged (stopped by guard)": ("#1f77b4", "^", "solver ran away (stopped by guard)"),
    "solver diverged (garbage Cd)": ("#8c564b", "s", "solver ended with a garbage Cd"),
}
OK = "#2ca02c"


def shape_key(item):
    p = item["params"]
    return tuple(round(float(p[k]), 4) if isinstance(p[k], (int, float)) else p[k] for k in ("NUM_RINGS", "CUTS_PER_RING", "RING_SPACING_EXPONENT", "INNER_RADIUS", "RING_OVERLAP_FRACTION"))


def pattern(rec):
    rep = generate_pattern(rec["params"], output_dir="/tmp/atlas", name="a")
    rings = normalize_ring_data(rep)
    sp = rec["solid_params"]
    strips = build_open_strips(rings, int(sp["CUTS_PER_RING"]), float(sp["VERTICAL_DROP"]), float(sp["KERF_FACTOR"]), 0.0, bridge_extra=0.0)
    return strips, rings


def page_for(pdf, shape_id, runs, rec):
    strips, rings = pattern(rec)
    fig = plt.figure(figsize=(11, 8.5))
    ax_plan = fig.add_axes([0.04, 0.34, 0.46, 0.60])
    ax_side = fig.add_axes([0.56, 0.38, 0.40, 0.52])
    tris = np.array([v[f][:, :2] for v, faces in strips for f in [faces]] if False else [v[tri][:, :2] for v, faces in strips for tri in faces])
    ax_plan.add_collection(PolyCollection(tris, facecolor="#d9d9d9", edgecolor="#9a9a9a", linewidth=0.05))
    pts = np.vstack([v for v, _ in strips])
    ax_side.scatter(np.hypot(pts[:, 0], pts[:, 1]), pts[:, 2], s=0.6, c="#7f7f7f")
    for ring in rings:
        pass
    seen = set()
    for run in runs:
        pos = run.get("position")
        if not pos:
            continue
        colour, marker, _ = next((v for k, v in STYLE.items() if run["outcome"].startswith(k)), ("#000000", "o", ""))
        ax_plan.scatter(pos["x"], pos["y"], s=150, marker=marker, facecolor="none", edgecolor=colour, linewidth=2.2)
        ax_plan.annotate(run["design_id"][-4:], (pos["x"], pos["y"]), xytext=(5, 5), textcoords="offset points", fontsize=7, color=colour)
        ax_side.scatter(pos["r"], pos["z"], s=150, marker=marker, facecolor="none", edgecolor=colour, linewidth=2.2)
        ax_side.annotate(run["design_id"][-4:], (pos["r"], pos["z"]), xytext=(5, 5), textcoords="offset points", fontsize=7, color=colour)
    r_max = float(np.hypot(pts[:, 0], pts[:, 1]).max())
    ax_plan.set_xlim(-r_max - 0.2, r_max + 0.2)
    ax_plan.set_ylim(-r_max - 0.2, r_max + 0.2)
    ax_plan.set_aspect("equal")
    ax_plan.set_title("plan view (from above; ribbons and cells)", fontsize=9)
    ax_side.set_title("side profile: distance from axis vs height (grey = ribbons)", fontsize=9)
    ax_side.set_xlabel("r (in)", fontsize=8)
    ax_side.set_ylabel("z (in), apex = 0", fontsize=8)
    ax_side.tick_params(labelsize=7)
    ax_plan.tick_params(labelsize=7)
    p = rec["params"]
    fig.suptitle(f"Shape {shape_id}: {p['NUM_RINGS']} rings, {p['CUTS_PER_RING']} cuts per ring, ring exponent {p['RING_SPACING_EXPONENT']:.2f}, inner radius {p['INNER_RADIUS']:.2f} in", fontsize=11, y=0.985)
    rows = []
    for run in runs:
        pos = run.get("position")
        where = f"r {pos['r']:.2f} in, z {pos['z']:.2f} in" if pos else "no position reported"
        if run["outcome"] == "ok":
            where = f"Cd {run['cd']:.4f}"
        rows.append([run["design_id"], run["method"], (run.get("settings") or "")[:34], run["outcome"][:40], f"{run['cpu_h']:.2f}", where])
    table = fig.add_axes([0.04, 0.03, 0.92, 0.27])
    table.axis("off")
    tb = table.table(cellText=rows, colLabels=["run", "build", "solver settings", "outcome", "CPU h", "where / result"], loc="upper left", cellLoc="left",
                     colWidths=[0.09, 0.08, 0.27, 0.27, 0.07, 0.22])
    tb.auto_set_font_size(False)
    tb.set_fontsize(7)
    tb.scale(1, 1.15)
    pdf.savefig(fig)
    plt.close(fig)


def main(out):
    data = json.loads((Path(config.RUNS_DIR) / "error_map.json").read_text())
    ledger = {r["design_id"]: r for r in json.loads(Path(config.LEDGER_PATH).read_text())["records"]}
    groups = OrderedDict()
    for did, item in data.items():
        groups.setdefault(shape_key(item), []).append(item)
    with PdfPages(out) as pdf:
        # cover + legend
        fig = plt.figure(figsize=(11, 8.5))
        fig.text(0.05, 0.93, "Kirigami parachute: every geometry tried and where it failed", fontsize=18, weight="bold")
        n_ok = sum(1 for d in data.values() if d["outcome"] == "ok")
        fig.text(0.05, 0.88, f"{len(data)} runs on {len(groups)} distinct shapes; {n_ok} finished with a valid Cd. Canopy builds: legacy (original), clean (fused slabs), lattice (pinned joints).", fontsize=10)
        handles = [Line2D([], [], marker=m, color="w", markeredgecolor=c, markerfacecolor="none", markersize=11, markeredgewidth=2, label=l) for c, m, l in STYLE.values()]
        fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.05, 0.84), fontsize=9, frameon=False, title="Markers = where SimScale put the failure", title_fontsize=9)
        by = {}
        for d in data.values():
            by.setdefault(d["outcome"], []).append(d["design_id"][-4:])
        y = 0.60
        fig.text(0.05, y + 0.04, "Outcomes", fontsize=11, weight="bold")
        for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
            fig.text(0.05, y, f"{k}: {len(v)}   ({', '.join(v)})", fontsize=8.5)
            y -= 0.032
        notes = ["What the positions say",
                 "- Mesher failures (red X) all sit within 1.5 in of the apex: the centre cap and the first rings (the old builds' crossing triangles and slivers).",
                 "- Solver blow-ups (orange / blue) are spread over r 1.0 - 4.6 in, on a different ribbon in each run; they follow the same designs across builds, not one spot.",
                 "- Runs 0001, 0010, 0014-0016 and 0019 were pipeline / settings problems, not geometry; 0029 hit the mesh CPU cap.",
                 "- Valid Cds (8 runs) all came from the legacy build; no clean- or lattice-build run has converged yet.",
                 "- Mesh quality is the common factor: converged runs had at most 81 nearly-flat tetrahedra (non-orthogonality > 88 deg); clean/lattice meshes had 190-1100 and half the boundary-layer cells (page 4)."]
        y = 0.20
        for k, line in enumerate(notes):
            fig.text(0.05, y, line, fontsize=10 if k == 0 else 8.5, weight="bold" if k == 0 else "normal")
            y -= 0.035
        pdf.savefig(fig)
        plt.close(fig)
        # overview: all failure positions on one profile
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 8.5))
        allpos = [(d, d["position"]) for d in data.values() if d.get("position")]
        rec0 = ledger["design_0023"]
        strips, _ = pattern(rec0)
        pts = np.vstack([v for v, _ in strips])
        a1.scatter(np.hypot(pts[:, 0], pts[:, 1]), pts[:, 2], s=0.4, c="#cccccc")
        for d, pos in allpos:
            c, m, _ = next((v for k, v in STYLE.items() if d["outcome"].startswith(k)), ("#000", "o", ""))
            a1.scatter(pos["r"], pos["z"], s=90, marker=m, facecolor="none", edgecolor=c, linewidth=2)
        a1.set_title("all reported failure positions on the dome profile")
        a1.set_xlabel("r (in)")
        a1.set_ylabel("z (in)")
        rs = [pos["r"] for _, pos in allpos]
        kinds = ["mesher" if d["outcome"].startswith("mesher") else "solver" for d, _ in allpos]
        a2.hist([[r for r, k in zip(rs, kinds) if k == "mesher"], [r for r, k in zip(rs, kinds) if k == "solver"]], bins=np.arange(0, 5.5, 0.5), color=["#d62728", "#ff7f0e"], label=["mesher", "solver"])
        a2.set_title("distance from the axis of every reported failure")
        a2.set_xlabel("r (in)")
        a2.set_ylabel("number of runs")
        a2.legend()
        pdf.savefig(fig)
        plt.close(fig)
        mq_path = Path(config.RUNS_DIR) / "mesh_quality.json"
        if mq_path.exists():
            mq = json.loads(mq_path.read_text())
            fig, ax = plt.subplots(figsize=(11, 8.5))
            colours = {"legacy": "#1f77b4", "clean": "#ff7f0e", "lattice": "#d62728"}
            for did, r in mq.items():
                if "error" in r or not r.get("prisms"):
                    continue
                good = r["outcome"] == "ok"
                ax.scatter(r["prisms"], max(r["tets_over_88"], 0.7), s=120 if good else 70, marker="o" if good else "x", c=colours.get(r["method"], "k"),
                           linewidths=2, facecolors=colours.get(r["method"]) if good else None)
                ax.annotate(did[-4:], (r["prisms"], max(r["tets_over_88"], 0.7)), xytext=(4, 4), textcoords="offset points", fontsize=6)
            ax.set_yscale("log")
            ax.set_xlabel("boundary-layer prism cells in the mesh")
            ax.set_ylabel("tetrahedra with non-orthogonality > 88 degrees (log)")
            ax.set_title("Mesh quality vs outcome: filled circle = converged, cross = solver diverged\n(blue legacy, orange clean, red lattice): the clean/lattice meshes have 5-1000x more nearly flat cells")
            ax.grid(alpha=0.3)
            pdf.savefig(fig)
            plt.close(fig)
        for i, (key, runs) in enumerate(groups.items(), 1):
            page_for(pdf, i, runs, ledger[runs[0]["design_id"]])
    print("wrote", out, f"({len(groups)} shapes)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "exports/geometry_error_atlas.pdf")
