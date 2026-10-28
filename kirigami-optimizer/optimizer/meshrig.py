"""Automatic mesh test: does SimScale's mesher accept a design? No manual flow-volume step.

The air volume (box minus canopy) is built locally and imported as one STEP solid; then ONLY the mesher is started with the real
settings (physics based, boundary layers). A rejected geometry fails within about half a minute at 0 CPU h; an accepted one is
cancelled as soon as it reaches ``pass_progress`` (default 30%), so a pass costs about 0.1-1 CPU h.
"""

import json
import re
import time
from pathlib import Path

import config
from pipeline import build_geometry
from simscale.runner import SimScaleClient


def mesh_test(params, name, route="step", cap=3.0, pass_progress=0.3, timeout=1500, log=print, import_only=False, improve=None):
    """Returns {"result": PASS|FAIL|ERROR, "reason", "cpu_hours", "seconds", ...}."""

    saved = config.SIMSCALE["GEOMETRY_FORMAT"]
    config.SIMSCALE["GEOMETRY_FORMAT"] = {"step": "STEP", "stl": "STL"}[route]
    if improve is not None:
        config.SIMSCALE["IMPORT_IMPROVE"] = improve
    config.SIMSCALE["STEP_CHECK"] = False  # the mesher is the judge; BRepCheck does not separate working from failing designs
    t0 = time.time()
    out = config.RUNS_DIR / "mesh_tests" / name
    out.mkdir(parents=True, exist_ok=True)
    try:
        _, info, _, paths = build_geometry(params, out, name)
        cfg = dict(config.SIMSCALE, MESH_MAX_CORE_HOURS=cap, MESH_TIMEOUT_S=timeout, MESH_PASS_PROGRESS=pass_progress,
                   STALL_TIMEOUT_S=900)
        client = SimScaleClient(cfg)
        pid = client.ensure_project(config.SIMSCALE["PROJECT_ID"], config.SIMSCALE["NEW_PROJECT_NAME"], config.STATE_DIR / "simscale_project.json")
        state = {}
        if import_only:  # free: SimScale's geometry validity check only, no mesher
            result = {"name": name, "route": route, "volume_in3": round(info["volume_in3"], 4), "triangles": info.get("triangle_count")}
            try:
                _, import_id = client.start_import(pid, name, paths["domain_file"], False, config.SIMSCALE["IMPORT_IMPROVE"], fmt=config.SIMSCALE["GEOMETRY_FORMAT"])
                gid = client.wait_for_import(pid, import_id)
                geo = client.describe_geometry(pid, gid, out / "simscale_entities.json")
                named = {g: len(n) for g, n in geo["groups"].items()}
                result.update(result="IMPORT OK", reason="", geometry_id=gid, faces_by_group=named, bodies=len(geo["bodies"]),
                              box_faces_named=all(named.get(g) for g in ("inlet", "outlet", "side_xmin", "side_xmax", "side_ymin", "side_ymax")))
            except Exception as exc:
                result.update(result="IMPORT FAIL", reason=str(exc).replace("\n", " ")[:600])
            result.update(cpu_hours=0.0, seconds=round(time.time() - t0))
            (out / "mesh_test.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
            return result
        result = {"name": name, "route": route, "triangles": info.get("triangle_count"), "volume_in3": round(info["volume_in3"], 4)}
        try:
            run = client.run_design(pid, name, paths["domain_file"], -6.148, info["reference_area_in2"], info["projected_diameter_in"],
                                    out, state, lambda s: None, mesh_only=True, manual={"bounds_in": info["domain_bounds_in"]})
            result.update(result="PASS" if run.get("mesh_passed") else "PASS (finished)", reason="", geometry_id=state.get("geometry_id"))
        except Exception as exc:
            reason = ""
            op = state.get("mesh_operation_id")
            if op and "meshing FAILED" in str(exc):
                entries = client.meshes.get_mesh_operation_event_log(pid, op).entries or []
                reason = " | ".join(e.message for e in entries)[:400]
                m = re.search(r'"X":(-?[\d.eE-]+),"Y":(-?[\d.eE-]+),"Z":(-?[\d.eE-]+)', reason)
                if m:
                    x, y, z = (float(v) / 0.0254 for v in m.groups())
                    reason = f"{reason.split('Position')[0].strip()} at r={(x * x + y * y) ** 0.5:.2f} in, z={z:.2f} in"
                result.update(result="FAIL", reason=reason, geometry_id=state.get("geometry_id"))
            else:
                result.update(result="ERROR", reason=f"{type(exc).__name__}: {str(exc)[:300]}", geometry_id=state.get("geometry_id"))
        result["cpu_hours"] = round(float(state.get("mesh_core_hours") or 0.0), 3)
    finally:
        config.SIMSCALE["GEOMETRY_FORMAT"] = saved
        config.SIMSCALE.pop("STEP_CHECK", None)
    result["seconds"] = round(time.time() - t0)
    (out / "mesh_test.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    return result
