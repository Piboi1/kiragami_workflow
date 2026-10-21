"""STL -> Onshape import -> Part Studio STL export, reusing one document."""

import json
from pathlib import Path

from .client import OnshapeError


def round_trip(client, stl_path, out_path, state_file, doc_name="kirigami_optimizer", unit="inch", log=print, allow_faulty=True):
    """Overwrites the working document's previous import with stl_path and exports the Part Studio.

    The document is created on first use (public: free plan) and its ids kept in
    state_file. Returns a dict with the exported file and import statistics.
    """

    state_file = Path(state_file)
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    save = lambda: state_file.write_text(json.dumps(state, indent=2))  # noqa: E731
    if "document_id" not in state:
        state["document_id"], state["workspace_id"] = client.create_document(doc_name, is_public=True)
        log(f"created Onshape document {doc_name!r} ({state['document_id']})")
        save()
    did, wid = state["document_id"], state["workspace_id"]

    for eid in state.pop("element_ids", []):  # drop the previous design's blob + Part Studio
        try:
            client.delete_element(did, wid, eid)
        except OnshapeError as exc:
            log(f"  could not delete old element {eid}: {exc}")
    save()

    before = {e["id"] for e in client.elements(did, wid)}
    tid, blob_id = client.upload_stl(did, wid, stl_path, unit=unit, allow_faulty=allow_faulty)
    state["element_ids"] = [blob_id]
    save()
    log(f"uploaded {Path(stl_path).name}; translation {tid}")
    result_ids = client.wait_translation(tid)
    after = client.elements(did, wid)
    state["element_ids"] = [e["id"] for e in after if e["id"] not in before]
    save()
    studios = [e for e in after if e["id"] in set(result_ids or state["element_ids"]) and e.get("elementType") == "PARTSTUDIO"]
    if not studios:
        studios = [e for e in after if e["id"] in state["element_ids"] and e.get("elementType") == "PARTSTUDIO"]
    if not studios:
        raise OnshapeError(f"import produced no Part Studio (new elements: {[(e['name'], e.get('elementType')) for e in after if e['id'] in state['element_ids']]})")
    eid = studios[0]["id"]
    n_parts = client.part_count(did, wid, eid)
    out = client.export_partstudio_stl(did, wid, eid, out_path, units=unit)
    log(f"Part Studio {studios[0]['name']!r}: {n_parts} part(s); exported {out} ({out.stat().st_size:,} bytes)")
    return {"file": str(out), "parts": n_parts, "document_id": did, "element_id": eid,
            "requests_used": client.requests_used()}
