import pytest

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
    # SimScale rejects specs without these (spec-validation-error on run 3)
    numerics = model["numerics"]
    assert "solvers" in numerics
    for block in ("timeDifferentiation", "gradient", "divergence", "laplacian", "interpolation",
                  "surfaceNormalGradient"):
        assert block in numerics["schemes"]
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


def test_numerics_match_the_working_manual_runs():
    """Runs 1-4 of the manual simulation diverged on SimScale's default numerics; runs 5+ used these."""

    ents = {k: [f"B1_TE{i}"] for i, k in enumerate(
        ["inlet", "outlet", "side_xmin", "side_xmax", "side_ymin", "side_ymax", "canopy"])}
    model = ApiClient().sanitize_for_serialization(build_model(config.SIMSCALE, ents, -6.148, 72.9, 9.6))
    vel = model["numerics"]["solvers"]["velocitySolver"]
    assert vel["type"] == "PBICGStab" and vel["preconditioner"] == {"type": "DILU"}
    assert vel["absoluteTolerance"] == 1e-8 and vel["relativeTolerance"] == 0.01
    div = model["numerics"]["schemes"]["divergence"]
    assert div["forDefault"] == {"type": "GAUSS_LINEARUPWINDV_UNLIMITED", "limiterCoefficient": 1.0}
    assert div["div_Phi_velocity"] == {"type": "BOUNDED_GAUSS_UPWIND"}


def test_parse_force_coefficients_from_api_csv():
    """The header the SimScale API actually returns (seen on the first real download)."""

    head = "Time (s),FORCE_COEFFICIENT_CD,FORCE_COEFFICIENT_CL,FORCE_COEFFICIENT_CLF,FORCE_COEFFICIENT_CLR,FORCE_COEFFICIENT_CM\n"
    rows = "".join(f'"{i:20d}",{1.0 if i <= 450 else 1.1:.6e},2.0e-04,-1.0e-04,3.0e-04,-5.0e-05\n' for i in range(1, 501))
    out = parse_force_coefficients(head + rows, 0.1)
    assert out["cd"] == pytest.approx(1.1) and out["cl"] == pytest.approx(2.0e-04) and out["cm"] == pytest.approx(-5.0e-05)
    assert out["cl_front"] == pytest.approx(-1.0e-04) and out["cl_rear"] == pytest.approx(3.0e-04)
    assert out["n_iterations"] == 500
