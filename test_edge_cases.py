"""
test_edge_cases.py
------------------
Comprehensive edge-case test suite for sync_alerts.py.
Tests all scenarios relevant to the ActOne two-report filter workflow.

Each test:
  1. Resets the environment (input/, central/, archive/, backup/, logs/)
  2. Seeds master.csv with known data
  3. Drops input files
  4. Runs sync_alerts.main()
  5. Reads back master.csv and asserts expected state

Run:  python test_edge_cases.py
"""

import os, sys, shutil, io, textwrap
from pathlib import Path

import pandas as pd

BASE       = Path(__file__).parent
INPUT_DIR  = BASE / "input"
CENTRAL    = BASE / "central" / "master.csv"
ARCHIVE    = BASE / "archive"
BACKUP_DIR = BASE / "backup"
LOG_DIR    = BASE / "logs"

HEADER = "Alert_Date,Message_Key/Party_Key,Parent_Entity_ID,Job_Name,Alert_Type,Alert_ID,Search_Definition,Source_System_ID,BU,User_Name,Open/Closed,Alert_Close_Date,Previous_Eligibility_Status,Alert_Step,Has_RFI"

PASS = 0
FAIL = 0
RESULTS = []

# ── helpers ────────────────────────────────────────────────────────────────────

def reset_env():
    """Wipe input/ archive/ backup/ logs/ and central/ to a clean state."""
    # Close any open log handlers first (Windows file locking)
    import logging
    logger = logging.getLogger("sync_alerts")
    for h in logger.handlers[:]:
        h.close()
        logger.removeHandler(h)
    for d in [INPUT_DIR, ARCHIVE, BACKUP_DIR, LOG_DIR, CENTRAL.parent]:
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)


def seed_master(rows: list[str]):
    """Write master.csv from row strings (no header — we add it)."""
    lines = [HEADER] + rows
    CENTRAL.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def drop_file(name: str, rows: list[str], header: str = None, encoding: str = "utf-8-sig"):
    """Drop a CSV into input/ folder."""
    h = header or HEADER
    lines = [h] + rows
    (INPUT_DIR / name).write_text("\n".join(lines) + "\n", encoding=encoding)


def read_master() -> pd.DataFrame:
    return pd.read_csv(CENTRAL, dtype=str, encoding="utf-8-sig").fillna("")


def run_sync() -> bool:
    """Run sync_alerts.main(), return True if it ran without error."""
    # We need to reimport each time because handlers accumulate
    import importlib
    import logging
    # Remove existing handlers to avoid duplication
    logger = logging.getLogger("sync_alerts")
    logger.handlers.clear()
    
    import sync_alerts
    importlib.reload(sync_alerts)
    try:
        sync_alerts.main()
        return True
    except SystemExit as e:
        # sys.exit(0) = no input files (expected in some tests)
        # sys.exit(1) = error
        return e.code == 0
    except Exception:
        return False


def assert_check(test_name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        RESULTS.append(("PASS", test_name, detail))
        print(f"  ✓ PASS  {detail}")
    else:
        FAIL += 1
        RESULTS.append(("FAIL", test_name, detail))
        print(f"  ✗ FAIL  {detail}")


def master_status(alert_id: str, df: pd.DataFrame) -> str:
    """Get Open/Closed status for an alert_id in master."""
    col_name = "Open/Closed"
    matches = df[df["Alert_ID"] == alert_id]
    if matches.empty:
        return "<NOT FOUND>"
    return matches.iloc[0][col_name].strip()


def master_has(alert_id: str, df: pd.DataFrame) -> bool:
    return not df[df["Alert_ID"] == alert_id].empty


def row(alert_id, status="Open", date="2026-03-25", close_date="", **kw):
    """Build a CSV row string with sensible defaults."""
    return ",".join([
        kw.get("alert_date", date),
        kw.get("msg_key", f"MK-{alert_id}"),
        kw.get("parent", f"PE-{alert_id}"),
        kw.get("job", "SanctionsJob"),
        kw.get("alert_type", "Sanctions"),
        alert_id,
        kw.get("search_def", "SDN"),
        kw.get("source_sys", "SYS1"),
        kw.get("bu", "BU1"),
        kw.get("user", "analyst1"),
        status,
        close_date,
        kw.get("prev_elig", "Eligible"),
        kw.get("step", "L1"),
        kw.get("rfi", "No"),
    ])


# ── TESTS ──────────────────────────────────────────────────────────────────────

def test_E1_normal_daily_R1_R2():
    """Normal daily: R1 = 2 new Open alerts, R2 = 1 closure on existing alert."""
    print("\n═══ E1: Normal daily R1 + R2 ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
        row("ALT-002", "Open", "2026-03-21"),
        row("ALT-003", "Closed", "2026-03-18", "2026-03-19"),
    ])
    drop_file("01_new_alerts.csv", [
        row("ALT-004", "Open"),
        row("ALT-005", "Open"),
    ])
    drop_file("02_closures.csv", [
        row("ALT-001", "Closed", "2026-03-20", "2026-03-25"),
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E1", ok, "sync ran successfully")
    assert_check("E1", len(df) == 5, f"master has 5 rows (got {len(df)})")
    assert_check("E1", master_status("ALT-004", df) == "Open", "ALT-004 inserted Open")
    assert_check("E1", master_status("ALT-005", df) == "Open", "ALT-005 inserted Open")
    assert_check("E1", master_status("ALT-001", df) == "Closed", "ALT-001 updated to Closed")
    assert_check("E1", master_status("ALT-002", df) == "Open", "ALT-002 unchanged Open")
    assert_check("E1", master_status("ALT-003", df) == "Closed", "ALT-003 unchanged Closed")


def test_E2_created_and_closed_same_day():
    """Alert appears in R1 as Open, same alert in R2 as Closed — same day."""
    print("\n═══ E2: Alert created + closed same day (in R1 and R2) ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    drop_file("01_new.csv", [
        row("ALT-010", "Open"),   # brand new
    ])
    drop_file("02_closures.csv", [
        row("ALT-010", "Closed", "2026-03-25", "2026-03-25"),  # closed same day
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E2", ok, "sync ran successfully")
    assert_check("E2", master_has("ALT-010", df), "ALT-010 exists in master")
    assert_check("E2", master_status("ALT-010", df) == "Closed",
                 "ALT-010 ends up Closed (R1 inserts Open, R2 updates to Closed)")


def test_E3_wrong_prefix_order():
    """Files sorted by name: 02_ comes after 01_. Naming enforced correctly."""
    print("\n═══ E3: Verify 01_ processes before 02_ (sort order) ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    # drop 02_ first to filesystem, but sorted order should make 01_ process first
    drop_file("02_closures.csv", [
        row("ALT-020", "Closed", "2026-03-25", "2026-03-25"),  # not in master yet
    ])
    drop_file("01_new.csv", [
        row("ALT-020", "Open"),  # new alert
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E3", ok, "sync ran successfully")
    assert_check("E3", master_has("ALT-020", df), "ALT-020 exists")
    assert_check("E3", master_status("ALT-020", df) == "Closed",
                 "ALT-020 = Closed (01_ inserted Open, 02_ updated to Closed)")


def test_E4_missed_3_days_catchup():
    """Missed Fri/Sat/Sun — drop 3 days of R1+R2 at once (6 files)."""
    print("\n═══ E4: Missed 3 days catch-up (6 files) ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
        row("ALT-002", "Open", "2026-03-21"),
    ])
    # Day 1 files  (Fri 20th)
    drop_file("01_fri_new.csv", [
        row("ALT-100", "Open", "2026-03-20"),
    ])
    drop_file("02_fri_close.csv", [
        row("ALT-001", "Closed", "2026-03-20", "2026-03-20"),
    ])
    # Day 2 files  (Sat 21st)
    drop_file("01_sat_new.csv", [
        row("ALT-101", "Open", "2026-03-21"),
    ])
    drop_file("02_sat_close.csv", [
        row("ALT-002", "Closed", "2026-03-21", "2026-03-21"),
    ])
    # Day 3 files  (Sun 22nd)
    drop_file("01_sun_new.csv", [
        row("ALT-102", "Open", "2026-03-22"),
    ])
    drop_file("02_sun_close.csv", [
        row("ALT-100", "Closed", "2026-03-20", "2026-03-22"),  # close ALT-100 from Fri
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E4", ok, "sync ran successfully")
    assert_check("E4", len(df) == 5, f"master has 5 rows (got {len(df)})")
    assert_check("E4", master_status("ALT-001", df) == "Closed", "ALT-001 closed day 1")
    assert_check("E4", master_status("ALT-002", df) == "Closed", "ALT-002 closed day 2")
    assert_check("E4", master_status("ALT-100", df) == "Closed", "ALT-100 added day1, closed day3")
    assert_check("E4", master_status("ALT-101", df) == "Open", "ALT-101 still Open")
    assert_check("E4", master_status("ALT-102", df) == "Open", "ALT-102 still Open")


def test_E5_duplicate_alert_same_file():
    """Same alert_id appears twice in one file — first row wins."""
    print("\n═══ E5: Duplicate alert_id in same file ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    drop_file("daily.csv", [
        row("ALT-050", "Open", user="analyst_A"),
        row("ALT-050", "Closed", user="analyst_B"),  # dupe — should be dropped
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E5", ok, "sync ran successfully")
    assert_check("E5", master_status("ALT-050", df) == "Open",
                 "ALT-050 = Open (first row wins, dupe dropped)")
    matches = df[df["Alert_ID"] == "ALT-050"]
    assert_check("E5", len(matches) == 1, "only 1 copy of ALT-050 in master")


def test_E6_reopen_blocked():
    """Already Closed in master, input sends Open — must be blocked."""
    print("\n═══ E6: Reopen attempt (Closed → Open) ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Closed", "2026-03-20", "2026-03-22"),
    ])
    drop_file("daily.csv", [
        row("ALT-001", "Open", "2026-03-20"),  # attempt to reopen
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E6", ok, "sync ran successfully")
    assert_check("E6", master_status("ALT-001", df) == "Closed",
                 "ALT-001 stays Closed (reopen blocked)")
    assert_check("E6", len(df) == 1, "no phantom rows created")


def test_E7_already_closed_resent():
    """Alert already Closed in master, input also says Closed — skip."""
    print("\n═══ E7: Already-closed re-sent as Closed ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Closed", "2026-03-20", "2026-03-22"),
    ])
    drop_file("daily.csv", [
        row("ALT-001", "Closed", "2026-03-20", "2026-03-22"),
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E7", ok, "sync ran successfully")
    assert_check("E7", master_status("ALT-001", df) == "Closed",
                 "ALT-001 stays Closed (no change)")
    assert_check("E7", len(df) == 1, "no phantom rows")


def test_E8_closure_for_unknown_alert():
    """R2 has a closure for an alert_id that's NOT in master (never seeded).
    This can happen if a user missed the seed and does only R2.
    The alert should be inserted as-is (Closed)."""
    print("\n═══ E8: Closure for unknown alert (not in master) ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    drop_file("daily.csv", [
        row("ALT-999", "Closed", "2026-01-15", "2026-03-25"),  # not in master
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E8", ok, "sync ran successfully")
    assert_check("E8", master_has("ALT-999", df), "ALT-999 inserted (even though Closed)")
    assert_check("E8", master_status("ALT-999", df) == "Closed",
                 "ALT-999 status = Closed")
    assert_check("E8", len(df) == 2, "master has 2 rows total")


def test_E9_only_R1_single_file():
    """Only R1 dropped (no closures). Single file — no prefix needed."""
    print("\n═══ E9: Only R1, single file (no prefix) ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    drop_file("new_alerts_20260325.csv", [
        row("ALT-060", "Open"),
        row("ALT-061", "Open"),
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E9", ok, "sync ran successfully")
    assert_check("E9", len(df) == 3, f"master has 3 rows (got {len(df)})")
    assert_check("E9", master_status("ALT-060", df) == "Open", "ALT-060 inserted")
    assert_check("E9", master_status("ALT-061", df) == "Open", "ALT-061 inserted")


def test_E10_only_R2_single_file():
    """Only R2 dropped (closures only). Single file — no prefix needed."""
    print("\n═══ E10: Only R2, single file (no prefix) ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
        row("ALT-002", "Open", "2026-03-21"),
    ])
    drop_file("closures_20260325.csv", [
        row("ALT-001", "Closed", "2026-03-20", "2026-03-25"),
        row("ALT-002", "Closed", "2026-03-21", "2026-03-25"),
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E10", ok, "sync ran successfully")
    assert_check("E10", master_status("ALT-001", df) == "Closed", "ALT-001 → Closed")
    assert_check("E10", master_status("ALT-002", df) == "Closed", "ALT-002 → Closed")


def test_E11_empty_input_folder():
    """No files in input/ — clean exit, master untouched."""
    print("\n═══ E11: Empty input folder ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    original = read_master()
    ok = run_sync()  # should exit(0) silently
    df = read_master()
    assert_check("E11", ok, "clean exit (no error)")
    assert_check("E11", len(df) == 1, "master unchanged")
    assert_check("E11", master_status("ALT-001", df) == "Open", "ALT-001 still Open")


def test_E12_BOM_and_column_variations():
    """Input has BOM and slightly different column names (spaces instead of underscores)."""
    print("\n═══ E12: BOM + column name variations ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    # Use space-separated column names and explicit BOM
    alt_header = "Alert Date,Message Key/Party Key,Parent Entity ID,Job Name,Alert Type,Alert ID,Search Definition,Source System ID,BU,User Name,Open/Closed,Alert Close Date,Previous Eligibility Status,Alert Step,Has RFI"
    drop_file("daily.csv", [
        row("ALT-070", "Open"),
    ], header=alt_header, encoding="utf-8-sig")  # BOM present
    ok = run_sync()
    df = read_master()
    assert_check("E12", ok, "sync ran successfully with variant columns")
    assert_check("E12", master_has("ALT-070", df), "ALT-070 inserted despite varied column names")
    # Check no extra columns crept in
    expected_cols = set(HEADER.split(","))
    actual_cols = set(df.columns)
    assert_check("E12", actual_cols == expected_cols,
                 f"columns match original ({len(actual_cols)} cols, expected {len(expected_cols)})")


def test_E13_backfill_batch_closures():
    """Backfill: large batch of historical closures (Alert_Close_Date between Jan 1 – Mar 15)."""
    print("\n═══ E13: Backfill historical closures ═══")
    reset_env()
    # Seed with 6 alerts, all Open
    seed_master([
        row("ALT-001", "Open", "2026-01-05"),
        row("ALT-002", "Open", "2026-01-20"),
        row("ALT-003", "Open", "2026-02-10"),
        row("ALT-004", "Open", "2026-02-28"),
        row("ALT-005", "Open", "2026-03-01"),
        row("ALT-006", "Open", "2026-03-15"),
    ])
    # Backfill: ALT-001, ALT-002, ALT-004 are actually closed (historical)
    drop_file("backfill_closures.csv", [
        row("ALT-001", "Closed", "2026-01-05", "2026-01-10"),
        row("ALT-002", "Closed", "2026-01-20", "2026-02-01"),
        row("ALT-004", "Closed", "2026-02-28", "2026-03-05"),
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E13", ok, "sync ran successfully")
    assert_check("E13", len(df) == 6, f"row count unchanged at 6 (got {len(df)})")
    assert_check("E13", master_status("ALT-001", df) == "Closed", "ALT-001 → Closed (backfill)")
    assert_check("E13", master_status("ALT-002", df) == "Closed", "ALT-002 → Closed (backfill)")
    assert_check("E13", master_status("ALT-003", df) == "Open", "ALT-003 still Open")
    assert_check("E13", master_status("ALT-004", df) == "Closed", "ALT-004 → Closed (backfill)")
    assert_check("E13", master_status("ALT-005", df) == "Open", "ALT-005 still Open")
    assert_check("E13", master_status("ALT-006", df) == "Open", "ALT-006 still Open")


def test_E14_same_alert_in_R1_and_R2():
    """Same alert appears in R1 (Open) and R2 (Closed) — end state should be Closed.
    This tests the 01_ → 02_ ordering guarantee."""
    print("\n═══ E14: Same alert in R1 (Open) and R2 (Closed) ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    drop_file("01_new.csv", [
        row("ALT-080", "Open"),
    ])
    drop_file("02_close.csv", [
        row("ALT-080", "Closed", "2026-03-25", "2026-03-25"),
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E14", ok, "sync ran successfully")
    assert_check("E14", master_status("ALT-080", df) == "Closed",
                 "ALT-080 = Closed (R1 inserted Open → R2 updated to Closed)")


def test_E15_invalid_prefix_with_multiple_files():
    """Multiple files but one lacks 01_/02_ prefix — should fail with ValueError."""
    print("\n═══ E15: Invalid prefix with multiple files ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    drop_file("01_new.csv", [
        row("ALT-090", "Open"),
    ])
    drop_file("wrong_name.csv", [  # no 01_/02_ prefix
        row("ALT-091", "Closed"),
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E15", not ok, "sync correctly rejected invalid file name")
    assert_check("E15", master_status("ALT-001", df) == "Open",
                 "master unchanged after rejection (rollback)")
    assert_check("E15", not master_has("ALT-090", df),
                 "ALT-090 not in master (rolled back)")


def test_E16_open_to_open_skip():
    """Alert is Open in master, input also Open — no state change → skip."""
    print("\n═══ E16: Open→Open (no change) ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    drop_file("daily.csv", [
        row("ALT-001", "Open", "2026-03-20"),  # same status
    ])
    ok = run_sync()
    df = read_master()
    assert_check("E16", ok, "sync ran successfully")
    assert_check("E16", master_status("ALT-001", df) == "Open", "status unchanged")
    assert_check("E16", len(df) == 1, "no duplicate rows")


def test_E17_many_new_no_closures():
    """Bulk insert of new alerts, no closures — simulates first daily after seed."""
    print("\n═══ E17: Bulk new alerts, no closures ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    new_rows = [row(f"ALT-{200+i:04d}", "Open") for i in range(50)]
    drop_file("daily.csv", new_rows)
    ok = run_sync()
    df = read_master()
    assert_check("E17", ok, "sync ran successfully")
    assert_check("E17", len(df) == 51, f"master has 51 rows (1 seed + 50 new, got {len(df)})")


def test_E18_close_date_populated_on_update():
    """When R2 updates Open→Closed, the Alert_Close_Date should be from the input row."""
    print("\n═══ E18: Close date populated correctly ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    drop_file("daily.csv", [
        row("ALT-001", "Closed", "2026-03-20", "2026-03-25"),
    ])
    ok = run_sync()
    df = read_master()
    close_date = df[df["Alert_ID"] == "ALT-001"]["Alert_Close_Date"].iloc[0]
    assert_check("E18", ok, "sync ran successfully")
    assert_check("E18", close_date == "2026-03-25",
                 f"Alert_Close_Date = 2026-03-25 (got '{close_date}')")


def test_E19_master_columns_preserved():
    """After sync, master.csv retains original header casing (not lowercased)."""
    print("\n═══ E19: Header casing preserved ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    drop_file("daily.csv", [
        row("ALT-050", "Open"),
    ])
    ok = run_sync()
    # Read raw first line
    first_line = CENTRAL.read_text(encoding="utf-8-sig").split("\n")[0].strip()
    assert_check("E19", ok, "sync ran successfully")
    assert_check("E19", first_line == HEADER,
                 f"header casing preserved: {first_line[:60]}...")


def test_E20_rollback_on_missing_alert_id_column():
    """Input CSV missing Alert_ID column entirely — must error and rollback."""
    print("\n═══ E20: Missing Alert_ID column → error + rollback ═══")
    reset_env()
    seed_master([
        row("ALT-001", "Open", "2026-03-20"),
    ])
    bad_header = "Alert_Date,Message_Key/Party_Key,Open/Closed"
    (INPUT_DIR / "bad.csv").write_text(
        bad_header + "\n2026-03-25,MK-X,Open\n", encoding="utf-8-sig"
    )
    ok = run_sync()
    df = read_master()
    assert_check("E20", not ok, "sync correctly failed on missing column")
    assert_check("E20", len(df) == 1, "master unchanged (rollback)")
    assert_check("E20", master_status("ALT-001", df) == "Open", "data intact after rollback")


# ── runner ─────────────────────────────────────────────────────────────────────

def main():
    global PASS, FAIL

    os.chdir(BASE)

    tests = [
        test_E1_normal_daily_R1_R2,
        test_E2_created_and_closed_same_day,
        test_E3_wrong_prefix_order,
        test_E4_missed_3_days_catchup,
        test_E5_duplicate_alert_same_file,
        test_E6_reopen_blocked,
        test_E7_already_closed_resent,
        test_E8_closure_for_unknown_alert,
        test_E9_only_R1_single_file,
        test_E10_only_R2_single_file,
        test_E11_empty_input_folder,
        test_E12_BOM_and_column_variations,
        test_E13_backfill_batch_closures,
        test_E14_same_alert_in_R1_and_R2,
        test_E15_invalid_prefix_with_multiple_files,
        test_E16_open_to_open_skip,
        test_E17_many_new_no_closures,
        test_E18_close_date_populated_on_update,
        test_E19_master_columns_preserved,
        test_E20_rollback_on_missing_alert_id_column,
    ]

    print("=" * 65)
    print(f"  EDGE-CASE TEST SUITE  —  {len(tests)} tests")
    print("=" * 65)

    for t in tests:
        try:
            t()
        except Exception as ex:
            FAIL += 1
            RESULTS.append(("ERROR", t.__name__, str(ex)))
            print(f"  ✗ ERROR  {t.__name__}: {ex}")

    print("\n" + "=" * 65)
    print(f"  RESULTS:  {PASS} passed  /  {FAIL} failed  /  {PASS + FAIL} total assertions")
    print("=" * 65)

    if FAIL:
        print("\n  FAILURES:")
        for status, name, detail in RESULTS:
            if status in ("FAIL", "ERROR"):
                print(f"    [{status}] {name}: {detail}")
        sys.exit(1)
    else:
        print("\n  ALL TESTS PASSED ✓")
        sys.exit(0)


if __name__ == "__main__":
    main()
