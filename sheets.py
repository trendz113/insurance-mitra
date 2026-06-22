"""
sheets.py — Google Sheets mirror for case tracking.

Design principle: Postgres is the source of truth. This module mirrors rows
into a Sheet so a human can triage cases visually (sort by deadline, filter
by stage, color-code overdue items) without needing a custom dashboard built
from scratch. If Sheets is unreachable for any reason, the app must continue
working normally — case data is already safely in Postgres regardless.

Required env vars:
  GOOGLE_SERVICE_ACCOUNT_JSON  -- the full JSON key content (not a file path),
                                  since Railway env vars are strings, not files.
  GOOGLE_SHEET_ID              -- the spreadsheet ID from its URL.

Sheet must have a tab named "Cases" with header row exactly:
  case_ref | case_type | insurer | policy_name | score | stage | status |
  gro_sent_date | gro_followup_due | irdai_filed_date | ombudsman_filed_date |
  resolved_date | notes
"""
import os
import json
import logging

logger = logging.getLogger("sheets")

_client = None
_sheet = None
_enabled = None  # tri-state: None = not yet checked, True/False = checked


def _init():
    """Lazily initialize the Sheets client. Returns True if usable, False if not."""
    global _client, _sheet, _enabled
    if _enabled is not None:
        return _enabled

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        sheet_id = os.environ.get("GOOGLE_SHEET_ID")

        if not creds_json or not sheet_id:
            logger.warning("Sheets sync disabled: missing GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SHEET_ID")
            _enabled = False
            return False

        creds_dict = json.loads(creds_json)
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        _client = gspread.authorize(creds)
        spreadsheet = _client.open_by_key(sheet_id)
        _sheet = spreadsheet.worksheet("Cases")
        _enabled = True
        logger.info("Sheets sync enabled")
        return True
    except Exception as e:
        logger.warning(f"Sheets sync disabled, init failed: {e}")
        _enabled = False
        return False


SHEET_COLUMNS = [
    "case_ref", "case_type", "insurer", "policy_name", "score", "stage",
    "status", "gro_sent_date", "gro_followup_due", "irdai_filed_date",
    "ombudsman_filed_date", "resolved_date", "notes",
]


def _row_from_case(case: dict, case_type: str) -> list:
    def fmt(v):
        if v is None:
            return ""
        return str(v)

    return [
        fmt(case.get("case_ref")),
        case_type,
        fmt(case.get("insurer")),
        fmt(case.get("policy_name")),
        fmt(case.get("score")),
        fmt(case.get("stage")),
        fmt(case.get("status")),
        fmt(case.get("gro_sent_date")),
        fmt(case.get("gro_followup_due")),
        fmt(case.get("irdai_filed_date")),
        fmt(case.get("ombudsman_filed_date")),
        fmt(case.get("resolved_date")),
        fmt(case.get("notes")),
    ]


def _find_row_by_case_ref(case_ref: str):
    """Returns 1-indexed row number if found, else None. Assumes row 1 is header."""
    try:
        col_a = _sheet.col_values(1)  # case_ref column
        for i, val in enumerate(col_a):
            if val == case_ref:
                return i + 1  # gspread is 1-indexed
        return None
    except Exception as e:
        logger.warning(f"Sheets row lookup failed for {case_ref}: {e}")
        return None


def upsert_case(case: dict, case_type: str):
    """
    Mirrors a case dict into the Sheet. Inserts a new row if case_ref isn't
    present, otherwise updates the existing row in place. Never raises --
    failures are logged and swallowed, since Postgres already has the data.
    """
    if not _init():
        return False

    case_ref = case.get("case_ref")
    if not case_ref:
        return False

    try:
        row_values = _row_from_case(case, case_type)
        existing_row = _find_row_by_case_ref(case_ref)
        if existing_row:
            _sheet.update(f"A{existing_row}:M{existing_row}", [row_values])
        else:
            _sheet.append_row(row_values)
        return True
    except Exception as e:
        logger.warning(f"Sheets upsert failed for {case_ref}: {e}")
        return False


def is_enabled() -> bool:
    return _init()
