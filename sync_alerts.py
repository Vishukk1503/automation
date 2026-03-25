"""
sync_alerts.py
--------------
Daily alert CSV sync script.

Folder layout (all relative to this script's directory):
  input/           → drop new daily CSV here (any .csv file)
  central/         → master.csv lives here (PowerBI connects to this)
  archive/         → processed input files moved here with timestamp
  backup/          → pre-run snapshot of master.csv (for rollback)
  logs/            → run log text files

Expected CSV columns (both central & input files share the same schema):
  Alert_Date, Message_Key/Party_Key, Parent_Entity_ID, Job_Name,
  Alert_Type, Alert_ID, Search_Definition, Source_System_ID, BU,
  User_Name, Open/Closed, Alert_Close_Date, Previous_Eligibility_Status,
  Alert_Step, Has_RFI

Logic per row in the input file:
  - Alert_ID NOT in master          → INSERT (append new row)
  - Alert_ID in master
      + Open → Closed               → REPLACE entire row
      + Any other combination        → SKIP (no meaningful state change)
      + Closed → Open               → SKIP (never reopen)
  - Duplicate Alert_ID in same input file → SKIP (first wins)

On any error → rollback master.csv from backup automatically.
"""

import os
import sys
import shutil
import logging
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE       = Path(__file__).parent
INPUT_DIR  = BASE / "input"
CENTRAL    = BASE / "central" / "master.csv"
ARCHIVE    = BASE / "archive"
BACKUP_DIR = BASE / "backup"
LOG_DIR    = BASE / "logs"

# ── Column config ──────────────────────────────────────────────────────────────
ALERT_ID_COL   = "alert_id"
STATUS_COL     = "open_closed"
OPEN_VALUE     = "Open"
CLOSED_VALUE   = "Closed"

# Original header casing to restore on output (populated at runtime)
ORIGINAL_HEADERS: list[str] = []

# Normalise incoming column names (lowercase strip) to internal names
COL_MAP = {
    "alert_id"                    : "alert_id",
    "alert id"                    : "alert_id",
    "alert_date"                  : "alert_date",
    "alert date"                  : "alert_date",
    "message_key/party_key"       : "message_key/party_key",
    "message key/party key"       : "message_key/party_key",
    "message_key_party_key"       : "message_key/party_key",
    "parent_entity_id"            : "parent_entity_id",
    "parent entity id"            : "parent_entity_id",
    "job_name"                    : "job_name",
    "job name"                    : "job_name",
    "alert_type"                  : "alert_type",
    "alert type"                  : "alert_type",
    "search_definition"           : "search_definition",
    "search definition"           : "search_definition",
    "source_system_id"            : "source_system_id",
    "source system id"            : "source_system_id",
    "bu"                          : "bu",
    "user_name"                   : "user_name",
    "user name"                   : "user_name",
    "open/closed"                 : "open_closed",
    "open_closed"                 : "open_closed",
    "openclosed"                  : "open_closed",
    "alert_close_date"            : "alert_close_date",
    "alert close date"            : "alert_close_date",
    "previous_eligibility_status" : "previous_eligibility_status",
    "previous eligibility status" : "previous_eligibility_status",
    "alert_step"                  : "alert_step",
    "alert step"                  : "alert_step",
    "has_rfi"                     : "has_rfi",
    "has rfi"                     : "has_rfi",
}


def setup_dirs():
    for d in [INPUT_DIR, CENTRAL.parent, ARCHIVE, BACKUP_DIR, LOG_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def setup_logging(run_ts: str) -> logging.Logger:
    log_file = LOG_DIR / f"sync_{run_ts}.txt"
    logger   = logging.getLogger("sync_alerts")
    logger.setLevel(logging.DEBUG)
    fmt      = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                                  datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def read_csv_safely(path, **kwargs) -> pd.DataFrame:
    """Try UTF-8-sig (strips BOM) first, fall back to cp1252 (Windows encoding)."""
    try:
        return pd.read_csv(path, encoding="utf-8-sig", **kwargs)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="cp1252", **kwargs)


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase + strip column names and apply COL_MAP (internal use only)."""
    df.columns = [c.strip().lower() for c in df.columns]
    df.rename(columns=COL_MAP, inplace=True)
    return df


def load_central(logger: logging.Logger) -> pd.DataFrame:
    global ORIGINAL_HEADERS
    if not CENTRAL.exists():
        logger.error(f"Central file not found: {CENTRAL}")
        raise FileNotFoundError(f"Central CSV missing: {CENTRAL}")
    df = read_csv_safely(CENTRAL, dtype=str).fillna("")
    # Capture original header casing before normalising
    ORIGINAL_HEADERS = [c.strip() for c in df.columns]
    df = normalise_columns(df)
    logger.info(f"Loaded central CSV: {len(df):,} rows")
    return df


def backup_central(run_ts: str, logger: logging.Logger) -> Path:
    backup_path = BACKUP_DIR / f"master_backup_{run_ts}.csv"
    shutil.copy2(CENTRAL, backup_path)
    logger.info(f"Backup created: {backup_path}")
    return backup_path


def rollback(backup_path: Path, logger: logging.Logger):
    shutil.copy2(backup_path, CENTRAL)
    logger.warning(f"ROLLBACK complete — master.csv restored from {backup_path}")


# Expected input file prefixes (controls processing order)
FILE_PREFIX_NEW      = "01_"   # New alerts  (Report 1)
FILE_PREFIX_CLOSURES = "02_"   # Closures    (Report 2)


def get_input_files(logger: logging.Logger) -> list[Path]:
    files = sorted(INPUT_DIR.glob("*.csv"))
    if not files:
        logger.info("No input CSV found in input/ — nothing to do.")
        sys.exit(0)
    # When multiple files, enforce 01_/02_ naming to guarantee processing order
    if len(files) > 1:
        for f in files:
            if not (f.name.startswith(FILE_PREFIX_NEW) or f.name.startswith(FILE_PREFIX_CLOSURES)):
                logger.warning(
                    f"File '{f.name}' does not start with '{FILE_PREFIX_NEW}' or '{FILE_PREFIX_CLOSURES}'. "
                    f"Rename to 01_<name>.csv (new alerts) or 02_<name>.csv (closures)."
                )
                raise ValueError(
                    f"Invalid file name '{f.name}' — when dropping multiple files, "
                    f"prefix with '01_' (new alerts) or '02_' (closures)"
                )
    logger.info(f"Found {len(files)} input file(s): {', '.join(f.name for f in files)}")
    return files


def load_input(path: Path, logger: logging.Logger, central_cols: set = None) -> pd.DataFrame:
    df = read_csv_safely(path, dtype=str).fillna("")
    df = normalise_columns(df)
    if ALERT_ID_COL not in df.columns:
        raise ValueError(f"Input CSV missing required column '{ALERT_ID_COL}'")
    # Warn about unrecognised columns
    if central_cols:
        extra = set(df.columns) - central_cols
        if extra:
            logger.warning(f"Input has columns not in master (will be ignored): {extra}")
        missing = central_cols - set(df.columns) - {ALERT_ID_COL}
        if missing:
            logger.warning(f"Input is missing master columns (will be blank): {missing}")
    before = len(df)
    df.drop_duplicates(subset=[ALERT_ID_COL], keep="first", inplace=True)
    dupes = before - len(df)
    if dupes:
        logger.warning(f"Dropped {dupes} duplicate alert_id rows from input (kept first)")
    df = df[df[ALERT_ID_COL].str.strip() != ""]
    logger.info(f"Input file: {len(df):,} unique rows after dedup")
    return df


def archive_input(path: Path, run_ts: str, logger: logging.Logger):
    dest = ARCHIVE / f"{path.stem}_{run_ts}{path.suffix}"
    shutil.move(str(path), dest)
    logger.info(f"Input archived to: {dest}")


def sync(central: pd.DataFrame, incoming: pd.DataFrame,
         logger: logging.Logger) -> tuple[pd.DataFrame, dict]:

    stats = {"new": 0, "updated": 0, "skipped": 0, "skipped_reopen": 0}

    central_idx = central.set_index(ALERT_ID_COL)
    rows_to_replace = {}

    for _, row in incoming.iterrows():
        aid = str(row[ALERT_ID_COL]).strip()

        if aid not in central_idx.index:
            # Brand new alert — will be appended
            rows_to_replace[aid] = row
            stats["new"] += 1
            logger.debug(f"  NEW       {aid}")

        else:
            existing_status = str(central_idx.at[aid, STATUS_COL]).strip() if STATUS_COL in central_idx.columns else ""
            incoming_status = str(row.get(STATUS_COL, "")).strip()

            if existing_status.lower() == OPEN_VALUE.lower() and incoming_status.lower() == CLOSED_VALUE.lower():
                # State change Open → Closed — replace row
                rows_to_replace[aid] = row
                stats["updated"] += 1
                logger.debug(f"  UPDATED   {aid}  ({existing_status} → {incoming_status})")

            elif existing_status.lower() == CLOSED_VALUE.lower() and incoming_status.lower() == OPEN_VALUE.lower():
                # Closed → Open — never reopen
                stats["skipped_reopen"] += 1
                logger.debug(f"  SKIP(reopen) {aid}")

            else:
                # No meaningful state change
                stats["skipped"] += 1
                logger.debug(f"  SKIP      {aid}  (no state change)")

    if not rows_to_replace:
        logger.info("No changes to apply.")
        return central, stats

    # Remove rows that will be replaced (Open → Closed updates)
    existing_replace_ids = {aid for aid in rows_to_replace if aid in central_idx.index}
    central = central[~central[ALERT_ID_COL].isin(existing_replace_ids)]

    # Build new rows dataframe — use ONLY central's columns (drop any extras from input)
    new_rows = pd.DataFrame(rows_to_replace.values())
    # Fill any missing columns with "" and select exactly central's columns
    for col in central.columns:
        if col not in new_rows.columns:
            new_rows[col] = ""
    new_rows = new_rows[central.columns]

    result = pd.concat([central, new_rows], ignore_index=True)
    return result, stats


def main():
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    setup_dirs()
    logger = setup_logging(run_ts)

    logger.info("=" * 60)
    logger.info(f"SYNC RUN STARTED  {run_ts}")
    logger.info("=" * 60)

    backup_path   = None
    input_files  = []

    try:
        input_files = get_input_files(logger)
        central     = load_central(logger)
        backup_path = backup_central(run_ts, logger)

        # Accumulate totals across all files
        total_stats = {"new": 0, "updated": 0, "skipped": 0, "skipped_reopen": 0}
        central_cols = set(central.columns)

        for input_file in input_files:
            logger.info("")
            logger.info(f"── Processing: {input_file.name} ──")
            incoming = load_input(input_file, logger, central_cols)
            central, stats = sync(central, incoming, logger)

            for k in total_stats:
                total_stats[k] += stats[k]

            logger.info(f"  ├ New: {stats['new']:,}  Updated: {stats['updated']:,}  "
                        f"Skipped: {stats['skipped']:,}  Reopen blocked: {stats['skipped_reopen']:,}")

            archive_input(input_file, run_ts, logger)

        # Restore original header casing before writing
        if ORIGINAL_HEADERS:
            norm_to_orig = {}
            for orig in ORIGINAL_HEADERS:
                low = orig.strip().lower()
                mapped = COL_MAP.get(low, low)
                norm_to_orig[mapped] = orig
            central.rename(columns=norm_to_orig, inplace=True)

        # Write UTF-8 with BOM so Excel / PowerBI opens cleanly
        central.to_csv(CENTRAL, index=False, encoding="utf-8-sig")
        logger.info(f"Central CSV updated: {len(central):,} total rows")

        logger.info("-" * 60)
        logger.info(f"  TOTALS across {len(input_files)} file(s):")
        logger.info(f"  New alerts added   : {total_stats['new']:,}")
        logger.info(f"  Rows updated O→C   : {total_stats['updated']:,}")
        logger.info(f"  Rows skipped       : {total_stats['skipped']:,}")
        logger.info(f"  Skipped (reopen)   : {total_stats['skipped_reopen']:,}")
        logger.info("-" * 60)
        logger.info("SYNC RUN COMPLETE ✓")

    except Exception:
        logger.error("SYNC FAILED — see traceback below")
        logger.error(traceback.format_exc())
        if backup_path and backup_path.exists():
            rollback(backup_path, logger)
        sys.exit(1)


if __name__ == "__main__":
    main()
