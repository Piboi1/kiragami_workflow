"""Onshape REST wrapper for the STL round trip that repairs the canopy mesh.

Flow (what the manual workflow does in the web app):
    upload STL as a blob + translate  ->  Part Studio with Part 1 + surfaces
    export the Part Studio as STL (basic settings)  ->  hand that to SimScale

The Onshape account is a FREE plan: documents are public and the API allows
about 2,500 requests per year, so every request is counted in a persistent
file and the client refuses to continue past a soft cap.
"""

import json
import os
import time
from pathlib import Path

import requests

API = "https://cad.onshape.com/api/v6"


class OnshapeError(RuntimeError):
    pass


class OnshapeBudgetError(OnshapeError):
    pass


class OnshapeClient:
    def __init__(self, usage_file, base_url="https://cad.onshape.com", request_cap=2000, log=print):
        access, secret = os.getenv("ONSHAPE_ACCESS_KEY", ""), os.getenv("ONSHAPE_SECRET_KEY", "")
        if not (access and secret):
            raise OnshapeError("ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY are not set")
        self.base = base_url.rstrip("/") + "/api/v6"
        self.session = requests.Session()
        self.session.auth = (access, secret)
        self.usage_file = Path(usage_file)
        self.request_cap = request_cap
        self.log = log
        self.session_requests = 0

    # -- request accounting ------------------------------------------------
    def requests_used(self):
        try:
            return int(json.loads(self.usage_file.read_text()).get("total", 0))
        except (OSError, ValueError):
            return 0

    def _count(self, n=1):
        total = self.requests_used()
        if total + n > self.request_cap:
            raise OnshapeBudgetError(f"Onshape request counter at {total} (cap {self.request_cap}); stopping to "
                                     "protect the free plan's 2,500/year limit")
        self.usage_file.parent.mkdir(parents=True, exist_ok=True)
        self.usage_file.write_text(json.dumps({"total": total + n}))
        self.session_requests += n

    def _call(self, method, path, **kw):
        self._count()
        headers = {"Accept": "application/json", **kw.pop("headers", {})}
        resp = self.session.request(method, self.base + path, headers=headers, timeout=kw.pop("timeout", 120), **kw)
        if resp.status_code >= 400:
            raise OnshapeError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
        return resp

    # -- API ---------------------------------------------------------------
    def session_info(self):
        return self._call("GET", "/users/sessioninfo").json()

    def create_document(self, name, is_public=True):
        d = self._call("POST", "/documents", json={"name": name, "isPublic": is_public}).json()
        return d["id"], d["defaultWorkspace"]["id"]

    def default_workspace(self, did):
        return self._call("GET", f"/documents/{did}").json()["defaultWorkspace"]["id"]

    def elements(self, did, wid):
        return self._call("GET", f"/documents/d/{did}/w/{wid}/elements").json()

    def delete_element(self, did, wid, eid):
        self._call("DELETE", f"/elements/d/{did}/w/{wid}/e/{eid}")

    def upload_stl(self, did, wid, path, unit="inch", allow_faulty=True):
        """Uploads an STL and starts the translation into a Part Studio. Returns (translation id, blob element id)."""

        path = Path(path)
        with open(path, "rb") as fh:
            resp = self._call(
                "POST", f"/blobelements/d/{did}/w/{wid}",
                files={"file": (path.name, fh, "application/octet-stream")},
                data={"encodedFilename": path.name, "formatName": "STL", "flattenAssemblies": "false",
                      "yAxisIsUp": "false", "unit": unit, "translate": "true", "allowFaultyParts": "true" if allow_faulty else "false"},
                timeout=600)
        body = resp.json()
        return body.get("translationId") or body["id"], body["id"]

    def wait_translation(self, tid, timeout_s=1800, poll_s=10):
        """Polls a translation; returns the new element ids (the imported Part Studio)."""

        t0 = time.time()
        while True:
            t = self._call("GET", f"/translations/{tid}").json()
            state = t.get("requestState")
            if state == "DONE":
                return t.get("resultElementIds") or []
            if state == "FAILED":
                raise OnshapeError(f"translation failed: {t.get('failureReason')}")
            if time.time() - t0 > timeout_s:
                raise OnshapeError(f"translation still {state} after {timeout_s}s")
            time.sleep(poll_s)

    def part_count(self, did, wid, eid):
        return len(self._call("GET", f"/parts/d/{did}/w/{wid}/e/{eid}").json())

    def export_partstudio_stl(self, did, wid, eid, out_path, units="inch", timeout_s=1800, poll_s=5):
        """Exports the entire Part Studio as a binary STL with the default tessellation settings.

        Uses the asynchronous translation (result downloaded from cad.onshape.com). The direct
        .../stl endpoint redirects to a regional host (cad-usw2.onshape.com) that the sandbox
        proxy blocks.
        """

        t = self._call("POST", f"/partstudios/d/{did}/w/{wid}/e/{eid}/translations",
                       json={"formatName": "STL", "storeInDocument": False, "grouping": True, "units": units,
                             "mode": "binary"}).json()
        t0 = time.time()
        while t.get("requestState") == "ACTIVE":
            if time.time() - t0 > timeout_s:
                raise OnshapeError(f"export still ACTIVE after {timeout_s}s")
            time.sleep(poll_s)
            t = self._call("GET", f"/translations/{t['id']}").json()
        if t.get("requestState") != "DONE" or not t.get("resultExternalDataIds"):
            raise OnshapeError(f"export failed: {t.get('requestState')} {t.get('failureReason')}")
        resp = self._call("GET", f"/documents/d/{did}/externaldata/{t['resultExternalDataIds'][0]}",
                          headers={"Accept": "application/octet-stream"}, timeout=900)
        Path(out_path).write_bytes(resp.content)
        return Path(out_path)
