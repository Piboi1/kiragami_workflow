"""Parses SimScale's FORCE_COEFFICIENTS_PLOT CSV into Cd / Cl / Cm.

The plot's series are "Moment coefficient", "Drag coefficient",
"Lift coefficient", "Cl(f)" and "Cl(r)". Column headers are matched loosely
(case-insensitive; full names or Cd/Cl/Cm abbreviations) so small format
changes on SimScale's side don't break parsing.
"""

import csv
import io
import zipfile

import numpy as np


def _classify(header):
    h = header.strip().lower().replace(" ", "")
    if "cl(f)" in h:
        return "cl_front"
    if "cl(r)" in h:
        return "cl_rear"
    if "time" in h or "iteration" in h or h in ("t", "step"):
        return "time"
    if "drag" in h or h.startswith("cd"):
        return "cd"
    if "lift" in h or h.startswith("cl"):
        return "cl"
    if "moment" in h or h.startswith("cm"):
        return "cm"
    return None


def maybe_unzip(data):
    """Returns CSV text from raw download bytes (plain or zipped)."""

    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            return zf.read(name).decode("utf-8")
    return data.decode("utf-8")


def parse_force_coefficients(csv_text, averaging_fraction=0.1):
    """Returns dict: cd, cl, cm (+ cl_front, cl_rear if present), each as the
    mean over the last ``averaging_fraction`` of iterations, plus *_final
    (last value), *_window_std, n_iterations and the raw column mapping."""

    rows = [r for r in csv.reader(io.StringIO(csv_text)) if r and any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ValueError("force coefficient CSV has no data rows")
    header, data = rows[0], rows[1:]

    columns = {}
    for i, name in enumerate(header):
        key = _classify(name)
        if key and key not in columns:
            columns[key] = i
    if "cd" not in columns:
        raise ValueError(f"no drag-coefficient column found in header {header}")

    values = np.array([[float(c) if c.strip() else np.nan for c in r] for r in data], dtype=float)
    n = len(values)
    window = max(1, int(round(n * averaging_fraction)))

    out = {"n_iterations": n, "averaging_window": window, "columns": {k: header[i] for k, i in columns.items()}}
    for key, idx in columns.items():
        if key == "time":
            continue
        series = values[:, idx]
        tail = series[-window:]
        out[key] = float(np.nanmean(tail))
        out[f"{key}_final"] = float(series[~np.isnan(series)][-1])
        out[f"{key}_window_std"] = float(np.nanstd(tail))
    return out
