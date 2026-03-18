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
    "alert_id"    : "alert_id",
    "alert id"    : "alert_id",
    "open/closed" : "open_closed",
    "open_closed" : "open_closed",
    "openclosed"  : "open_closed",
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
    """Try UTF-8 first, fall back to cp1252 (Windows encoding)."""
    try:
        return pd.read_csv(path, encoding="utf-8", **kwargs)
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


def get_input_file(logger: logging.Logger) -> Path:
    files = sorted(INPUT_DIR.glob("*.csv"))
    if not files:
        logger.info("No input CSV found in input/ — nothing to do.")
        sys.exit(0)
    if len(files) > 1:
        logger.warning(f"Multiple CSVs found — processing first only: {files[0].name}")
    logger.info(f"Input file: {files[0].name}")
    return files[0]


def load_input(path: Path, logger: logging.Logger) -> pd.DataFrame:
    df = read_csv_safely(path, dtype=str).fillna("")
    df = normalise_columns(df)
    if ALERT_ID_COL not in df.columns:
        raise ValueError(f"Input CSV missing required column '{ALERT_ID_COL}'")
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
    updated_ids = set()
    rows_to_replace = {}

    for _, row in incoming.iterrows():
        aid = str(row[ALERT_ID_COL]).strip()

        if aid not in central_idx.index:
            # Brand new alert — will be appended
            updated_ids.add(aid)
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

    # Ensure columns align — add any missing cols to the existing central df
    incoming_cols = incoming.columns.tolist()
    for col in incoming_cols:
        if col not in central.columns:
            central[col] = ""

    # Remove rows that need to be replaced (updated) — new rows won't be in central
    ids_to_remove = set(rows_to_replace.keys()) - set(k for k, _ in [(k, v) for k, v in rows_to_replace.items() if k not in central_idx.index])
    # More precisely: only remove IDs that already existed in central
    existing_replace_ids = {aid for aid in rows_to_replace if aid in central_idx.index}
    central = central[~central[ALERT_ID_COL].isin(existing_replace_ids)]

    # Build new rows dataframe (preserves central column order for new rows)
    new_rows = pd.DataFrame(rows_to_replace.values())
    # Align columns to central
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

    backup_path = None
    input_file  = None

    try:
        input_file  = get_input_file(logger)
        central     = load_central(logger)
        backup_path = backup_central(run_ts, logger)
        incoming    = load_input(input_file, logger)

        result, stats = sync(central, incoming, logger)

        # Restore original header casing before writing
        if ORIGINAL_HEADERS:
            norm_to_orig = {}
            for orig in ORIGINAL_HEADERS:
                low = orig.strip().lower()
                mapped = COL_MAP.get(low, low)
                norm_to_orig[mapped] = orig
            result.rename(columns=norm_to_orig, inplace=True)

        # Write UTF-8 with BOM so Excel / PowerBI opens cleanly
        result.to_csv(CENTRAL, index=False, encoding="utf-8-sig")
        logger.info(f"Central CSV updated: {len(result):,} total rows")

        # Archive input
        archive_input(input_file, run_ts, logger)

        logger.info("-" * 60)
        logger.info(f"  New alerts added   : {stats['new']:,}")
        logger.info(f"  Rows updated O→C   : {stats['updated']:,}")
        logger.info(f"  Rows skipped       : {stats['skipped']:,}")
        logger.info(f"  Skipped (reopen)   : {stats['skipped_reopen']:,}")
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
