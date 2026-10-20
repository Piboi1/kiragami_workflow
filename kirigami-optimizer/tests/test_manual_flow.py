from types import SimpleNamespace as NS

import pytest

from simscale import manual_flow
from simscale.runner import SimScaleClient, SimScaleError

WALLS = ["max x", "max y", "min x", "min y", "max z", "min z"]


def face(name, *labels):
    return NS(name=name, _class="face", originate_from=[NS(body="Flow region", entity=l, attribute_list=[])
                                                        for l in labels])


def flow_volume_faces():
    # as in the manual Kiragami_CFD geometry: six labelled walls, other faces unlabelled
    walls = [face(f"B2_TE{i}", f"{w.title().replace('Max', 'Max').replace('Min', 'Min')}@Flow region".replace(
        w.title(), w.title())) for i, w in enumerate(WALLS, 2)]
    canopy = [NS(name=f"B2_TE{1000 + i}", _class="face", originate_from=[]) for i in range(50)]
    return walls + canopy


class FakeClient(SimScaleClient):
    def __init__(self, geometries, faces_by_geometry):
        self.log = lambda *_: None
        self._geos, self._faces = geometries, faces_by_geometry

    def list_geometry_ids(self, project_id):
        return self._geos

    def _all_mappings(self, project_id, geometry_id, entity_class):
        if entity_class == "face":
            return self._faces[geometry_id]
        return [NS(name="B2_TE5", _class="region", originate_from=[NS(body="Flow region", entity=None,
                                                                      attribute_list=[])])]


def test_is_flow_volume_needs_all_six_walls():
    labels = [{w + "@flow region"} for w in WALLS]
    assert manual_flow.is_flow_volume(labels)
    assert not manual_flow.is_flow_volume(labels[:5])
    assert not manual_flow.is_flow_volume([set()] * 100)


def test_box_summary_and_instructions():
    bounds = ([-25.83, -25.83, -70.0], [25.83, 25.83, 31.13])
    box = manual_flow.box_summary(bounds)
    assert box["m"][0][2] == -1.778 and box["m"][1][2] == 0.790702
    text = manual_flow.instructions("d1", "d1_canopy", bounds, "123")
    assert "d1_canopy" in text and "+31.130" in text and "Max Z is the inlet" in text


def test_find_flow_volume_skips_known_and_canopy_only_geometries():
    canopy = NS(geometry_id="canopy", name="d1_canopy")
    old = NS(geometry_id="old", name="older")
    new = NS(geometry_id="new", name="d1_flow")
    faces = {"canopy": [NS(name="B1_TE1", _class="face", originate_from=[])] * 3,
             "old": flow_volume_faces(), "new": flow_volume_faces()}
    client = FakeClient([old, canopy, new], faces)
    assert client.find_flow_volume("p", {"old", "canopy"}).geometry_id == "new"
    assert client.find_flow_volume("p", {"old", "canopy", "new"}) is None


def test_describe_geometry_names_walls_and_canopy():
    client = FakeClient([], {"g": flow_volume_faces()})
    geo = client.describe_geometry("p", "g")
    assert [len(geo["groups"][g]) for g in ("inlet", "outlet", "side_xmin", "side_xmax", "side_ymin", "side_ymax")] \
        == [1] * 6
    assert len(geo["groups"]["canopy"]) == 50 and not geo["unlabeled_faces"]
    groups, body = client.resolve_entities("p", "g")
    assert body == "B2_TE5" and groups["inlet"] == ["B2_TE6"]  # "max z" is the 5th wall


def test_wait_for_flow_volume_times_out():
    client = FakeClient([], {})
    with pytest.raises(SimScaleError, match="no flow-volume geometry"):
        client.wait_for_flow_volume("p", set(), "steps", 0, 0)


def test_manual_import_uses_sewing(tmp_path, monkeypatch):
    """Without sewing SimScale imports the canopy as a sheet body and External flow volume fails."""

    calls = {}

    class C(FakeClient):
        def start_import(self, project_id, name, path, sewing, improve, fmt=None, facet_split=False):
            calls.update(sewing=sewing, fmt=fmt, facet_split=facet_split)
            raise RuntimeError("stop after import request")

    client = C([], {})
    client.cfg = {"GEOMETRY_FORMAT": "MANUAL_FLOW_VOLUME", "IMPORT_IMPROVE": True}
    with pytest.raises(RuntimeError, match="stop after"):
        client.run_design("p", "d", "canopy.stl", -6.0, 70.0, 9.7, tmp_path, {}, lambda s: None,
                          manual={"bounds_in": ([-1, -1, -1], [1, 1, 1])})
    assert calls == {"sewing": True, "fmt": "STL", "facet_split": True}
