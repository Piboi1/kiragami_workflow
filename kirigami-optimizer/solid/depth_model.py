"""Predicts the deployed canopy depth (VERTICAL_DROP) from the cut pattern.

The 3D canopy is a prescribed paraboloid, so its depth must come from
somewhere. Instead of one fixed value for every design, depth is fitted to
the hanging-depth measurements in data/depth_measurements.csv (one row per
physical parachute, measured under the payload).

Model: depth = sum_j c_j * feature_j  (+ intercept, optional), least squares.
Features are computed from the generator output, so any pattern parameter
can be used once the data covers it. With the 4 current measurements (all
8 cuts per ring, 12-16 deg overlap) only one coefficient is justified;
leave-one-out comparison picked

    depth = c * (NUM_RINGS - 1)          ("n_bands")

i.e. every ring-to-ring band opens like a hinge and adds about the same
lift. Add rows to the CSV (e.g. the cpr4..cpr12 and overlap test chutes)
and extend DEPTH_MODEL["FEATURES"] to capture more dependencies.
"""

import csv
import math
from pathlib import Path

import numpy as np

from generator import generate_pattern


def pattern_features(report):
    """Candidate depth predictors computed from a generate_pattern() report."""

    rings = report["rings"]
    p = report["parameters"]
    r = np.array([g["radius_inches"] for g in rings])
    overlap = np.radians([g["actual_overlap_degrees"] for g in rings])
    return {
        "n_bands": float(len(rings) - 1),
        "n_rings": float(len(rings)),
        "cuts_per_ring": float(p["CUTS_PER_RING"]),
        "mean_overlap_deg": float(np.degrees(overlap).mean()),
        "sum_overlap_arc_in": float((r * overlap).sum()),
        "max_cut_radius_in": float(p["OUTER_RADIUS"] - p["RIM_WIDTH"]),
    }


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def load_measurements(path):
    """Rows of the CSV with numbers parsed; blank cells are dropped."""

    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("depth_in") or "").strip():
                continue
            rows.append({k: _num(v) for k, v in row.items() if v is not None and str(v).strip() != ""})
    return rows


class DepthModel:
    def __init__(self, cfg):
        self.cfg = cfg
        self.features = list(cfg["FEATURES"])
        self.intercept = bool(cfg.get("INTERCEPT", False))
        self.rows = load_measurements(cfg["DATA_FILE"])
        n_params = len(self.features) + int(self.intercept)
        if len(self.rows) < n_params + 1:
            raise ValueError(f"depth model needs at least {n_params + 1} measurements, "
                             f"found {len(self.rows)} in {cfg['DATA_FILE']}")

        self.names = [str(r.get("name", f"row{i}")) for i, r in enumerate(self.rows)]
        self.X_all = [self._features_for_row(r) for r in self.rows]
        self.y = np.array([r["depth_in"] for r in self.rows])
        self.X = self._design(self.X_all)
        self.coef = np.linalg.lstsq(self.X, self.y, rcond=None)[0]
        self.loo_errors = self._loo()
        self.ranges = {f: (min(x[f] for x in self.X_all), max(x[f] for x in self.X_all)) for f in self.features}
        self.cuts_seen = sorted({x["cuts_per_ring"] for x in self.X_all})

    # -- fitting -----------------------------------------------------------
    @staticmethod
    def _features_for_row(row):
        params = {k: v for k, v in row.items() if k.isupper()}
        return pattern_features(generate_pattern(params, write_files=False))

    def _design(self, feats):
        cols = [[f[name] for name in self.features] for f in feats]
        X = np.array(cols, dtype=float)
        return np.c_[X, np.ones(len(X))] if self.intercept else X

    def _loo(self):
        errors = []
        for i in range(len(self.y)):
            keep = np.arange(len(self.y)) != i
            c = np.linalg.lstsq(self.X[keep], self.y[keep], rcond=None)[0]
            errors.append(float(self.X[i] @ c - self.y[i]))
        return errors

    @property
    def loo_rmse(self):
        return math.sqrt(float(np.mean(np.square(self.loo_errors))))

    def equation(self):
        terms = [f"{c:.4f}*{name}" for c, name in zip(self.coef, self.features)]
        if self.intercept:
            terms.append(f"{self.coef[-1]:+.4f}")
        return "depth_in = " + " + ".join(terms)

    # -- prediction --------------------------------------------------------
    def predict(self, report):
        """Returns (depth_in, info) for a generate_pattern() report."""

        feats = pattern_features(report)
        raw = float(self._design([feats])[0] @ self.coef)
        lo, hi = self.cfg["CLAMP_IN"]
        depth = min(max(raw, lo), hi)

        notes = []
        for name, (fmin, fmax) in self.ranges.items():
            if not fmin <= feats[name] <= fmax:
                notes.append(f"{name}={feats[name]:g} outside measured {fmin:g}-{fmax:g}")
        if feats["cuts_per_ring"] not in self.cuts_seen:
            notes.append(f"cuts_per_ring={feats['cuts_per_ring']:g} never measured ({self.cuts_seen})")
        if depth != raw:
            notes.append(f"clamped from {raw:.2f} in")
        return depth, {
            "depth_model": self.equation(),
            "depth_predicted_in": depth,
            "depth_extrapolated": bool(notes),
            "depth_notes": "; ".join(notes),
            "depth_model_loo_rmse_in": self.loo_rmse,
        }

    def summary(self):
        lines = [self.equation(),
                 f"fitted on {len(self.y)} measurements; leave-one-out RMSE {self.loo_rmse:.2f} in",
                 f"{'measurement':40s} {'measured':>9s} {'fit':>7s} {'LOO pred':>9s}"]
        fitted = self.X @ self.coef
        for name, y, f, e in zip(self.names, self.y, fitted, self.loo_errors):
            lines.append(f"{name[:40]:40s} {y:9.2f} {f:7.2f} {y + e:9.2f}")
        return "\n".join(lines)


_cache = {}


def get_depth_model(cfg):
    """Fits once per process (the CSV is tiny, but fits run per design)."""

    key = (str(Path(cfg["DATA_FILE"]).resolve()), tuple(cfg["FEATURES"]), cfg.get("INTERCEPT", False),
           tuple(cfg["CLAMP_IN"]), Path(cfg["DATA_FILE"]).stat().st_mtime)
    if key not in _cache:
        _cache[key] = DepthModel(cfg)
    return _cache[key]
