# StockScanner Dashboard Manual Verification Checklist (QA-04)

Generated: 2026-07-09

Covers PRD Wave 5 (UI-01..UI-07) and LEARN-05. This is the manual/visual
counterpart to the pure-function tests in `tests/test_dashboard.py` (which never
launch Streamlit). Use it to verify the interactive app end to end.

## Prerequisites

```bash
python -m pip install -r requirements.txt   # includes streamlit
streamlit run app.py
```

The app is defined in `app.py`. It reads two sources, both set from the sidebar:

- A scanner output JSON (a `scan_results_*.json` written by
  `python run_scanner.py --deterministic`, or any `write_scan_outputs` file).
- A trade-journal SQLite DB URL (default `sqlite:///./data/stonx.db`).

To generate live data first:

```bash
python run_scanner.py --deterministic         # writes scan_results JSON + CSV into ./data
python run_scanner.py --pipeline               # populates journal candidates + agent reasoning
python run_scanner.py --monitor                # generates open/closed trades + events (dry-run)
```

Tip: point the DB URL at a journal that already has rows, or run `--pipeline`
and `--monitor` so the Reasoning / Journal / Learning tabs have content.

---

## 0. App shell and sidebar (UI-01)

- [ ] `streamlit run app.py` starts with no import-time errors in the console.
- [ ] Page title reads **"StockScanner Dashboard"**; layout is wide.
- [ ] Sidebar shows **Data sources** with two inputs: "Scan output JSON" and "Journal DB URL".
- [ ] The Scan output JSON field defaults to the latest `scan_results_*.json` when one exists.
- [ ] Four tabs are present in order: **Scanner Output**, **Agent Reasoning**, **Trade Journal**, **Learning Insights**.
- [ ] Pointing the DB URL at an invalid/unreachable DB shows a friendly "Journal unavailable" error on the journal-backed tabs (app stays alive).

---

## 1. Scanner Output tab (UI-02)

- [ ] With no scan loaded, an info message prompts to point the sidebar at a `scan_results_*.json`.
- [ ] With a scan loaded, four metrics render at top: **Market regime**, **Universe**, **Approved**, **Rejected**.
- [ ] Scan timestamp caption appears when present in the file.
- [ ] Candidate table displays the expected columns (rank, symbol, pattern, confidence, entry, stop, target, R:R, position size, capital at risk, sector, market regime, risk status).
- [ ] Row count line reads "Showing N of M candidates".
- [ ] If plotly is installed, a "Scan funnel" bar chart renders from the funnel counts (best-effort; absence is not an error).

## 2. Scanner Output filters (UI-03)

Open the **Filters** expander (expanded by default).

- [ ] **Pattern** selectbox lists "All" plus every distinct pattern; selecting one narrows the table.
- [ ] **Sector** selectbox lists "All" plus every distinct sector; selecting one narrows the table.
- [ ] **Min R:R** number input drops candidates below the entered R:R (0 = no filter).
- [ ] **Approved only** checkbox keeps only rows with risk status "approved".
- [ ] **Volume confirmed only** checkbox filters when the scan carries a `volume_confirmed` column; caption notes it is a no-op otherwise.
- [ ] Combining several filters at once narrows correctly and never crashes on an empty result.

---

## 3. Agent Reasoning tab (UI-04)

- [ ] Two text inputs: "Filter by symbol (optional)" and "Filter by run_id (optional)".
- [ ] With no decisions recorded, an info message "No agent decisions recorded yet." shows.
- [ ] After a `--pipeline` run, decisions render with columns for run_id, symbol, agent_name, decision, confidence, reasoning.
- [ ] Filtering by a known symbol narrows rows; filtering by an unknown symbol yields an empty (still-typed) table with no error.
- [ ] Filtering by run_id restricts to that run.

---

## 4. Trade Journal tab (UI-05)

- [ ] Five summary metrics render: **Open**, **Closed**, **Win rate** (%), **Total PnL**, **Avg PnL %**.
- [ ] "Open positions" section lists open trades, or shows "No open positions."
- [ ] "Closed trades" section lists closed trades with outcome and PnL, or shows "No closed trades."
- [ ] Win rate and totals match the underlying journal (cross-check against `--monitor` output or a seeded DB).
- [ ] If plotly is installed and closed trades exist, a "Closed-trade PnL" bar chart renders.

---

## 5. Learning Insights tab (UI-06) + approve/reject (LEARN-05)

- [ ] If the learning module is unavailable, a warning explains recommendations will appear once ready (app does not crash).
- [ ] With fewer than the minimum closed trades (50), an info message states there is not enough data yet and shows the analyzed count.
- [ ] With enough trades, the stats block renders and any recommendations appear in expanders.
- [ ] A prominent warning states **human approval is required** and that Approve/Reject are placeholders.
- [ ] Each recommendation shows Action, proposed config change (JSON), backtest validation (JSON when present), and `auto_apply = False`.
- [ ] Clicking **Approve** shows a placeholder success message; clicking **Reject** shows a placeholder info message. Confirm **no config file is modified** on disk after either click.

---

## 6. Performance / smoke (UI-07)

- [ ] The dashboard loads and renders the Scanner Output tab within the PRD target on local data (target: under ~5 seconds on a typical scan file).
- [ ] Switching between all four tabs is responsive and free of tracebacks.
- [ ] Reloading the browser tab re-reads the sidebar sources without stale state.

---

## Notes

- The pure data functions behind every tab (`load_scan_output`,
  `scan_candidates_df`, `filter_candidates`, `reasoning_df`,
  `open_positions_df`, `closed_trades_df`, `journal_summary`, `learning_view`)
  are unit-tested in `tests/test_dashboard.py`; this checklist verifies the
  interactive rendering those functions feed.
- Plotly charts are optional/best-effort: if plotly is not installed the tables
  still render and the app must not error.
