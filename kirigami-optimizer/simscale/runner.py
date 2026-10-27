"""SimScale SDK wrapper: upload -> import -> simulation -> mesh -> run -> results.

Every SimScale ID is written into a caller-owned ``state`` dict and
``save_state(state)`` is called right after it is created, so a crash in the
middle of a multi-hour run can be resumed: run_design() skips any step whose
ID is already present and just resumes polling.
"""

import json
import math
import os
import time
from pathlib import Path

import isodate
import urllib3
from simscale_sdk import (
    ApiClient,
    ApiException,
    Configuration,
    GeometriesApi,
    GeometryImportRequest,
    GeometryImportRequestLocation,
    GeometryImportRequestOptions,
    GeometryImportsApi,
    MaterialGroupType,
    MaterialsApi,
    MaterialUpdateOperation,
    MaterialUpdateOperationReference,
    MaterialUpdateRequest,
    MeshOperation,
    MeshesApi,
    MeshOperationsApi,
    Project,
    ProjectPermissionsApi,
    ProjectsApi,
    SimulationRun,
    SimulationRunsApi,
    SimulationsApi,
    StorageApi,
    TopologicalReference,
)

from solid.step_export import BOX_FACES, COLOUR_TO_FACE, parse_colour

from . import manual_flow
from .results import core_hours_used, maybe_unzip, parse_force_coefficients
from .spec import build_mesh_model, build_model, build_simulation_spec

API_KEY_HEADER = "X-API-KEY"
TERMINAL = ("FINISHED", "CANCELED", "FAILED")
MAX_CD_NOISE, MAX_CD_DRIFT = 0.01, 0.01   # converged runs: 0.05% / 0.16%; design_0019 (unconverged) 3.9% / 11%
MIN_PLAUSIBLE_CD, MAX_PLAUSIBLE_CD = 0.05, 4.0   # kirigami canopies measured 0.73-0.78; anything else is a diverged solve
FACE_GROUPS = ("inlet", "outlet", "side_xmin", "side_xmax", "side_ymin", "side_ymax", "canopy", "sting")
REQUIRED_GROUPS = FACE_GROUPS[:-1]  # the sting is optional


class SimScaleError(RuntimeError):
    pass


def describe_api_error(exc):
    """Short 'HTTP 403 Forbidden: <body>' text for an SDK ApiException."""

    body = exc.body.decode("utf-8", "replace") if isinstance(exc.body, bytes) else str(exc.body or "")
    body = " ".join(body.split())
    head = " ".join(str(part) for part in (f"HTTP {exc.status}", exc.reason) if part)
    return head + (f": {body[:300]}" if body else "")


def _core_hours(obj):
    """CPU hours SimScale billed for a finished mesh operation or run."""

    resource = getattr(obj, "compute_resource", None)
    return float(resource.value) if resource is not None and resource.value is not None else 0.0


class SimScaleClient:
    def __init__(self, sim_cfg, log=print):
        if not sim_cfg["API_KEY"]:
            raise SimScaleError("SIMSCALE_API_KEY is not set (see .env.example)")
        self.cfg = sim_cfg
        self.log = log
        self.api_key = sim_cfg["API_KEY"]

        conf = Configuration()
        conf.host = sim_cfg["API_URL"].rstrip("/") + "/v0"
        conf.api_key = {API_KEY_HEADER: self.api_key}
        # The SDK's urllib3 pool ignores the standard proxy/CA environment variables.
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:
            conf.proxy = proxy
        ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
        if ca_bundle:
            conf.ssl_ca_cert = ca_bundle
        self.api_client = ApiClient(conf)
        retry = urllib3.Retry(connect=5, read=5, redirect=0, status=5, backoff_factor=0.5)
        self.api_client.rest_client.pool_manager.connection_pool_kw["retries"] = retry

        self.projects = ProjectsApi(self.api_client)
        self.storage = StorageApi(self.api_client)
        self.imports = GeometryImportsApi(self.api_client)
        self.geometries = GeometriesApi(self.api_client)
        self.meshes = MeshOperationsApi(self.api_client)
        self.simulations = SimulationsApi(self.api_client)
        self.runs = SimulationRunsApi(self.api_client)
        self.materials = MaterialsApi(self.api_client)

    # ------------------------------------------------------------------
    # projects
    # ------------------------------------------------------------------
    def list_projects(self, limit=100):
        return self.projects.get_projects(limit=limit).embedded or []

    def find_project(self, name):
        for p in self.list_projects(limit=1000):
            if p.name == name:
                return p
        return None

    def ensure_project(self, project_id, name, cache_path):
        """Returns a project ID: the configured one, a cached one, or a new project."""

        if project_id:
            return project_id
        cache = Path(cache_path)
        if cache.exists():
            return json.loads(cache.read_text())["project_id"]
        existing = self.find_project(name)
        if existing:
            pid = existing.project_id
        else:
            try:
                project = self.projects.create_project(Project(
                    name=name, description="Automated kirigami parachute Cd optimization", measurement_system="SI"
                ))
            except ApiException as exc:
                if exc.status in (401, 403):
                    raise SimScaleError(
                        f"This API key may not create projects ({describe_api_error(exc)}). Create a project "
                        f"named {name!r} in the SimScale web app and set SIMSCALE_PROJECT_ID to the ID in its URL "
                        "(https://www.simscale.com/workbench/?pid=<ID>)."
                    ) from exc
                raise
            pid = project.project_id
            self.log(f"Created SimScale project {name!r}: {pid}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"project_id": pid, "name": name}) + "\n")
        return pid

    def check_access(self, project_id, project_name):
        """Read-only (plus one free upload-slot) probes; creates no project.

        Returns a list of (label, ok, detail) tuples.
        """

        results = []

        def probe(label, fn):
            try:
                detail = fn()
                results.append((label, True, detail))
                return True
            except ApiException as exc:
                results.append((label, False, describe_api_error(exc)))
            except LookupError as exc:
                results.append((label, False, str(exc)))
            except Exception as exc:
                results.append((label, False, f"{type(exc).__name__}: {exc}"))
            return False

        if not probe("list projects", lambda: f"{len(self.list_projects(limit=100))} project(s) visible"):
            return results  # key/network problem; the other probes would fail the same way
        if project_id:
            probe(f"open project {project_id}", lambda: f"name {self.projects.get_project(project_id).name!r}")
            probe("list simulations in it",
                  lambda: f"{len(self.simulations.get_simulations(project_id, limit=100).embedded or [])} simulation(s)")
        else:
            def find():
                existing = self.find_project(project_name)
                if existing is None:
                    raise LookupError(
                        f"SIMSCALE_PROJECT_ID is not set and no project named {project_name!r} exists; the first "
                        "run would try to create it (this check does not)")
                return f"found {project_name!r}: {existing.project_id}"
            probe("pipeline project", find)
        # Write permission: an upload slot is what every run starts with.
        # It is never used and costs nothing.
        probe("write access (create upload slot)", lambda: "ok" if self.storage.create_storage().storage_id else "?")

        # The upload slot is account-level; importing geometry needs project
        # write access and meshing/simulating needs billable-action rights.
        resolved = project_id or next((r[2].rsplit(": ", 1)[-1] for r in results
                                       if r[0] == "pipeline project" and r[1]), None)
        if resolved:
            def permissions():
                perms = ProjectPermissionsApi(self.api_client).get_requester_project_permissions(resolved)
                missing = [name for name in ("can_write_project", "can_execute_project_billable_action")
                           if not getattr(perms, name)]
                if missing:
                    raise LookupError(
                        f"this API key lacks {', '.join(missing)} on project {resolved}; it can read but cannot "
                        "import geometry or start meshing/simulations. Create a key with write access "
                        "(User Settings > API Keys) or ask SimScale support to enable API write access.")
                return "can write and run billable actions"
            probe("project permissions", permissions)
        return results

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _download(self, url):
        resp = self.api_client.rest_client.GET(url=url, headers={API_KEY_HEADER: self.api_key}, _preload_content=False)
        return resp.data

    def _poll(self, what, getter, max_seconds, cancel=None, cpu_cap=None, stall_seconds=None):
        """Waits for a SimScale job; with ``cancel`` it is a watchdog.

        The job is cancelled ON SIMSCALE (not just abandoned locally) when it
        runs longer than max_seconds, when its billed CPU hours exceed
        cpu_cap, or when its progress has not moved for stall_seconds.
        """

        start = time.time()
        running_since = None  # billing starts at RUNNING; queue time is free
        obj = getter()
        last = None
        last_change = time.time()
        while obj.status not in TERMINAL:
            if obj.status == "RUNNING" and running_since is None:
                running_since = time.time()
            progress = getattr(obj, "progress", None)
            if (obj.status, progress) != last:
                self.log(f"  {what}: {obj.status}" + (f" {progress:.0%}" if isinstance(progress, float) else ""))
                last = (obj.status, progress)
                last_change = time.time()

            reason = None
            used = _core_hours(obj)
            if running_since is not None and time.time() - running_since > max_seconds:
                reason = f"ran longer than {max_seconds:.0f} s"
            elif time.time() - start > max_seconds + 7200:
                reason = f"still not finished {max_seconds + 7200:.0f} s after submission (queued?)"
            elif cpu_cap is not None and used > cpu_cap:
                reason = f"used {used:.2f} CPU h, over the {cpu_cap:g} CPU h cap"
            elif stall_seconds and obj.status == "RUNNING" and time.time() - last_change > stall_seconds:
                reason = f"progress stuck at {progress:.0%} for {stall_seconds:.0f} s" if isinstance(progress, float) \
                    else f"no progress for {stall_seconds:.0f} s"
            if reason:
                if cancel is None:
                    raise TimeoutError(f"{what} {reason}")
                self.log(f"  {what}: {reason} -- cancelling it on SimScale")
                try:
                    cancel()
                except ApiException as exc:
                    self.log(f"  cancel request failed: {describe_api_error(exc)}")
                obj = self._wait_terminal(getter)
                raise SimScaleError(f"{what} cancelled: {reason} ({_core_hours(obj):.2f} CPU h billed)")

            time.sleep(self.cfg["POLL_SECONDS"])
            obj = getter()
        self.log(f"  {what}: {obj.status}")
        return obj

    def _wait_terminal(self, getter, max_seconds=600):
        """After a cancel request, waits (bounded) for the job to stop."""

        start = time.time()
        obj = getter()
        while obj.status not in TERMINAL and time.time() - start < max_seconds:
            time.sleep(min(self.cfg["POLL_SECONDS"], 15))
            obj = getter()
        return obj

    def _all_mappings(self, project_id, geometry_id, entity_class):
        out, page = [], 1
        while True:
            resp = self.geometries.get_geometry_mappings(
                project_id, geometry_id, _class=entity_class, limit=1000, page=page
            )
            items = resp.embedded or []
            out.extend(items)
            if len(items) < 1000:
                return out
            page += 1

    @staticmethod
    def _labels_of(entity):
        """All human-readable names attached to a mapped entity."""

        labels = set()
        for ref in entity.originate_from or []:
            for name in (ref.body, ref.entity):
                if name:
                    labels.add(name.lower())
            for attr in ref.attribute_list or []:
                if attr.value:
                    labels.add(str(attr.value).lower())
        return labels

    def start_import(self, project_id, name, path, sewing, improve, fmt=None, facet_split=False):
        """Uploads the flow domain and starts its import. Returns (storage_id, import_id).

        STEP files are written in millimetres, STL files in inches. For STL,
        sewing=True joins the separate ``solid`` blocks (imported as separate
        sheet bodies) into solids, keeping each block's name on its faces.
        """

        fmt = fmt or ("STEP" if str(path).lower().endswith((".step", ".stp")) else "STL")
        unit = "mm" if fmt == "STEP" else "in"
        sewing = bool(sewing) and fmt == "STL"
        size_mb = Path(path).stat().st_size / 1e6
        self.log(f"  uploading {Path(path).name} ({size_mb:.0f} MB, {fmt}, sewing={sewing}, improve={improve})")
        storage = self.storage.create_storage()
        with open(path, "rb") as fh:
            self.api_client.rest_client.PUT(
                url=storage.url, headers={"Content-Type": "application/octet-stream"}, body=fh.read()
            )
        imp = self.imports.import_geometry(project_id, GeometryImportRequest(
            name=name,
            location=GeometryImportRequestLocation(storage.storage_id),
            format=fmt,
            input_unit=unit,
            options=GeometryImportRequestOptions(
                facet_split=bool(facet_split), sewing=sewing, improve=improve, optimize_for_lbm_solver=False
            ),
        ))
        return storage.storage_id, imp.geometry_import_id

    def wait_for_import(self, project_id, import_id):
        imp = self._poll("geometry import", lambda: self.imports.get_geometry_import(project_id, import_id), 1800)
        if imp.status != "FINISHED":
            raise SimScaleError(f"geometry import {imp.status}: {getattr(imp, 'failure_reason', '')}")
        return imp.geometry_id

    def list_geometry_ids(self, project_id):
        out, page = [], 1
        while True:
            items = self.geometries.get_geometries(project_id, limit=100, page=page).embedded or []
            out.extend(items)
            if len(items) < 100:
                return out
            page += 1

    def find_flow_volume(self, project_id, exclude_ids):
        """First geometry outside ``exclude_ids`` that is a CAD-mode flow volume, or None."""

        for geo in self.list_geometry_ids(project_id):
            if geo.geometry_id in exclude_ids:
                continue
            faces = self._all_mappings(project_id, geo.geometry_id, "face")
            if manual_flow.is_flow_volume(self._labels_of(f) for f in faces):
                return geo
        return None

    def wait_for_flow_volume(self, project_id, exclude_ids, text, timeout_s, poll_s):
        """Blocks until a flow volume that is not in ``exclude_ids`` shows up in the project."""

        self.log("\n" + text)
        deadline = time.time() + timeout_s
        while True:
            geo = self.find_flow_volume(project_id, set(exclude_ids))
            if geo is not None:
                self.log(f"  found flow-volume geometry {geo.name!r} ({geo.geometry_id})")
                return geo.geometry_id
            if time.time() >= deadline:
                raise SimScaleError(f"no flow-volume geometry appeared within {timeout_s / 3600:.1f} h; "
                                    "re-run to resume this design")
            time.sleep(poll_s)

    def describe_geometry(self, project_id, geometry_id, dump_path=None):
        """Bodies, and which of our face groups ended up on which body."""

        faces = self._all_mappings(project_id, geometry_id, "face")
        # Imported volumes are listed under the "region" class (e.g. B1_TE124126,
        # labelled "Flow region"); "body" is kept as a fallback.
        bodies = (self._all_mappings(project_id, geometry_id, "region")
                  or self._all_mappings(project_id, geometry_id, "body"))
        if dump_path:
            dump = [{"name": e.name, "class": e._class, "labels": sorted(self._labels_of(e))}
                    for e in faces + bodies]
            Path(dump_path).write_text(json.dumps(dump, indent=2) + "\n")

        groups = {g: [] for g in FACE_GROUPS}
        unlabeled = []
        for e in faces:
            labels = self._labels_of(e)
            group = next((g for g in FACE_GROUPS if g in labels), None)
            if group is None:  # CAD-mode flow volume: "Max Z@Flow region" ...
                group = next((manual_flow.WALL_LABELS[l] for l in labels if l in manual_flow.WALL_LABELS), None)
            if group is None:  # STEP walls also carry a distinct colour
                group = next((COLOUR_TO_FACE[c] for c in map(parse_colour, labels) if c in COLOUR_TO_FACE), None)
            (groups[group] if group else unlabeled).append(e.name)
        if not groups["canopy"] and unlabeled and all(groups[g] for g in BOX_FACES):
            # STEP: only the box walls are named; every other face is canopy.
            groups["canopy"], unlabeled = unlabeled, []

        def body_of(entity_name):  # internal names look like B1_TE5 / A1_I26_..._B102_TE196
            return entity_name.rsplit("_TE", 1)[0]

        body_list = [{"name": b.name, "labels": sorted(self._labels_of(b))} for b in bodies]
        face_bodies = sorted({body_of(n) for names in groups.values() for n in names} |
                             {body_of(n) for n in unlabeled})
        if not body_list:
            # A sewn import listed no body mappings in testing; the faces'
            # internal names (B1_TE5 ...) still say which body they are on.
            body_list = [{"name": b, "labels": ["(from face names)"]} for b in face_bodies]
        return {
            "bodies": body_list,
            "groups": groups,
            "group_bodies": {g: sorted({body_of(n) for n in names}) for g, names in groups.items()},
            "unlabeled_faces": unlabeled,
        }

    def resolve_entities(self, project_id, geometry_id, dump_path=None):
        """Maps our STL solid names to SimScale internal face / body names."""

        geo = self.describe_geometry(project_id, geometry_id, dump_path)
        missing = [g for g in REQUIRED_GROUPS if not geo["groups"][g]]
        if missing:
            raise SimScaleError(
                f"Could not find faces for {missing} in the imported geometry. "
                f"The face/body mapping was saved to {dump_path}."
            )
        if len(geo["bodies"]) != 1:
            layout = ", ".join(f"{g}->{'/'.join(b)}" for g, b in geo["group_bodies"].items())
            raise SimScaleError(
                f"expected exactly 1 fluid body, found {len(geo['bodies'])} ({layout}). "
                "SimScale imports each STL 'solid' block as its own body; IMPORT_SEWING must join them "
                "(run `python main.py probe-import` to test import options at no cost)."
            )
        return geo["groups"], geo["bodies"][0]["name"]

    def _assign_air(self, project_id, simulation_id, body_entity):
        groups = self.materials.get_material_groups().embedded
        default = next(g for g in groups if g.group_type == MaterialGroupType.SIMSCALE_DEFAULT)
        air = next(m for m in self.materials.get_materials(material_group_id=default.material_group_id).embedded
                   if m.name == "Air")
        data = self.materials.get_material_data(material_group_id=default.material_group_id, material_id=air.id)
        self.simulations.update_simulation_materials(
            project_id,
            simulation_id,
            MaterialUpdateRequest(operations=[
                MaterialUpdateOperation(
                    path="/materials/fluids",
                    material_data=data,
                    reference=MaterialUpdateOperationReference(
                        material_group_id=default.material_group_id, material_id=air.id
                    ),
                )
            ]),
        )
        spec = self.simulations.get_simulation(project_id, simulation_id)
        spec.model.materials.fluids[0].topological_reference = TopologicalReference(entities=[body_entity])
        self.simulations.update_simulation(project_id, simulation_id, spec)

    def _check(self, what, check):
        errors = [e for e in check.entries if e.severity == "ERROR"]
        warnings = [e for e in check.entries if e.severity == "WARNING"]
        for w in warnings:
            self.log(f"  {what} check warning: {w.message if hasattr(w, 'message') else w}")
        if errors:
            raise SimScaleError(f"{what} setup check failed: {errors}")

    def _estimate_ok(self, what, estimate_fn, default_max):
        """Returns a max runtime (s); raises if the core-hour cap is exceeded."""

        cap = self.cfg.get("MAX_CORE_HOURS_PER_RUN")
        try:
            est = estimate_fn()
        except ApiException as exc:
            if exc.status == 422:
                return default_max, None
            raise
        core_hours = est.compute_resource.value if est.compute_resource is not None else None
        if cap is not None and core_hours is not None and core_hours > cap:
            if what != "simulation" or not self.cfg.get("SKIP_ESTIMATE_GATE"):
                raise SimScaleError(f"{what} estimate {core_hours} core hours exceeds cap {cap}")
            # The estimate is a worst case (the reference run estimated 18.66 and used 3.5); the live cap in _poll still applies.
            self.log(f"  simulation estimate {core_hours:.2f} core hours exceeds cap {cap:g}; estimate gate skipped on request, "
                     f"the run is cancelled live at the cap")
        max_rt = default_max
        if est.duration is not None:
            max_rt = max(default_max, 2 * isodate.parse_duration(est.duration.interval_max).total_seconds())
        return max_rt, core_hours

    # ------------------------------------------------------------------
    # one design
    # ------------------------------------------------------------------
    def run_design(self, project_id, design_id, domain_file, uz_mps, reference_area_in2,
                   reference_length_in, work_dir, state, save_state, mesh_only=False, manual=None):
        """Runs one CFD case end to end and returns the parsed coefficients.

        state: dict persisted by the caller (keys are filled in as SimScale
            objects are created); pass the saved dict back in to resume.
        """

        work_dir = Path(work_dir)
        cfg = self.cfg

        def remember(**kw):
            state.update(kw)
            save_state(state)

        # 1. upload + import --------------------------------------------------
        if "geometry_id" not in state and cfg.get("GEOMETRY_FORMAT") == "MANUAL_FLOW_VOLUME":
            # Canopy only (faceted STL); a person makes the flow volume in the web app.
            if "canopy_geometry_id" not in state:
                remember(geometries_before=[g.geometry_id for g in self.list_geometry_ids(project_id)])
                # sewing=True is what makes the closed STL a solid volume; without it
                # SimScale imports a zero-thickness sheet body and External flow
                # volume (box - solid) fails.
                storage_id, import_id = self.start_import(
                    project_id, f"{design_id}_canopy", domain_file, True, cfg["IMPORT_IMPROVE"], "STL",
                    facet_split=not cfg.get("CANOPY_PRESPLIT"))
                remember(storage_id=storage_id, geometry_import_id=import_id)
                remember(canopy_geometry_id=self.wait_for_import(project_id, import_id))
            text = manual_flow.instructions(design_id, f"{design_id}_canopy", manual["bounds_in"], project_id)
            (work_dir / "MANUAL_STEP.txt").write_text(text)
            remember(geometry_id=self.wait_for_flow_volume(
                project_id, set(state["geometries_before"]) | {state["canopy_geometry_id"]}, text,
                cfg["MANUAL_WAIT_S"], cfg["MANUAL_POLL_S"]))
        if "geometry_id" not in state:
            if "geometry_import_id" not in state:
                storage_id, import_id = self.start_import(
                    project_id, design_id, domain_file, cfg["IMPORT_SEWING"], cfg["IMPORT_IMPROVE"],
                    cfg.get("GEOMETRY_FORMAT"), facet_split=cfg.get("GEOMETRY_FORMAT") == "STL")
                remember(storage_id=storage_id, geometry_import_id=import_id)
            remember(geometry_id=self.wait_for_import(project_id, state["geometry_import_id"]))

        # 2. simulation spec --------------------------------------------------
        if "simulation_id" not in state:
            groups, body = self.resolve_entities(project_id, state["geometry_id"], work_dir / "simscale_entities.json")
            model = build_model(cfg, groups, uz_mps, reference_area_in2, reference_length_in)
            sim = self.simulations.create_simulation(
                project_id, build_simulation_spec(design_id, state["geometry_id"], model)
            )
            self._assign_air(project_id, sim.simulation_id, body)
            remember(simulation_id=sim.simulation_id)
        sim_id = state["simulation_id"]

        # 3. mesh ------------------------------------------------------------
        if "mesh_id" not in state:
            if "mesh_operation_id" not in state:
                op = self.meshes.create_mesh_operation(project_id, MeshOperation(
                    name=f"{design_id} mesh", geometry_id=state["geometry_id"], model=build_mesh_model(cfg)
                ))
                self._check("mesh", self.meshes.check_mesh_operation_setup(
                    project_id, op.mesh_operation_id, simulation_id=sim_id))
                mesh_max, _ = self._estimate_ok(
                    "mesh", lambda: self.meshes.estimate_mesh_operation(project_id, op.mesh_operation_id),
                    cfg["MESH_MAX_RUN_TIME_S"])
                self.meshes.start_mesh_operation(project_id, op.mesh_operation_id, simulation_id=sim_id)
                remember(mesh_operation_id=op.mesh_operation_id, mesh_max_runtime_s=mesh_max)
            mesh_get = lambda: self.meshes.get_mesh_operation(project_id, state["mesh_operation_id"])  # noqa: E731
            try:
                op = self._poll(
                    "meshing", mesh_get,
                    min(state.get("mesh_max_runtime_s", cfg["MESH_MAX_RUN_TIME_S"]), cfg["MESH_TIMEOUT_S"]),
                    cancel=lambda: self.meshes.cancel_mesh_operation(project_id, state["mesh_operation_id"]),
                    cpu_cap=cfg["MESH_MAX_CORE_HOURS"], stall_seconds=cfg["STALL_TIMEOUT_S"])
            finally:
                remember(mesh_core_hours=_core_hours(mesh_get()))
            if op.status != "FINISHED":
                raise SimScaleError(f"meshing {op.status}")
            spec = self.simulations.get_simulation(project_id, sim_id)
            spec.mesh_id = op.mesh_id
            self.simulations.update_simulation(project_id, sim_id, spec)
            remember(mesh_id=op.mesh_id, mesh_core_hours=_core_hours(op))

        if mesh_only:
            mesh = MeshesApi(self.api_client).get_mesh(project_id, state["mesh_id"])
            return {"mesh_id": state["mesh_id"], "cells": mesh.number_of_cells, "nodes": mesh.number_of_nodes,
                    "mesh_core_hours": state.get("mesh_core_hours")}

        # 4. run -------------------------------------------------------------
        if "run_id" not in state:
            self._check("simulation", self.simulations.check_simulation_setup(project_id, sim_id))
            run_max, core_hours = self._estimate_ok(
                "simulation", lambda: self.simulations.estimate_simulation_setup(project_id, sim_id),
                cfg["MAX_RUN_TIME_S"])
            run = self.runs.create_simulation_run(project_id, sim_id, SimulationRun(name="Run 1"))
            self.runs.start_simulation_run(project_id, sim_id, run.run_id)
            remember(run_id=run.run_id, run_max_runtime_s=run_max, estimated_core_hours=core_hours)
        run_get = lambda: self.runs.get_simulation_run(project_id, sim_id, state["run_id"])  # noqa: E731
        run_cap = None
        if cfg.get("MAX_CORE_HOURS_PER_RUN") is not None:
            run_cap = max(0.0, cfg["MAX_CORE_HOURS_PER_RUN"] - float(state.get("mesh_core_hours") or 0.0))
        try:
            run = self._poll("simulation run", run_get,
                             state.get("run_max_runtime_s", cfg["MAX_RUN_TIME_S"]) + 3600,
                             cancel=lambda: self.runs.cancel_simulation_run(project_id, sim_id, state["run_id"]),
                             cpu_cap=run_cap, stall_seconds=cfg["STALL_TIMEOUT_S"])
        finally:
            remember(run_core_hours=_core_hours(run_get()))
        if run.status != "FINISHED":
            raise SimScaleError(f"simulation run {run.status}")

        # 5. results ---------------------------------------------------------
        results = self.runs.get_simulation_run_results(
            project_id, sim_id, state["run_id"], page=1, limit=100, category="FORCE_COEFFICIENTS_PLOT"
        ).embedded or []
        if not results:
            raise SimScaleError("run finished but has no FORCE_COEFFICIENTS_PLOT result")
        csv_text = maybe_unzip(self._download(results[0].download.url))
        (work_dir / "force_coefficients.csv").write_text(csv_text)

        coeffs = parse_force_coefficients(csv_text, cfg["AVERAGING_FRACTION"])
        cd = coeffs.get("cd")
        if cd is None or not math.isfinite(cd) or not (MIN_PLAUSIBLE_CD <= cd <= MAX_PLAUSIBLE_CD):
            # A diverged solver can still end as FINISHED (design_0008 reported Cd = -1.8e6).
            raise SimScaleError(f"implausible Cd {cd}: the solver diverged although SimScale reported the run as finished")
        noise, drift = coeffs.get("cd_noise_rel"), coeffs.get("cd_drift_rel")
        if noise is not None and (noise > MAX_CD_NOISE or abs(drift) > MAX_CD_DRIFT):
            raise SimScaleError(f"not converged: Cd {cd:.3f} is still moving (noise {noise:.1%}, drift {drift:+.1%} against the previous window); "
                                "the reported value is not a steady-state result")
        coeffs["run_duration"] = getattr(run, "duration", None)
        coeffs["core_hours_used"] = core_hours_used(state)
        return coeffs

    # ------------------------------------------------------------------
    # inspection of the manual Kiragami_CFD project
    # ------------------------------------------------------------------
    def dump_project(self, project_id, out_dir):
        """Saves every simulation spec (JSON) and SimScale's own SDK code for it."""

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        sims = self.simulations.get_simulations(project_id, limit=100).embedded or []
        written = []
        for s in sims:
            spec = self.simulations.get_simulation(project_id, s.simulation_id)
            base = out / f"{s.name}_{s.simulation_id}".replace("/", "_").replace(" ", "_")
            base.with_suffix(".json").write_text(
                json.dumps(self.api_client.sanitize_for_serialization(spec), indent=2) + "\n")
            try:
                code = self.simulations.get_simulation_sdk_code(project_id, s.simulation_id)
                base.with_suffix(".py").write_text(code if isinstance(code, str) else str(code))
            except ApiException as exc:
                self.log(f"  (no SDK code for {s.name}: {exc.status})")
            written.append(str(base))
        return written
