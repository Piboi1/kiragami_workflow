import json

import pytest

from onshape.client import OnshapeBudgetError, OnshapeClient


def test_request_counter_persists_and_caps(tmp_path, monkeypatch):
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "a")
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "b")
    usage = tmp_path / "usage.json"
    client = OnshapeClient(usage, request_cap=3)
    client._count(2)
    assert json.loads(usage.read_text())["total"] == 2
    assert OnshapeClient(usage, request_cap=3).requests_used() == 2
    client._count(1)
    with pytest.raises(OnshapeBudgetError):
        client._count(1)
    assert json.loads(usage.read_text())["total"] == 3
