"""Crash-safe run ledger: the single source of truth for resuming.

state/ledger.json holds
  * records: one per finished design (success or failure) with params,
    the optimizer vector x, the objective y and every logged value;
  * pending: the design currently being evaluated, including SimScale IDs
    as they are created, so an interrupted CFD run is resumed rather than
    re-run (and re-billed).

Every write goes to a temp file and is atomically renamed over the ledger.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {"created_at": _now(), "records": [], "pending": None}

    # -- persistence --------------------------------------------------------
    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = _now()
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".ledger-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    # -- records ------------------------------------------------------------
    @property
    def records(self):
        return self.data["records"]

    @property
    def pending(self):
        return self.data.get("pending")

    def next_design_id(self):
        n = len(self.records) + (1 if self.pending else 0) + 1
        return f"design_{n:04d}"

    def start(self, design_id, params, x):
        self.data["pending"] = {"design_id": design_id, "params": params, "x": x,
                                "started_at": _now(), "simscale": {}}
        self.save()

    def update_pending(self, **fields):
        self.data["pending"].update(fields)
        self.save()

    def finish(self, record):
        record.setdefault("finished_at", _now())
        self.records.append(record)
        self.data["pending"] = None
        self.save()

    def mark_synced(self, design_id):
        for r in self.records:
            if r["design_id"] == design_id:
                r["sheet_synced"] = True
        self.save()

    def unsynced(self):
        return [r for r in self.records if not r.get("sheet_synced")]

    # -- optimizer view -----------------------------------------------------
    def xy(self):
        """(x0, y0) of all finished records that belong to the search."""

        pairs = [(r["x"], r["y"]) for r in self.records if r.get("x") is not None and r.get("y") is not None]
        return [p[0] for p in pairs], [p[1] for p in pairs]

    def lookup(self, x):
        for r in self.records:
            if r.get("x") == list(x) and r.get("y") is not None:
                return r
        return None

    def cfd_history(self):
        """[(x_norm, cd, design_id)] of successful CFD runs, for the velocity estimator."""

        return [(r["x_norm"], r["cd"], r["design_id"]) for r in self.records
                if r.get("status") == "ok" and r.get("x_norm") is not None and r.get("cd") is not None]
