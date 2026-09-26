"""SimScale SDK wrapper: upload -> import -> simulation -> mesh -> run -> results.

Every SimScale ID is written into a caller-owned ``state`` dict and
``save_state(state)`` is called right after it is created, so a crash in the
middle of a multi-hour run can be resumed: run_design() skips any step whose
ID is already present and just resumes polling.
"""

import json
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
    MeshOperationsApi,
    Project,
    ProjectsApi,
    SimulationRun,
    SimulationRunsApi,
    SimulationsApi,
    StorageApi,
    TopologicalReference,
)

from .results import maybe_unzip, parse_force_coefficients
from .spec import build_mesh_model, build_model, build_simulation_spec

API_KEY_HEADER = "X-API-KEY"
TERMINAL = ("FINISHED", "CANCELED", "FAILED")
FACE_GROUPS = ("inlet", "outlet", "side_xmin", "side_xmax", "side_ymin", "side_ymax", "canopy")


class SimScaleError(RuntimeError):
    pass


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
            project = self.projects.create_project(
                Project(name=name, description="Automated kirigami parachute Cd optimization", measurement_system="SI")
            )
            pid = project.project_id
            self.log(f"Created SimScale project {name!r}: {pid}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"project_id": pid, "name": name}) + "\n")
        return pid

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _download(self, url):
        resp = self.api_client.rest_client.GET(url=url, headers={API_KEY_HEADER: self.api_key}, _preload_content=False)
        return resp.data

    def _poll(self, what, getter, max_seconds):
        start = time.time()
        obj = getter()
        last = None
        while obj.status not in TERMINAL:
            if time.time() - start > max_seconds:
                raise TimeoutError(f"{what} did not finish within {max_seconds} s")
            progress = getattr(obj, "progress", None)
            if (obj.status, progress) != last:
                self.log(f"  {what}: {obj.status}" + (f" {progress:.0%}" if isinstance(progress, float) else ""))
                last = (obj.status, progress)
            time.sleep(self.cfg["POLL_SECONDS"])
            obj = getter()
        self.log(f"  {what}: {obj.status}")
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

    def resolve_entities(self, project_id, geometry_id, dump_path=None):
        """Maps our STL solid names to SimScale internal face / body names."""

        faces = self._all_mappings(project_id, geometry_id, "face")
        bodies = self._all_mappings(project_id, geometry_id, "body")

        if dump_path:
            dump = [
                {"name": e.name, "class": e._class, "labels": sorted(self._labels_of(e))}
                for e in faces + bodies
            ]
            Path(dump_path).write_text(json.dumps(dump, indent=2) + "\n")

        groups = {g: [] for g in FACE_GROUPS}
        for e in faces:
            labels = self._labels_of(e)
            for g in FACE_GROUPS:
                if g in labels:
                    groups[g].append(e.name)
                    break
        missing = [g for g, names in groups.items() if not names]
        if missing:
            raise SimScaleError(
                f"Could not find faces for {missing} in the imported geometry. "
                f"The face/body mapping was saved to {dump_path} -- check how SimScale "
                "named the STL solids and adjust resolve_entities()."
            )
        if len(bodies) != 1:
            raise SimScaleError(f"expected exactly 1 fluid body, found {len(bodies)} (mapping in {dump_path})")
        return groups, bodies[0].name

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
            raise SimScaleError(f"{what} estimate {core_hours} core hours exceeds cap {cap}")
        max_rt = default_max
        if est.duration is not None:
            max_rt = max(default_max, 2 * isodate.parse_duration(est.duration.interval_max).total_seconds())
        return max_rt, core_hours

    # ------------------------------------------------------------------
    # one design
    # ------------------------------------------------------------------
    def run_design(self, project_id, design_id, domain_stl, uz_mps, reference_area_in2,
                   reference_length_in, work_dir, state, save_state):
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
        if "geometry_id" not in state:
            if "geometry_import_id" not in state:
                self.log("  uploading flow domain STL")
                storage = self.storage.create_storage()
                with open(domain_stl, "rb") as fh:
                    self.api_client.rest_client.PUT(
                        url=storage.url, headers={"Content-Type": "application/octet-stream"}, body=fh.read()
                    )
                imp = self.imports.import_geometry(project_id, GeometryImportRequest(
                    name=design_id,
                    location=GeometryImportRequestLocation(storage.storage_id),
                    format="STL",
                    input_unit="in",
                    options=GeometryImportRequestOptions(
                        facet_split=False, sewing=False, improve=True, optimize_for_lbm_solver=False
                    ),
                ))
                remember(storage_id=storage.storage_id, geometry_import_id=imp.geometry_import_id)
            imp = self._poll("geometry import",
                             lambda: self.imports.get_geometry_import(project_id, state["geometry_import_id"]), 1800)
            if imp.status != "FINISHED":
                raise SimScaleError(f"geometry import {imp.status}: {getattr(imp, 'failure_reason', '')}")
            remember(geometry_id=imp.geometry_id)

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
            op = self._poll("meshing", lambda: self.meshes.get_mesh_operation(project_id, state["mesh_operation_id"]),
                            state.get("mesh_max_runtime_s", cfg["MESH_MAX_RUN_TIME_S"]))
            if op.status != "FINISHED":
                raise SimScaleError(f"meshing {op.status}")
            spec = self.simulations.get_simulation(project_id, sim_id)
            spec.mesh_id = op.mesh_id
            self.simulations.update_simulation(project_id, sim_id, spec)
            remember(mesh_id=op.mesh_id)

        # 4. run -------------------------------------------------------------
        if "run_id" not in state:
            self._check("simulation", self.simulations.check_simulation_setup(project_id, sim_id))
            run_max, core_hours = self._estimate_ok(
                "simulation", lambda: self.simulations.estimate_simulation_setup(project_id, sim_id),
                cfg["MAX_RUN_TIME_S"])
            run = self.runs.create_simulation_run(project_id, sim_id, SimulationRun(name="Run 1"))
            self.runs.start_simulation_run(project_id, sim_id, run.run_id)
            remember(run_id=run.run_id, run_max_runtime_s=run_max, estimated_core_hours=core_hours)
        run = self._poll("simulation run",
                         lambda: self.runs.get_simulation_run(project_id, sim_id, state["run_id"]),
                         state.get("run_max_runtime_s", cfg["MAX_RUN_TIME_S"]) + 3600)
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
        coeffs["run_duration"] = getattr(run, "duration", None)
        coeffs["compute_resource"] = (
            run.compute_resource.value if getattr(run, "compute_resource", None) is not None else None
        )
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
