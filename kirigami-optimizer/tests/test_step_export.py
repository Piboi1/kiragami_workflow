"""STEP flow domain: one valid solid with the canopy as a void, named/coloured walls."""

from types import SimpleNamespace

import pytest

from generator import generate_pattern
from solid import build_flow_domain, build_solid
from solid.step_export import BOX_FACES, FACE_COLOURS, colour_key, parse_colour, read_step_summary, write_step


@pytest.fixture(scope="module")
def step_file(tmp_path_factory):
    report = generate_pattern({"NUM_RINGS": 16, "CUTS_PER_RING": 6, "RING_OVERLAP_FRACTION": 0.6,
                               "INNER_OVERLAP_RATIO": 0.3}, write_files=False)
    canopy, _ = build_solid(report, {"CUTS_PER_RING": 6, "VERTICAL_DROP": 6.0})
    fluid, labels, _ = build_flow_domain(canopy, 10.0)
    path = tmp_path_factory.mktemp("step") / "domain.step"
    info = write_step(fluid, labels, path)
    return path, info, fluid


def test_step_is_one_valid_solid_with_a_void(step_file):
    path, info, fluid = step_file
    assert info["brep_valid"]
    assert abs(info["brep_volume_in3"] - fluid.volume) < 1e-6 * fluid.volume
    summary = read_step_summary(path)
    assert summary["solids"] == 1 and summary["shells"] == 2 and summary["valid"]


def test_step_walls_are_named_and_coloured(step_file):
    path, info, _ = step_file
    assert info["step_box_faces"] == 6
    named = read_step_summary(path)["named_faces"]
    assert set(named) == set(BOX_FACES)
    for name in BOX_FACES:
        assert named[name]["faces"] == 1
        assert named[name]["colours"] == [colour_key(FACE_COLOURS[name])]


def test_parse_colour_formats():
    assert parse_colour("magenta") == "255,0,255"
    assert parse_colour("#00FF00") == "0,255,0"
    assert parse_colour("0.0 0.0 1.0") == "0,0,255"
    assert parse_colour("255, 255, 0") == "255,255,0"
    assert parse_colour("inlet") is None


def _entity(name, *labels):
    attrs = [SimpleNamespace(attribute="SDL/TYSA_COLOUR", value=v) for v in labels]
    return SimpleNamespace(name=name, _class="face",
                           originate_from=[SimpleNamespace(body=None, entity=None, attribute_list=attrs)])


@pytest.mark.parametrize("by", ["name", "colour"])
def test_runner_maps_step_faces(by, monkeypatch):
    from simscale.runner import SimScaleClient

    faces = [_entity(f"B1_TE{i}", name if by == "name" else ",".join(str(int(c * 255)) for c in FACE_COLOURS[name]))
             for i, name in enumerate(BOX_FACES)]
    faces += [_entity(f"B1_TE{100 + i}") for i in range(5)]  # canopy faces: no name, no colour
    client = SimScaleClient.__new__(SimScaleClient)
    monkeypatch.setattr(client, "_all_mappings",
                        lambda p, g, cls: faces if cls == "face" else
                        [SimpleNamespace(name="B1_TE999", originate_from=[])] if cls == "region" else [],
                        raising=False)
    groups, body = client.resolve_entities("p", "g")
    assert body == "B1_TE999"
    assert groups["inlet"] == ["B1_TE0"] and len(groups["canopy"]) == 5
    assert all(len(groups[n]) == 1 for n in BOX_FACES)
