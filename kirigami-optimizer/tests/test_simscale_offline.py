from simscale_sdk import ApiClient

import config
from simscale.results import parse_force_coefficients
from simscale.spec import build_mesh_model, build_model, build_simulation_spec


def test_spec_serializes_like_manual_setup():
    ents = {k: [f"B1_TE{i}"] for i, k in enumerate(
        ["inlet", "outlet", "side_xmin", "side_xmax", "side_ymin", "side_ymax", "canopy"])}
    spec = ApiClient().sanitize_for_serialization(
        build_simulation_spec("d", "g", build_model(config.SIMSCALE, ents, -6.148, 74.95, 9.77)))
    model = spec["model"]
    assert model["turbulenceModel"] == "KOMEGASST"
    bcs = {bc["name"]: bc for bc in model["boundaryConditions"]}
    assert bcs["Wall 3"]["velocity"]["type"] == "SLIP"
    assert len(bcs["Wall 3"]["topologicalReference"]["entities"]) == 4
    assert bcs["Velocity inlet 2"]["velocity"]["value"]["value"]["z"]["value"] == -6.148
    fc = model["resultControl"]["forcesMoments"][0]
    assert fc["dragDirection"]["value"] == {"x": 0, "y": 0, "z": -1}
    assert fc["topologicalReference"]["entities"] == ["B1_TE6"]
    mesh = ApiClient().sanitize_for_serialization(build_mesh_model(config.SIMSCALE))
    assert mesh["sizing"]["fineness"] == 4.5 and mesh["hexCore"] and mesh["physicsBasedMeshing"]


def test_parse_force_coefficients():
    rows = "".join(f"{i},0.01,{1.0 if i <= 450 else 1.1},0.02,0,0\n" for i in range(1, 501))
    out = parse_force_coefficients(
        "Time (s),Moment coefficient,Drag coefficient,Lift coefficient,Cl(f),Cl(r)\n" + rows, 0.1)
    assert out["cd"] == 1.1 and out["cd_final"] == 1.1
    assert out["cl"] == 0.02 and out["cm"] == 0.01 and out["n_iterations"] == 500
