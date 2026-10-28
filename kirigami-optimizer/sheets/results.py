"""Makes the results sheet easy to read: an `outcome` label for every run, and a ranked `results` tab of the runs that count.

The raw log (tab `runs`) is never reordered or deleted; every run keeps its row. On top of it:
  * column `outcome` (valid / mesh failed / runaway ... / never ran) next to `status`, with row colours and a filter,
  * tab `results`: the valid designs ranked by Cd (with predicted fall speed and the parameters), then valid runs that used
    different solver settings (not comparable) below them.
"""

import math

DEFAULT_SETTINGS = "fineness 4.5; 500 it; default numerics"
CD_RANGE = (0.05, 4.0)


def outcome_of(record):
    """One short human label for a ledger record."""

    status, err = record.get("status"), (record.get("error") or "")
    cd = record.get("cd")
    if status == "ok" and cd is not None and CD_RANGE[0] <= cd <= CD_RANGE[1]:
        return "valid"
    if status == "cancelled":
        return "cancelled by user"
    if status == "infeasible":
        return "infeasible geometry"
    if "meshing FAILED" in err:
        return "mesh failed"
    if "diverging" in err:
        return "runaway (stopped early)"
    if "not converged" in err:
        return "not converged"
    if "exceeds cap" in err:
        return "refused (over the CPU cap)"
    if "ApiException" in err or "math domain" in err:
        return "never ran (setup error)"
    if "implausible Cd" in err or "simulation run FAILED" in err or status == "ok":   # "ok" here means an implausible Cd got through
        return "solver diverged"
    return "failed"


def _settings(record):
    return record.get("settings") or DEFAULT_SETTINGS


def results_table(records, air_density):
    """(header, ranked rows, other-settings rows) for the `results` tab, from ledger records."""

    header = ["rank", "design_id", "Cd", "predicted fall speed (m/s)", "rings", "cuts per ring", "spacing exponent",
              "overlap fraction", "inner radius (in)", "depth (in)", "Cd x area (in2)", "solver settings", "CPU hours", "note"]
    good, other = [], []
    for r in records:
        if outcome_of(r) != "valid":
            continue
        p, s, v = r.get("params") or {}, r.get("solid") or {}, r.get("velocity") or {}
        area_in2 = s.get("reference_area_in2")
        speed = ""
        if area_in2 and v.get("mass_kg"):
            speed = round(math.sqrt(2 * v["mass_kg"] * 9.81 / (air_density * r["cd"] * area_in2 * 0.00064516)), 3)
        sim = r.get("simscale") or {}
        hours = round((sim.get("mesh_core_hours") or 0) + (sim.get("run_core_hours") or 0), 2)
        note = "Run 11 design (baseline)" if r["design_id"] == "design_0002" else ""
        if not (24 < (p.get("NUM_RINGS") or 0) < 34) and r["design_id"] != "design_0002":
            note = (note + "; " if note else "") + "outside the current search space"
        row = [None, r["design_id"], round(r["cd"], 4), speed, p.get("NUM_RINGS"), p.get("CUTS_PER_RING"),
               _round(p.get("RING_SPACING_EXPONENT")), _round(p.get("RING_OVERLAP_FRACTION")), _round(p.get("INNER_RADIUS")),
               _round(s.get("canopy_depth_in"), 1), _round(r["cd"] * area_in2, 1) if area_in2 else "", _settings(r), hours, note]
        (good if _settings(r) == DEFAULT_SETTINGS else other).append(row)
    good.sort(key=lambda row: -row[2])
    for i, row in enumerate(good, start=1):
        row[0] = i
    other.sort(key=lambda row: -row[2])
    for row in other:
        row[0] = ""
        row[-1] = (row[-1] + "; " if row[-1] else "") + "different settings: not comparable with the ranking"
    return header, good, other


def _round(x, n=3):
    return round(x, n) if isinstance(x, (int, float)) else ("" if x is None else x)


def refresh_results_tab(spreadsheet, records, air_density):
    """Rewrites the `results` tab. Returns the number of ranked designs."""

    header, good, other = results_table(records, air_density)
    rows = [header] + good
    if other:
        rows += [[""] * len(header), ["valid, but with other solver settings (not comparable with the ranking above)"] + [""] * (len(header) - 1)]
        rows += other
    try:
        ws = spreadsheet.worksheet("results")
        ws.clear()
    except Exception:
        ws = spreadsheet.add_worksheet(title="results", rows=200, cols=len(header) + 2)
    if ws.col_count < len(header):
        ws.add_cols(len(header) - ws.col_count)
    ws.update(range_name="A1", values=rows, value_input_option="RAW")
    sid = ws.id
    req = [
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.85, "green": 0.92, "blue": 0.85}}},
                        "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
        {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 2},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}}, "fields": "userEnteredFormat.textFormat"}},
        {"autoResizeDimensions": {"dimensions": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": len(header)}}},
    ]
    spreadsheet.batch_update({"requests": req})
    return len(good)


def format_runs_tab(spreadsheet, ws, outcome_col_index):
    """Freezes the header and two id columns, adds a filter and colours rows by outcome (valid = green, everything else grey)."""

    sid = ws.id
    meta = spreadsheet.fetch_sheet_metadata()
    sheet = next(s for s in meta["sheets"] if s["properties"]["sheetId"] == sid)
    req = [{"deleteConditionalFormatRule": {"sheetId": sid, "index": 0}} for _ in sheet.get("conditionalFormats", [])]
    ncols = ws.col_count
    col_letter = _col_letter(outcome_col_index + 1)
    full = {"sheetId": sid, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": ncols}
    req += [
        {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 2}},
                                   "fields": "gridProperties(frozenRowCount,frozenColumnCount)"}},
        {"addConditionalFormatRule": {"index": 0, "rule": {"ranges": [full], "booleanRule": {
            "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": f'=${col_letter}2="valid"'}]},
            "format": {"backgroundColor": {"red": 0.78, "green": 0.92, "blue": 0.78}, "textFormat": {"bold": True}}}}}},
        {"addConditionalFormatRule": {"index": 1, "rule": {"ranges": [full], "booleanRule": {
            "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": f'=AND(${col_letter}2<>"valid",${col_letter}2<>"")'}]},
            "format": {"textFormat": {"foregroundColor": {"red": 0.55, "green": 0.55, "blue": 0.55}}}}}}},
        {"setBasicFilter": {"filter": {"range": {"sheetId": sid, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": ncols}}}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}}, "fields": "userEnteredFormat.textFormat"}},
    ]
    spreadsheet.batch_update({"requests": req})


def _col_letter(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s
