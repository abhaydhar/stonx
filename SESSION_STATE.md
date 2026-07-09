# StockScanner Build Session State

**Last Updated**: 2026-07-09 (Build orchestration session)
**Build Phase**: Waves 0–8 all built and verified. Feature-complete MVP; remaining work is live-data/LLM enablement (see Deferred in `docs/FINAL_STATUS.md`).
**Overall Progress**: PRD v2 MVP feature set implemented. **Full suite: 178 passed** (Python 3.12 venv).

> This file is the single source of truth for resuming on another machine.
> It is updated continuously as work lands. Read this first, then
> `PRD_v2_agent_task_breakdown.md` (waves/tasks) and `PRD_v2_code_gap_audit.md`.

---

## 🖥️ Environment Reproduction (DO THIS FIRST on a new machine)

Python **3.12** required (`.python-version` = 3.12.13).

```powershell
# From repo root C:\abhay\stonx
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt   # full set
Copy-Item .env.example .env                  # fill ANTHROPIC_API_KEY only for LLM agent runs
```

### Test command (canonical)

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

NOTE (sandbox only): in the build sandbox the default Windows temp dir is not
writable by pytest, so the build used:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider \
  -o cache_dir=<scratch>/pytest_cache --basetemp=<scratch>/pytest_tmp
```

On a normal dev machine the plain `python -m pytest -q` works.

### Deps actually installed in build venv (subset of requirements.txt)

pandas 2.2.0, numpy 1.26.0, pydantic 2.12.5, pydantic-settings 2.10.1,
pytest 8.1.1, pytest-mock 3.12.0, python-dotenv 1.2.2, pyarrow, sqlalchemy 2.0.28,
requests, beautifulsoup4, structlog, yfinance 0.2.37, streamlit 1.32.0, plotly 5.20.0,
apprise 1.7.4.

**Intentionally NOT installed in build venv** (heavy / not needed for deterministic
+ mockable-agent tests): `crewai`, `langchain`, `langchain-anthropic`, `nsepy`,
`vectorbt`, `backtesting`, `apscheduler`, `psycopg2-binary`, `python-telegram-bot`.
They remain in requirements.txt for full LLM runs. **Design rule: all agent and
backtest code must be importable and unit-testable WITHOUT these heavy deps** —
use lazy imports and injectable/mockable LLM clients.

---

## ✅ COMPLETE & VERIFIED (Waves 0–1)

Verified by 31 passing tests (`tests/test_scanner.py`, `tests/test_config.py`).

### Wave 0 — Reproducible Foundation ✅
- FND-01 `.python-version` (3.12.13) + README setup section
- FND-02 `requirements.txt` fixed (`nsepy==0.8`, not 0.9.1)
- FND-03 `pyarrow` added for parquet cache
- FND-04 `.env.example` present
- FND-05 `config.py` constructs without `ANTHROPIC_API_KEY` (key is Optional)
- FND-06 README smoke-check docs

### Wave 1 — Deterministic Scanner Core ✅
- DATA-01 CSV universe loader — `data/universe/nse_universe.csv` (40 stocks), `DataIngestion.get_universe/get_nse_universe/get_sector`
- DATA-02 provider abstraction — `OHLCVProvider` Protocol + `YFinanceOHLCVProvider`
- DATA-03 data quality metadata — `DataQualityMetadata` (source, missing %, adjusted, cache age)
- DATA-04 fundamentals from CSV — `FundamentalCSVSource` + `data/fundamentals/fundamentals_fixture.csv` (20 rows), promoter holding enforced
- SCAN-01 config wired via `build_*_from_config()` in `modules/scanner.py`
- SCAN-02 deterministic pipeline — `DeterministicScanner.run()` returns `ScannerOutput` (candidates + funnel counts, no LLM)
- SCAN-03 market regime → risk gate — `detect_market_regime()`, bear uses higher min R:R (tested)
- SCAN-04 portfolio heat + sector limits in scan path — `RiskManager.validate_batch()` (tested)
- SCAN-05 false-breakout 2-bar hold rule — `PatternDetector.breakout_hold_bars` (tested)
- SCAN-06 JSON + CSV output — `write_scan_outputs()` (tested)

---

## 🔑 Key Interfaces (build against these — do NOT change signatures)

- `modules/scanner.py`
  - `DeterministicScanner(config, ingestion, fundamentals, pattern_detector, volume_profiler)`
  - `.run(symbols, limit, market_regime, portfolio, use_cache) -> ScannerOutput`
  - `ScannerOutput{timestamp, market_regime: MarketRegime, funnel_counts: dict, candidates: [ScannerCandidate], rejected: [RejectedSetup], data_quality}`
  - `ScannerCandidate{rank, symbol, pattern, confidence, entry, stop, target, rr_ratio, position_shares, position_inr, capital_at_risk_inr, capital_at_risk_pct, sector, market_regime, risk_status, rationale}`
  - `write_scan_outputs(output, output_dir, basename) -> {"json":Path,"csv":Path}`
  - `build_{ingestion,fundamental_filter,pattern_detector,volume_profiler,risk_manager}_from_config(config, ...)`
  - `load_scanner_config()` — returns config without needing API key
- `modules/risk.py`: `RiskSetup{symbol,entry_price,stop_price,target_price,sector}`, `RiskResult{...,approved,rr_ratio,position_size_shares,position_size_inr,capital_at_risk_inr,capital_at_risk_pct,rejection_reason}`, `PortfolioState{open_positions,sector_counts,total_heat,position_count}`, `RiskManager.validate/validate_batch`
- `modules/ingest.py`: `DataIngestion.fetch_ohlcv_with_quality(symbol,...) -> OHLCVFetchResult{symbol,data:DataFrame|None,quality:DataQualityMetadata}`; `normalize_nse_symbol()`; `OHLCVProvider` Protocol (`.fetch(symbol,start,end)`, `.source_name`, `.adjusted`)
- `modules/fundamental.py`: `FundamentalFilter.screen(symbol) -> FundamentalResult{symbol,passed,data:FundamentalData,rejection_reason}`
- `modules/patterns.py`: `PatternDetector.scan(symbol, df) -> ScanResult{symbol,patterns:[PatternResult],best_pattern,passed}`
- `modules/volume.py`: `VolumeProfiler.analyse(symbol, df) -> (VolumeProfile|None, hvn_support|None, lvn_targets:list)`
- `config.py`: `ScannerConfig` (pydantic-settings), `get_config()`, agent model names present
- Interface contracts (Research/Risk/Execution/Learning JSON shapes): see `PRD_v2_agent_task_breakdown.md` "Interface Contracts".

---

## 🏗️ Build Plan (ralph-wiggum: build → test → fix until green, per round)

Ownership is DISJOINT per round to avoid file conflicts. Shared files
(`config.py`, `requirements.txt`, `orchestrator/crew.py`, `run_scanner.py`) are
integrated by the main agent between rounds.

### Round 1 — Foundations for the rest  [STATUS: ✅ COMPLETE — verified 71 passed]
- **Backtest (Wave 2, BT-01..06)** ✅ `modules/backtest.py` (23 tests) + `reports/sample_backtest_report.md`. Reuses scanner/risk/pattern/volume. Public API: `BacktestConfig`, `BacktestTrade`, `BacktestMetrics`, `BacktestResult`, `WalkForwardSplit`, `Backtester.run/walk_forward/optimize`, `generate_report`, `evaluate_thresholds`, `DEFAULT_THRESHOLDS`.
- **Journal/Persistence (Wave 3, DB-01..04)** ✅ `modules/journal.py` (17 tests). Models: `Candidate`, `AgentDecision`, `OpenPosition`, `ClosedTrade` (Base=DeclarativeBase). Service `TradeJournal(db_url|engine)`: `record_scan`, `get_candidates`, `record_agent_decision`, `get_agent_decisions`, `open_position`, `update_position`, `close_trade`, `get_open_positions`, `get_closed_trades`, `summary`. Query methods return dicts. Supports `sqlite:///:memory:` (StaticPool).
- **Shared-file prep (main agent)** ✅ Made `agents/__init__.py` and `tools/__init__.py` LAZY (PEP 562) so importing a specific agent/tool module does not pull in crewai/langchain. Added `agents/llm.py` with `LLMClient` protocol, `DeterministicLLM`, `FakeLLM`, `build_llm_client()` (degrades gracefully w/o key/deps). **All new agents must accept `llm_client: LLMClient | None` and default to DeterministicLLM.**

### Round 2 — Agents + orchestration  [STATUS: ✅ COMPLETE — verified 135 passed]
- **Research Agent (Wave 4, RES-01..03)** ✅ `tools/web_tools.py` (`WebResearchSource` Protocol, `StubWebSource`, `NullWebSource`), `agents/research_agent.py` (`ResearchAgent(llm_client, web_source, staleness_days)`, `ResearchResult`, `Citation`), 14 tests.
- **Risk Agent (Wave 4, RISK-01..03)** ✅ `tools/risk_tools.py` (`annualized_volatility`, `beta`, `correlation`, `average_true_range`, `max_drawdown`, `compute_risk_metrics`), `agents/risk_agent.py` (`RiskAgent.challenge/challenge_batch`, `RiskDecision`, `apply_size_multiplier`), 24 tests.
- **Execution + Alerts (Wave 6, EXEC-01..03, ALERT-01..03)** ✅ `agents/execution_agent.py` (`ExecutionAgent(journal, price_provider, alert_sender, one_r_trail)`, `ExecutionEvent`, `DictPriceProvider`), `tools/alert_tools.py` (`AlertFormatter`, `AlertSender(dry_run=True)` — apprise lazy, no live send in dry-run), 23 tests.
- **Orchestration (ORCH-01..02)** ✅ `orchestrator/pipeline.py` (`ScanResearchRiskPipeline.run()` → scanner→research→risk, persists candidates + agent reasoning to journal; `PipelineResult`, `PipelineDecision`), 3 tests. Legacy `orchestrator/crew.py` (CrewAI) left intact. Made `orchestrator/__init__.py` lazy.

### Round 3 — UI + Learning  [STATUS: ✅ COMPLETE — verified 170 passed]
- **Learning (Wave 7, LEARN-01..04)** ✅ `modules/learning.py` (`LearningStats`, `analyze_trades/analyze_journal`), `agents/learning_agent.py` (`LearningAgent.analyze` → `LearningReport`, `LearningRecommendation`; min 50 trades gate → 'insufficient_data'; backtest before/after; `auto_apply=False`), 17 tests.
- **Dashboard (Wave 5, UI-01..07 + LEARN-05)** ✅ `app.py` (Streamlit, 4 tabs; pure testable data fns `load_scan_output`/`scan_candidates_df`/`filter_candidates`/`reasoning_df`/`open_positions_df`/`closed_trades_df`/`journal_summary`/`learning_view`; approve/reject placeholder). Imports with NO side effects. 18 tests. Run: `streamlit run app.py`.
### Round 3 — UI + Learning  [STATUS: ✅ COMPLETE]
- **Learning (Wave 7, LEARN-01..05)** ✅ `modules/learning.py` (pure closed-trade outcome analytics: headline stats + per-pattern/per-sector breakdowns; expectancy/pct conventions documented) and `agents/learning_agent.py` (`LearningAgent.analyze(journal)` → backtest-validated, human-approved config recommendations; `auto_apply` always False; min-closed-trades gate; backtest imported lazily). No dedicated `test_learning.py`; the learning path is exercised indirectly via `tests/test_dashboard.py` (`learning_view`). **Follow-up for Wave 8: add direct unit tests for `modules/learning.py` and `agents/learning_agent.py`.**
- **Dashboard (Wave 5, UI-01..07)** ✅ `app.py` (streamlit). Import-safe (no `st.*` at import; pure data-shaping functions unit-testable). Tabs: Scanner Output (metrics, filters, funnel chart), Agent Reasoning, Trade Journal (open/closed + summary + PnL chart), Learning Insights (approve/reject placeholders, no auto-apply). 18 tests in `tests/test_dashboard.py`.

### Round 4 — Full-system QA (Wave 8, QA-01..06)  [STATUS: ✅ COMPLETE — verified 178 passed]
- **CLI wiring (ALERT-03)** ✅ `run_scanner.py` gained `--pipeline` (scanner→research→risk, persists) and `--monitor` (execution monitor once, dry-run alerts); functions `pipeline_scan`, `monitor_once` are injection-testable.
- **Integration (QA-02)** ✅ `tests/test_integration.py`: end-to-end pipeline→journal→execution→dry-run alert, + real scanner funnel driven by a fake OHLCV provider (no network).
- **CLI smoke (QA-03)** ✅ `tests/test_cli.py`: flag dispatch + `monitor_once` breach handling.
- **Docs (QA-01/04/06)** ✅ `docs/PRD_FEATURE_MATRIX.md`, `docs/DASHBOARD_CHECKLIST.md`, `docs/FINAL_STATUS.md`.
- **Regression (QA-05)** ✅ Full suite 178 passed; all new modules import without crewai/langchain/apprise.

---

## 📁 Files to be created (target inventory)

Missing per audit → to build:
- [x] `modules/backtest.py`  (Round 1) ✅
- [x] `modules/journal.py`   (Round 1) ✅
- [x] `agents/llm.py` (shared LLM abstraction) ✅
- [x] `tools/web_tools.py`   (Round 2) ✅
- [x] `tools/risk_tools.py`  (Round 2) ✅
- [x] `tools/alert_tools.py` (Round 2) ✅
- [x] `agents/research_agent.py`   (Round 2) ✅
- [x] `agents/risk_agent.py`       (Round 2) ✅
- [x] `agents/execution_agent.py`  (Round 2) ✅
- [x] `orchestrator/pipeline.py` scanner→research→risk wiring (main) ✅
- [x] `reports/` backtest artifact ✅
- [x] `agents/learning_agent.py`   (Round 3) ✅
- [x] `modules/learning.py`        (Round 3) ✅
- [x] `app.py`                     (Round 3) ✅
- [x] `tests/test_integration.py`, `tests/test_cli.py` (Round 4) ✅
- [x] `docs/PRD_FEATURE_MATRIX.md`, `docs/DASHBOARD_CHECKLIST.md`, `docs/FINAL_STATUS.md` (Round 4) ✅
- [x] tests for every module above ✅ (14 test files, 178 tests)
- [x] `tests/test_dashboard.py`    (Round 3) ✅ (18 tests; covers app + learning path)
- [ ] direct tests for `modules/learning.py` / `agents/learning_agent.py` (deferred to Wave 8)

---

## 🚨 Constraints / Design Rules (enforce in every agent)
1. Deterministic modules stay pure and LLM-free; agents wrap them.
2. Agents take an **injectable LLM client** (default deterministic/heuristic fallback) so tests run with a fake — no API key, no network, no crewai/langchain hard import at module top.
3. New features add NEW files; do not edit another round's files.
4. Every new module ships with tests that pass under the canonical pytest command.
5. Keep heavy imports (crewai, langchain, streamlit, apprise, vectorbt) lazy/optional.
6. Money math (R:R, sizing, heat, expectancy) must be deterministic and unit-tested.

---

## 🔁 Verification Log (ralph-wiggum loop)
- Baseline: **31 passed** (test_scanner.py, test_config.py) in venv. ✅
- Round 1: **71 passed** (+backtest 23, +journal 17). ✅
- Round 2: **135 passed** (+research 14, +risk 24, +exec/alerts 23, +orchestrator 3). ✅
- Round 3: **170 passed** (+learning 17, +dashboard 18). ✅
- Round 4: **178 passed** (+integration 2, +cli 6). ✅ FINAL — full regression green.

---

## ▶️ Next action if resuming now
All PRD waves (0–8) are built and the deterministic + mockable-agent suite is green (178 passed). On resume:
1. Recreate venv (see Environment Reproduction) and run `python -m pytest -q` — expect 178 passed.
2. Read `docs/FINAL_STATUS.md` for Completed / Partial / Deferred breakdown, and `docs/PRD_FEATURE_MATRIX.md` for the feature→test map.
3. Try it: `python run_scanner.py --dry-run` (needs network for yfinance), `--deterministic` (writes JSON/CSV to data/), `--pipeline`, `--monitor`; `streamlit run app.py` for the dashboard.
4. To enable the live/LLM path (Deferred items): `pip install -r requirements.txt` (adds crewai/langchain/nsepy/etc.), set `ANTHROPIC_API_KEY` in `.env`, expand `data/universe/nse_universe.csv`, and implement an NSEpy provider behind the existing `OHLCVProvider` abstraction. Then `python run_scanner.py` (full CrewAI scan).

### Suggested next enhancements (not blocking MVP)
- Expand universe CSV toward the full NSE list + a real NSEpy/Screener.in data provider.
- Wire a real web-research source behind `tools/web_tools.WebResearchSource`.
- Run a real 2020–2025 backtest and commit the report; gate live-readiness on PRD thresholds.
- Not committed yet — this session left all changes in the working tree (see `git status`).
