"""Run logging: Google Sheet (gspread + service account) and a local CSV mirror.

The local CSV is always written first, so a Sheets outage never loses a
result; rows that failed to reach the sheet are retried by sync_pending().
"""

import csv
from pathlib import Path


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return float(f"{value:.6g}")
    return value


class LocalCsvLogger:
    """Appends rows to a CSV, widening the header when new columns appear."""

    def __init__(self, path):
        self.path = Path(path)

    def append(self, row):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows, header = [], []
        if self.path.exists():
            with open(self.path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                header = list(reader.fieldnames or [])
                rows = list(reader)
        new_cols = [k for k in row if k not in header]
        if new_cols or not header:
            header += new_cols
            rows.append({k: _cell(v) for k, v in row.items()})
            with open(self.path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=header)
                writer.writeheader()
                writer.writerows(rows)
        else:
            with open(self.path, "a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=header).writerow({k: _cell(v) for k, v in row.items()})


class SheetsLogger:
    """Appends one row per run to a Google Sheet worksheet.

    The sheet must be shared (Editor) with the service account's
    client_email from the JSON key file.
    """

    def __init__(self, service_account_file, sheet_id, worksheet="runs"):
        import gspread

        if not sheet_id:
            raise ValueError("GOOGLE_SHEET_ID is not set")
        if not Path(service_account_file).exists():
            raise FileNotFoundError(f"service account JSON not found: {service_account_file}")
        self._gspread = gspread
        self.client = gspread.service_account(filename=str(service_account_file))
        self.spreadsheet = self.client.open_by_key(sheet_id)
        try:
            self.ws = self.spreadsheet.worksheet(worksheet)
        except gspread.WorksheetNotFound:
            self.ws = self.spreadsheet.add_worksheet(title=worksheet, rows=1000, cols=80)

    def _header(self):
        return [h for h in self.ws.row_values(1) if h]

    def append(self, row):
        header = self._header()
        new_cols = [k for k in row if k not in header]
        if new_cols:
            header += new_cols
            if self.ws.col_count < len(header):
                self.ws.add_cols(len(header) - self.ws.col_count)
            self.ws.update(range_name="A1", values=[header])
        values = [_cell(row.get(col)) for col in header]
        self.ws.append_row(values, value_input_option="USER_ENTERED", table_range="A1")

    def describe(self):
        return f"{self.spreadsheet.title} / {self.ws.title}"


class RunLogger:
    """Local CSV always; Google Sheet when configured."""

    def __init__(self, csv_path, sheets_cfg=None, use_sheets=True):
        self.local = LocalCsvLogger(csv_path)
        self.sheet = None
        self.sheet_error = None
        if use_sheets and sheets_cfg:
            try:
                self.sheet = SheetsLogger(
                    sheets_cfg["SERVICE_ACCOUNT_FILE"], sheets_cfg["SHEET_ID"], sheets_cfg["WORKSHEET"]
                )
            except Exception as exc:  # keep running; rows stay queued in the ledger
                self.sheet_error = f"{type(exc).__name__}: {exc}"

    def log(self, row):
        """Writes the row locally, then to the sheet. Returns True if the sheet got it."""

        self.local.append(row)
        return self.push_to_sheet(row)

    def push_to_sheet(self, row):
        if self.sheet is None:
            return False
        try:
            self.sheet.append(row)
            return True
        except Exception as exc:
            self.sheet_error = f"{type(exc).__name__}: {exc}"
            return False
