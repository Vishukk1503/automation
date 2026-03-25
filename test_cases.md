# csv_sync Edge-Case Test Suite

20 automated tests covering every scenario relevant to the ActOne two-report filter workflow.

**Run:** `python test_edge_cases.py` from `csv_sync/`

---

## E1 — Normal Daily R1 + R2

| Item | Detail |
|------|--------|
| **Scenario** | Standard daily run: R1 delivers 2 new Open alerts, R2 delivers 1 closure on an existing alert |
| **Seed** | ALT-001 Open, ALT-002 Open, ALT-003 Closed |
| **Input** | `01_new_alerts.csv` → ALT-004 Open, ALT-005 Open · `02_closures.csv` → ALT-001 Closed |
| **Expected** | 5 rows total. ALT-004/005 inserted Open. ALT-001 → Closed. ALT-002 unchanged Open. ALT-003 unchanged Closed |

---

## E2 — Alert Created + Closed Same Day

| Item | Detail |
|------|--------|
| **Scenario** | An alert appears in R1 as Open and in R2 as Closed in the same run (created and closed same day) |
| **Seed** | ALT-001 Open |
| **Input** | `01_new.csv` → ALT-010 Open · `02_closures.csv` → ALT-010 Closed |
| **Expected** | ALT-010 exists in master with status Closed (R1 inserts Open, R2 updates to Closed) |

---

## E3 — Sort Order Guarantee (01_ before 02_)

| Item | Detail |
|------|--------|
| **Scenario** | 02_ file dropped to filesystem before 01_, but sorted name order ensures 01_ processes first |
| **Seed** | ALT-001 Open |
| **Input** | `02_closures.csv` → ALT-020 Closed · `01_new.csv` → ALT-020 Open |
| **Expected** | ALT-020 = Closed (01_ inserted it as Open first, then 02_ updated to Closed) |

---

## E4 — Missed 3 Days Catch-Up (6 Files)

| Item | Detail |
|------|--------|
| **Scenario** | User missed Fri/Sat/Sun — drops 3 days of R1+R2 at once (6 files total) |
| **Seed** | ALT-001 Open, ALT-002 Open |
| **Input** | `01_fri_new.csv` → ALT-100 Open · `02_fri_close.csv` → ALT-001 Closed · `01_sat_new.csv` → ALT-101 Open · `02_sat_close.csv` → ALT-002 Closed · `01_sun_new.csv` → ALT-102 Open · `02_sun_close.csv` → ALT-100 Closed |
| **Expected** | 5 rows. ALT-001 Closed (day 1). ALT-002 Closed (day 2). ALT-100 Closed (added day 1, closed day 3). ALT-101 Open. ALT-102 Open |

---

## E5 — Duplicate Alert_ID in Same File

| Item | Detail |
|------|--------|
| **Scenario** | Same alert_id appears twice in one input file with different statuses |
| **Seed** | ALT-001 Open |
| **Input** | `daily.csv` → ALT-050 Open (analyst_A), ALT-050 Closed (analyst_B) |
| **Expected** | ALT-050 = Open (first row wins, duplicate dropped). Only 1 copy in master |

---

## E6 — Reopen Blocked (Closed → Open)

| Item | Detail |
|------|--------|
| **Scenario** | Alert is Closed in master, input tries to reopen it as Open |
| **Seed** | ALT-001 Closed |
| **Input** | `daily.csv` → ALT-001 Open |
| **Expected** | ALT-001 stays Closed. No phantom rows created. Reopen is blocked |

---

## E7 — Already-Closed Re-sent as Closed

| Item | Detail |
|------|--------|
| **Scenario** | Alert is already Closed in master, input also sends it as Closed (no state change) |
| **Seed** | ALT-001 Closed |
| **Input** | `daily.csv` → ALT-001 Closed |
| **Expected** | ALT-001 stays Closed. Skipped (no state change). No phantom rows |

---

## E8 — Closure for Unknown Alert (Not in Master)

| Item | Detail |
|------|--------|
| **Scenario** | R2 contains a closure for an alert_id that was never seeded into master |
| **Seed** | ALT-001 Open |
| **Input** | `daily.csv` → ALT-999 Closed |
| **Expected** | ALT-999 inserted as Closed (new row). Master has 2 rows total. Unknown alerts are still inserted |

---

## E9 — Only R1, Single File (No Prefix Required)

| Item | Detail |
|------|--------|
| **Scenario** | Only new alerts dropped (no closures). Single file — no 01_/02_ prefix needed |
| **Seed** | ALT-001 Open |
| **Input** | `new_alerts_20260325.csv` → ALT-060 Open, ALT-061 Open |
| **Expected** | 3 rows. ALT-060 and ALT-061 inserted. No prefix validation error |

---

## E10 — Only R2, Single File (No Prefix Required)

| Item | Detail |
|------|--------|
| **Scenario** | Only closures dropped (no new alerts). Single file — no prefix needed |
| **Seed** | ALT-001 Open, ALT-002 Open |
| **Input** | `closures_20260325.csv` → ALT-001 Closed, ALT-002 Closed |
| **Expected** | ALT-001 → Closed. ALT-002 → Closed |

---

## E11 — Empty Input Folder

| Item | Detail |
|------|--------|
| **Scenario** | No CSV files in input/ folder — should exit cleanly |
| **Seed** | ALT-001 Open |
| **Input** | *(none)* |
| **Expected** | Clean exit (code 0). Master unchanged. ALT-001 still Open |

---

## E12 — BOM + Column Name Variations

| Item | Detail |
|------|--------|
| **Scenario** | Input file has UTF-8 BOM and space-separated column names instead of underscores |
| **Seed** | ALT-001 Open |
| **Input** | `daily.csv` (UTF-8-sig BOM) with headers: `Alert Date`, `Message Key/Party Key`, `Alert ID`, `Open/Closed`, etc. → ALT-070 Open |
| **Expected** | ALT-070 inserted despite variant column names. Master columns stay at exactly 15 (no extras) |

---

## E13 — Backfill Historical Closures

| Item | Detail |
|------|--------|
| **Scenario** | Backfill run: large batch of historical closures (Alert_Close_Date between Jan 1 – Mar 15) |
| **Seed** | 6 alerts all Open (ALT-001 through ALT-006, dates from Jan–Mar) |
| **Input** | `backfill_closures.csv` → ALT-001 Closed (Jan 10), ALT-002 Closed (Feb 1), ALT-004 Closed (Mar 5) |
| **Expected** | Row count stays at 6. ALT-001/002/004 → Closed. ALT-003/005/006 still Open |

---

## E14 — Same Alert in Both R1 and R2

| Item | Detail |
|------|--------|
| **Scenario** | Same alert appears in 01_ (Open) and 02_ (Closed) — tests ordering guarantee |
| **Seed** | ALT-001 Open |
| **Input** | `01_new.csv` → ALT-080 Open · `02_close.csv` → ALT-080 Closed |
| **Expected** | ALT-080 = Closed (01_ inserted Open → 02_ updated to Closed) |

---

## E15 — Invalid Prefix with Multiple Files

| Item | Detail |
|------|--------|
| **Scenario** | Multiple files in input/ but one lacks the required 01_/02_ prefix |
| **Seed** | ALT-001 Open |
| **Input** | `01_new.csv` → ALT-090 Open · `wrong_name.csv` → ALT-091 Closed |
| **Expected** | Sync fails with ValueError. Master unchanged (rollback). ALT-090 not in master |

---

## E16 — Open → Open Skip (No State Change)

| Item | Detail |
|------|--------|
| **Scenario** | Alert is Open in master, input also sends Open — no meaningful change |
| **Seed** | ALT-001 Open |
| **Input** | `daily.csv` → ALT-001 Open |
| **Expected** | Status unchanged (Open). No duplicate rows. Counted as "skipped" |

---

## E17 — Bulk 50 New Alerts

| Item | Detail |
|------|--------|
| **Scenario** | Large batch of 50 new alerts in a single file, no closures |
| **Seed** | ALT-001 Open |
| **Input** | `daily.csv` → 50 new alerts (ALT-0200 through ALT-0249) |
| **Expected** | Master has 51 rows (1 seed + 50 new) |

---

## E18 — Alert_Close_Date Populated on Update

| Item | Detail |
|------|--------|
| **Scenario** | When R2 updates Open→Closed, the Alert_Close_Date value from the input row must be preserved |
| **Seed** | ALT-001 Open |
| **Input** | `daily.csv` → ALT-001 Closed with Alert_Close_Date = 2026-03-25 |
| **Expected** | ALT-001 Alert_Close_Date = "2026-03-25" in master |

---

## E19 — Header Casing Preserved

| Item | Detail |
|------|--------|
| **Scenario** | After sync, master.csv must retain original header casing (not lowercased internal names) |
| **Seed** | ALT-001 Open |
| **Input** | `daily.csv` → ALT-050 Open |
| **Expected** | First line of master.csv = `Alert_Date,Message_Key/Party_Key,Parent_Entity_ID,Job_Name,Alert_Type,Alert_ID,Search_Definition,Source_System_ID,BU,User_Name,Open/Closed,Alert_Close_Date,Previous_Eligibility_Status,Alert_Step,Has_RFI` |

---

## E20 — Missing Alert_ID Column → Error + Rollback

| Item | Detail |
|------|--------|
| **Scenario** | Input CSV is missing the Alert_ID column entirely — must error and rollback |
| **Seed** | ALT-001 Open |
| **Input** | `bad.csv` with only columns: Alert_Date, Message_Key/Party_Key, Open/Closed |
| **Expected** | Sync fails (ValueError). Master unchanged via rollback. Data intact |

---

## Results Summary

| # | Edge Case | What it tests | Result |
|---|-----------|---------------|--------|
| E1 | Normal daily R1+R2 | 2 new alerts + 1 closure in one run | ✅ PASS |
| E2 | Created + closed same day | Alert in R1 (Open) then R2 (Closed) same run | ✅ PASS |
| E3 | Sort order guarantee | 02_ dropped before 01_ on disk — 01_ still processes first | ✅ PASS |
| E4 | Missed 3 days catch-up | 6 files (3x R1 + 3x R2) at once | ✅ PASS |
| E5 | Duplicate in same file | Same alert_id twice — first row wins | ✅ PASS |
| E6 | Reopen blocked | Closed→Open in input — stays Closed | ✅ PASS |
| E7 | Already-closed re-sent | Closed→Closed — skip, no phantom rows | ✅ PASS |
| E8 | Closure for unknown alert | R2 has alert not in master — inserted as Closed | ✅ PASS |
| E9 | Only R1 (single file) | No prefix required for single file | ✅ PASS |
| E10 | Only R2 (single file) | Closures-only single file, no prefix | ✅ PASS |
| E11 | Empty input folder | Clean exit, master untouched | ✅ PASS |
| E12 | BOM + column variations | Spaces instead of underscores, UTF-8-sig BOM | ✅ PASS |
| E13 | Backfill batch closures | Historical closures (3 of 6 alerts → Closed) | ✅ PASS |
| E14 | Same alert in R1 and R2 | Appears Open in 01_, Closed in 02_ — ends Closed | ✅ PASS |
| E15 | Invalid prefix (multi-file) | Bad name with 2+ files → error + rollback | ✅ PASS |
| E16 | Open→Open skip | Same status, no change — skipped | ✅ PASS |
| E17 | Bulk 50 new alerts | 50 inserts in one file | ✅ PASS |
| E18 | Close date populated | Alert_Close_Date from input row preserved | ✅ PASS |
| E19 | Header casing preserved | Output headers match original casing exactly | ✅ PASS |
| E20 | Missing column → rollback | Bad CSV → error + rollback, data intact | ✅ PASS |

**71 / 71 assertions passed** — run date: March 25, 2026
