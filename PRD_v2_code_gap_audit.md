# StockScanner PRD v2 Code Gap Audit

Generated: 2026-07-08

Scope: Review current repository state against `PRD_v2_with_recommendations.md`, using an iterative multi-agent audit loop plus local verification. This file is intentionally separate from the PRD for review before merge.

## Executive Summary

The repository is currently a **Scanner Agent proof of concept**, not the full PRD v2 multi-agent application.

What works today:

- Core Python scanner modules exist for ingestion, fundamental screening, pattern detection, volume profile, and risk/reward sizing.
- A single CrewAI Scanner Agent wrapper exists.
- A single-agent orchestrator exists.
- Synthetic unit tests for patterns, volume profile, risk sizing, and a small end-to-end module pipeline pass.
- A live dry run can fetch one symbol and execute core modules when network access is allowed.

What is not yet built:

- Research Agent, adversarial Risk Agent, Execution Agent, and Learning Agent.
- Backtesting framework.
- Streamlit dashboard.
- Trade journal database/schema.
- Alert tools and Telegram/Apprise integration.
- Full NSE/BSE universe ingestion.
- NSEpy primary data source.
- Screener.in or equivalent fundamental data integration.
- Portfolio-aware scan execution using heat and sector constraints.

Current state estimate:

- Core deterministic scanner modules: **partial but real**.
- PRD v2 MVP application: **early POC, not production-ready**.
- Live trading readiness: **not ready** because mandatory backtesting, trade journal, research/risk validation, and operational monitoring are missing.

## Ralph Wiggum Loop Results

The review was run as separate passes, then reconciled:

1. PRD checklist pass: extracted required feature set across core pipeline, agents, dashboard, alerts, backtesting, config, and success criteria.
2. Implementation coverage pass: inspected repo files and classified implemented, partial, stub/mock, and missing features.
3. Verification pass: attempted local tests and scanner dry run.
4. Main reconciliation pass: reran verification using the bundled Python runtime and folded the results into this report.

Important note: two subagents could not see Python on PATH. The main pass found and used the bundled Codex Python runtime at:

```text
C:\Users\asbhat\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

## Verification Summary

### Commands Attempted

| Check | Result | Notes |
|---|---:|---|
| `python --version` from PATH | Failed | `python` is not available on PATH. |
| Bundled Python version | Passed | Python 3.12.13. |
| `pip install -r requirements.txt` | Failed | `nsepy==0.9.1` is not resolvable for this runtime; pip showed available versions only up to `0.8`. |
| Minimal test dependency install | Passed | Installed into bundled runtime only, not repo source. |
| `pytest -q` | Passed | 20 tests passed. |
| `run_scanner.py --dry-run` without escalation | Failed | Network/cache restrictions blocked yfinance. |
| `run_scanner.py --dry-run` with escalation | Passed | Fetched `RELIANCE.NS`, ran fundamental, pattern, volume modules. |
| `validate_models.py` | Failed | Requires `DATABRICKS_TOKEN` or `DATABRICKS_PAT`. |

### Test Output

```text
20 passed in 1.07s
```

### Dry Run Output Summary

`run_scanner.py --dry-run` passed after network/cache permission:

- Ingested 103 bars for `RELIANCE.NS`.
- Fundamental filter passed.
- Pattern detector found no setup for this symbol, which is acceptable for a dry run.
- Volume profile built 5 HVNs and 6 LVNs.
- Risk gate was not exercised because no HVN support existed below current price.
- Script reported all modules operational.

The dry run also exposed a cache dependency gap:

```text
Cache write error: Unable to find a usable engine; tried using: 'pyarrow', 'fastparquet'.
```

The code writes parquet cache files, but `requirements.txt` does not declare `pyarrow` or `fastparquet`.

## Current Repository Inventory

Implemented files:

- `config.py`
- `run_scanner.py`
- `modules/ingest.py`
- `modules/fundamental.py`
- `modules/patterns.py`
- `modules/volume.py`
- `modules/risk.py`
- `agents/base.py`
- `agents/scanner_agent.py`
- `tools/data_tools.py`
- `tools/analysis_tools.py`
- `orchestrator/crew.py`
- `tests/test_scanner.py`
- `validate_models.py`

Missing PRD files/modules:

- `app.py`
- `modules/backtest.py`
- `agents/research_agent.py`
- `agents/risk_agent.py`
- `agents/execution_agent.py`
- `agents/learning_agent.py`
- `tools/web_tools.py`
- `tools/alert_tools.py`
- trade journal models/schema/migrations
- dashboard tests
- agent integration tests
- backtest reports/notebooks

## PRD Feature Checklist

Legend:

- Pass: implemented and verified at least locally.
- Partial: meaningful implementation exists, but PRD behavior is incomplete.
- Fail: required by PRD and absent or not wired.
- Blocked: present but could not be verified due credentials/environment.

### Core Pipeline

| PRD Requirement | Status | Evidence / Gap |
|---|---:|---|
| EOD scanner pipeline: ingestion -> fundamental -> technical -> volume -> risk -> ranking | Partial | Core modules exist. Ranking is mostly delegated to LLM output; script dry run does not produce ranked shortlist. |
| Full NSE/BSE universe around 5000 stocks | Fail | `get_nse_universe()` returns 30 hardcoded liquid NSE names. |
| NSEpy primary, yfinance fallback | Fail | Code imports and uses yfinance only. NSEpy is listed in requirements but not implemented. |
| Data caching | Partial | Cache path exists, but parquet writes fail without pyarrow/fastparquet. |
| Fundamental filter: market cap, revenue growth, debt/equity, promoter holding | Partial | yfinance fields used where available. Promoter holding is always `None`; missing optional fields pass. Mock `fetch_fundamentals()` remains. |
| Technical patterns: consolidation, higher lows, range tightening | Pass | Implemented and covered by synthetic tests. |
| False breakout protection / 2-bar hold rule | Fail | Not implemented. |
| Volume profile HVN/LVN | Pass | Implemented and covered by tests. |
| Risk/reward gate and position sizing | Pass | Implemented and covered by tests. |
| Portfolio heat and sector limits | Partial | `RiskManager` supports them, but scanner tool calls `validate(setup)` without portfolio state. |
| Market regime filter with bull/bear R:R | Partial | Tool exists, but default data fetch is too short for a 200-day SMA and result is not wired into risk validation. |
| CSV output of 5-10 candidates | Fail | Full scan saves JSON only when LLM returns setups; no CSV output path. |

### Multi-Agent Layer

| PRD Requirement | Status | Evidence / Gap |
|---|---:|---|
| Scanner Agent | Partial | Exists, wraps tools, but output depends on one LLM agent rather than deterministic pipeline stats. |
| Scanner output includes funnel counts | Fail | PRD expects total scanned/passed counts. Current final schema omits counts. |
| Research Agent | Fail | File absent. No news, sentiment, citations, Screener integration, red flag workflow. |
| Adversarial Risk Agent | Fail | File absent. `modules/risk.py` is deterministic risk sizing, not adversarial validation. |
| Execution Agent | Fail | File absent. No monitoring, trailing stop, alert, or journal update logic. |
| Learning Agent | Fail | File absent. No outcome analysis or threshold proposal loop. |
| Crew orchestration scan -> research -> risk -> execution/learning | Fail | `orchestrator/crew.py` is explicitly Scanner Agent only. |
| Agent reasoning persisted for dashboard | Fail | No dashboard or persistence layer. |

### Dashboard, Alerts, and Journal

| PRD Requirement | Status | Evidence / Gap |
|---|---:|---|
| Streamlit dashboard `app.py` | Fail | File absent. |
| Scanner output tab | Fail | No dashboard. |
| Agent reasoning tab | Fail | No dashboard and missing agents. |
| Trade journal tab | Fail | No trade journal DB/schema. |
| Learning insights tab | Fail | No Learning Agent or dashboard. |
| Telegram daily alert | Fail | No alert tool. |
| Stop loss breach alert | Fail | No Execution Agent or alert tool. |
| Dashboard loads under 5 seconds | Blocked | Cannot verify without dashboard. |

### Backtesting and Learning

| PRD Requirement | Status | Evidence / Gap |
|---|---:|---|
| Backtest before live trading | Fail | `modules/backtest.py` absent. |
| Backtest 2020-2025 historical data | Fail | No implementation. |
| Positive expectancy, Sharpe, win rate, max drawdown checks | Fail | No backtest or metrics pipeline. |
| Walk-forward testing | Fail | No implementation. |
| Parameter optimization | Fail | No implementation. |
| Trade outcome analytics | Fail | No journal/learning module. |
| Human approval before config changes | Fail | No learning/config proposal workflow. |

### Config and Environment

| PRD Requirement | Status | Evidence / Gap |
|---|---:|---|
| Pydantic config | Pass | `ScannerConfig` exists. |
| Capital, risk, min R:R, heat, max positions, sector limit | Pass | Present in config and risk manager defaults. |
| Fundamental thresholds configurable | Partial | Present in config, but runtime modules instantiate hardcoded defaults. |
| Technical/volume thresholds configurable | Partial | Present in config, but runtime modules instantiate hardcoded defaults. |
| Agent model names in config | Pass | Present in config. |
| API keys via environment | Partial | Present, but `ANTHROPIC_API_KEY` is required by config and BaseAgent; no `.env.example` is present. |
| Requirements reproducible | Fail | `nsepy==0.9.1` cannot be installed in tested runtime; parquet dependency missing. |

## Key Findings and Risks

### P0: Declared dependencies are not reproducible

`pip install -r requirements.txt` fails because `nsepy==0.9.1` is not available for the tested Python 3.12 runtime. This blocks any clean install workflow.

Recommended action:

- Pin a supported Python version, likely 3.10 or 3.11 if current dependency set requires it, or update dependency pins.
- Replace `nsepy==0.9.1` with an available/maintained source or implement another NSE data provider.
- Add `pyarrow` or `fastparquet` if parquet caching remains the selected cache format.

### P0: Backtesting is missing despite being mandatory before live use

The PRD says backtesting is required before live trading, with explicit metrics and walk-forward validation. There is no `modules/backtest.py`.

Recommended action:

- Build deterministic backtest module before adding live trading or alerts.
- Ensure backtest logic shares scanner/risk code paths to avoid strategy drift.
- Produce a report artifact with expectancy, Sharpe, drawdown, win rate, trade count, and walk-forward results.

### P0: PRD multi-agent system is mostly absent

Only the Scanner Agent exists. Research, adversarial Risk, Execution, and Learning agents are not implemented.

Recommended action:

- Build agents in dependency order: Research Agent, Risk Agent, then Execution Agent, then Learning Agent.
- Keep deterministic modules separate from LLM reasoning so tests can validate the trade math.

### P1: Portfolio risk controls exist but are not enforced in scanner execution

`RiskManager.validate_batch()` can apply portfolio heat and sector limits, but `validate_risk_reward_tool()` validates one setup without portfolio context.

Recommended action:

- Move candidate validation into deterministic batch code.
- Track portfolio state from journal/open positions.
- Have the Scanner Agent consume batch results instead of asking the LLM to coordinate risk state.

### P1: Market regime logic is not functional end-to-end

The market regime tool requires 200 rows but data ingestion defaults to roughly 150 calendar days. Even when a regime is returned, the risk tool uses a singleton `RiskManager()` defaulted to bull market.

Recommended action:

- Fetch enough Nifty history for 200-day SMA plus buffer.
- Pass detected regime into risk validation.
- Add tests for bull and bear thresholds.

### P1: Fundamental screening is incomplete

Promoter holding is not sourced, missing fields are treated as pass, and `DataIngestion.fetch_fundamentals()` returns mock data.

Recommended action:

- Add Screener.in CSV/API ingestion or another reliable fundamental source.
- Decide which fields are mandatory versus optional.
- Record stale-data age and reject/flag stale fundamentals.

### P1: Data universe is too small for PRD scope

The PRD expects NSE/BSE universe scanning. Code scans 30 curated names.

Recommended action:

- Implement a real universe loader.
- Add filters for liquidity and exchange.
- Cache the universe with timestamp and source metadata.

### P1: Model validation script is misleading

`validate_models.py` can mark all candidate model names available because the "foundation_model_api" attempt posts to a hardcoded `databricks-claude-opus-4-6` endpoint regardless of candidate model ID. Existing results show many requested model IDs returning as the same Opus model.

Recommended action:

- Validate each candidate against its actual endpoint/model routing.
- Report "endpoint reachable" separately from "specific model available".

### P2: Tests are too narrow for PRD confidence

Current tests are good for core math smoke coverage, but do not cover:

- config wiring
- ingestion cache behavior
- tool wrappers
- market regime
- orchestrator parsing
- agent construction
- full scanner script output
- missing data and API failures
- portfolio batch behavior in the actual scan path

Recommended action:

- Add deterministic unit tests first.
- Add integration tests with mocked yfinance responses.
- Add golden-output tests for scanner candidate generation.

## Module-by-Module State

### `config.py`

Status: Partial pass.

Strengths:

- Good Pydantic settings structure.
- Contains most PRD thresholds and agent model names.

Gaps:

- Runtime classes/tools mostly instantiate hardcoded defaults rather than reading config.
- `ANTHROPIC_API_KEY` is required for config construction, which may make non-agent test workflows brittle.
- No `.env.example` exists despite `.gitignore` allowing one.

### `modules/ingest.py`

Status: Partial.

Strengths:

- yfinance OHLCV ingestion exists.
- Basic caching structure exists.
- Basic missing-data check exists.

Gaps:

- No NSEpy implementation despite docstring/PRD.
- Universe is hardcoded to 30 stocks.
- Parquet cache dependency missing.
- `fetch_fundamentals()` returns mock values.
- Live data behavior depends on network and yfinance cache permissions.

### `modules/fundamental.py`

Status: Partial.

Strengths:

- Encapsulated dataclasses and clear checks.
- Market cap, revenue growth, and debt/equity are attempted via yfinance.

Gaps:

- Promoter holding not implemented.
- Missing optional data passes by default.
- No Screener.in CSV/API support.
- No stale-data reporting.

### `modules/patterns.py`

Status: Pass for MVP pattern skeleton.

Strengths:

- Three PRD MVP patterns implemented.
- Synthetic tests pass.

Gaps:

- No false-breakout 2-bar hold rule.
- Pattern thresholds are not loaded from config in tool path.
- No backtest validation that patterns have edge.

### `modules/volume.py`

Status: Pass for core HVN/LVN skeleton.

Strengths:

- HVN/LVN histogram implementation exists.
- Tests cover profile creation and helper behavior.

Gaps:

- Does not use `VOLUME_LOOKBACK_DAYS` from config in the tool path.
- Current target selection is simple first LVN above price, not optimized by R:R or structure.

### `modules/risk.py`

Status: Partial.

Strengths:

- R:R calculation, fixed fractional sizing, heat limit, max positions, and sector limit exist.
- Unit tests pass.

Gaps:

- Live tool path validates a single setup without portfolio state.
- Market regime is not dynamically applied.
- No volatility/beta/drawdown/correlation analysis from PRD Risk Agent.

### `agents/scanner_agent.py`

Status: Partial.

Strengths:

- CrewAI Scanner Agent exists.
- Task description maps the 5-stage pipeline.

Gaps:

- Depends heavily on LLM orchestration for a deterministic pipeline.
- Does not expose PRD funnel counts in structured output.
- No agent tests.
- Requires external LLM credentials.

### `orchestrator/crew.py`

Status: Partial.

Strengths:

- Minimal one-agent CrewAI wrapper exists.
- JSON parsing helper exists.

Gaps:

- Explicitly Scanner Agent only.
- No Research/Risk/Execution/Learning workflow.
- No retry/fallback handling.
- No persistence of intermediate agent reasoning.

### `run_scanner.py`

Status: Partial.

Strengths:

- CLI exists.
- Dry run exercises modules without LLM.
- Full scan can save JSON results.

Gaps:

- No CSV output.
- Full scan requires Anthropic credentials and CrewAI dependencies not verified because full requirements install failed.
- Dry run does not exercise batch portfolio risk.

### `tests/test_scanner.py`

Status: Partial pass.

Strengths:

- 20 tests pass.
- Core pattern, volume, risk, and module-pipeline smoke tests exist.

Gaps:

- No tests for agents/tools/config/orchestrator/scripts.
- No mocked ingestion or fundamental API tests.
- No regression fixtures from real historical market data.

## Recommended Build Order

1. Fix environment reproducibility.
   - Decide supported Python version.
   - Fix `requirements.txt`.
   - Add missing parquet dependency or switch cache format.
   - Add `.env.example`.

2. Make deterministic scanner pipeline independent of LLM.
   - Create a function that scans the universe and returns structured candidates plus funnel counts.
   - Make Scanner Agent wrap that output rather than personally driving every tool call.

3. Wire config into modules and tools.
   - Use `get_config()` for thresholds.
   - Add tests proving env/config overrides affect behavior.

4. Implement real data and fundamentals.
   - Real NSE/BSE universe loader.
   - NSE data provider or maintained replacement.
   - Screener.in CSV/API ingestion.

5. Build backtesting before live features.
   - `modules/backtest.py`.
   - Walk-forward validation.
   - Required PRD success metrics.

6. Add persistence.
   - Trade journal schema.
   - Open positions table.
   - Candidate/rejection history for Risk Agent performance measurement.

7. Add Research Agent and adversarial Risk Agent.
   - Research Agent should include citations and red flags.
   - Risk Agent should consume scanner + research + portfolio state.

8. Add dashboard and alerts.
   - Streamlit tabs 1-3 first.
   - Telegram/Apprise after journal and execution monitoring exist.

9. Add Execution and Learning agents.
   - Execution Agent needs journal/open position state.
   - Learning Agent needs enough closed trades or backtest samples.

## Acceptance Criteria for Next Audit

Before calling the PRD MVP "working", the next audit should pass:

- Clean install from `requirements.txt` in a documented Python version.
- `pytest` passes without manual dependency workarounds.
- Core deterministic scanner can run without LLM and output JSON/CSV with funnel counts.
- Full scanner can scan more than the 30-stock demo universe.
- Market regime affects minimum R:R in verified tests.
- Portfolio heat and sector limits are enforced in scanner output, not just in isolated unit tests.
- Backtest report exists and meets or clearly fails PRD success metrics.
- Research Agent and Risk Agent files exist and are wired into CrewAI orchestration.
- Streamlit dashboard exists with at least scanner output, reasoning, and journal tabs.
- Trade journal schema exists and stores open/closed trade lifecycle.
- Alerts can be dry-run tested without sending live messages.

## Final State

No functional source code changes were made during this audit. The only intended repository change is this report file.

Ignored runtime artifacts may have been created during verification:

- `.pytest_cache/`
- `logs/`
- `data/cache/`
- `__pycache__/`

These are already covered by `.gitignore`.
