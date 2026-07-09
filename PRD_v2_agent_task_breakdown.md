# StockScanner PRD v2 Agent Task Breakdown

Generated: 2026-07-08

Purpose: Break the PRD into implementation tasks that can be assigned to parallel agents while still converging on one complete stock scanner application.

Related files:

- `PRD_v2_with_recommendations.md`
- `PRD_v2_code_gap_audit.md`

## Target End State

The completed application should provide:

- A reproducible Python environment.
- A deterministic scanner pipeline that can run without LLMs.
- A multi-agent layer that adds research, adversarial risk review, execution monitoring, and learning.
- A backtest-first validation workflow before live use.
- A trade journal and persistence layer.
- A Streamlit dashboard for scanner output, reasoning, journal, and learning insights.
- Alerting for daily setups and stop/target events.
- Tests that verify each module, each agent contract, and the end-to-end workflow.

## Workstream Agents

These are implementation workstreams. They can be mapped to Codex subagents, human owners, or sprint tickets.

| Workstream Agent | Owns | Main Output |
|---|---|---|
| Foundation Agent | environment, dependencies, config, repo hygiene | clean install, config wiring, smoke tests |
| Data Agent | universe, OHLCV, fundamentals, caching | reliable NSE/BSE data and fundamental snapshots |
| Scanner Agent | deterministic 5-stage scanner | candidates, funnel counts, JSON/CSV outputs |
| Backtest Agent | historical validation and metrics | backtest module, reports, walk-forward tests |
| Research Agent | news, fundamentals enrichment, citations | research summaries, sentiment, red flags |
| Risk Agent | adversarial validation and portfolio constraints | approved/rejected/conditional setup decisions |
| Execution Agent | journal, open positions, monitoring, alerts | trade lifecycle and stop/target notifications |
| Learning Agent | outcome analytics and threshold recommendations | learning reports and proposed config changes |
| Dashboard Agent | Streamlit UI | multi-tab user application |
| QA Agent | test strategy and verification loop | unit, integration, smoke, and acceptance tests |
| Orchestrator Agent | CrewAI wiring and app flow integration | end-to-end agent workflow |

## Delivery Waves

### Wave 0: Reproducible Foundation

Goal: Anyone can install, run tests, and run a dry scanner flow.

Parallelism: mostly Foundation Agent plus QA Agent.

| Task ID | Owner | Task | Files | Depends On | Acceptance |
|---|---|---|---|---|---|
| FND-01 | Foundation Agent | Decide supported Python version and document it | `README.md`, optional `.python-version` | None | Clean setup instructions name exact Python version. |
| FND-02 | Foundation Agent | Fix `requirements.txt` install failure | `requirements.txt` | FND-01 | `pip install -r requirements.txt` succeeds in clean env. |
| FND-03 | Foundation Agent | Add parquet dependency or switch cache format | `requirements.txt`, `modules/ingest.py` if needed | FND-02 | Scanner dry run writes cache without warning. |
| FND-04 | Foundation Agent | Add `.env.example` | `.env.example` | None | Required and optional env vars are documented. |
| FND-05 | Foundation Agent | Make config usable without mandatory LLM key for non-agent tests | `config.py`, tests | FND-04 | Core module tests run without `ANTHROPIC_API_KEY`. |
| FND-06 | QA Agent | Add install/test smoke command doc | `README.md` | FND-02 | New contributor can run test command from docs. |

### Wave 1: Deterministic Scanner Core

Goal: Make the scanner work as reliable application logic before asking LLM agents to reason over it.

Parallelism: Data Agent and Scanner Agent can work in parallel after FND-02, but must coordinate shared config objects.

| Task ID | Owner | Task | Files | Depends On | Acceptance |
|---|---|---|---|---|---|
| DATA-01 | Data Agent | Replace 30-stock hardcoded universe with real universe loader | `modules/ingest.py`, `data/` docs/tests | FND-02 | Universe loader can return broader NSE list and a small test fixture. |
| DATA-02 | Data Agent | Implement provider abstraction for OHLCV | `modules/ingest.py`, tests | FND-02 | yfinance provider still works; alternate NSE provider can be plugged in. |
| DATA-03 | Data Agent | Add data quality metadata | `modules/ingest.py`, tests | DATA-02 | Fetch result records missing data %, source, adjusted status, cache age. |
| DATA-04 | Data Agent | Implement fundamental data source path | `modules/fundamental.py`, `data/fundamentals/`, tests | FND-02 | Market cap, growth, debt/equity, promoter holding can be read from fixture CSV. |
| SCAN-01 | Scanner Agent | Wire `ScannerConfig` into modules and tools | `modules/*.py`, `tools/*.py`, tests | FND-05 | Test proves config override changes pattern/risk behavior. |
| SCAN-02 | Scanner Agent | Build deterministic pipeline service | new `modules/scanner.py` or similar, `run_scanner.py`, tests | DATA-01, DATA-04, SCAN-01 | Function returns candidates plus funnel counts without LLM. |
| SCAN-03 | Scanner Agent | Enforce market regime in risk gate | `tools/analysis_tools.py`, `modules/risk.py`, scanner pipeline tests | SCAN-02 | Bear market uses higher min R:R in tests. |
| SCAN-04 | Scanner Agent | Enforce portfolio heat and sector limits in actual scan path | `modules/scanner.py`, `modules/risk.py`, tests | SCAN-02 | Batch scanner rejects setups that exceed heat/sector limits. |
| SCAN-05 | Scanner Agent | Add false-breakout 2-bar hold rule | `modules/patterns.py`, tests | SCAN-02 | Pattern tests cover breakout hold pass/fail. |
| SCAN-06 | Scanner Agent | Add JSON and CSV output | `run_scanner.py`, `modules/scanner.py`, tests | SCAN-02 | Dry run or fixture run writes expected JSON and CSV. |

### Wave 2: Backtest-First Validation

Goal: Prove or disprove the scanner edge before adding live monitoring.

Parallelism: Backtest Agent can start after SCAN-02 and iterate with Scanner Agent.

| Task ID | Owner | Task | Files | Depends On | Acceptance |
|---|---|---|---|---|---|
| BT-01 | Backtest Agent | Create backtest module skeleton | `modules/backtest.py`, tests | SCAN-02 | Backtest can run against fixture OHLCV data. |
| BT-02 | Backtest Agent | Share scanner/risk logic in backtest | `modules/backtest.py`, `modules/scanner.py` | BT-01 | Backtest does not duplicate pattern/risk logic unnecessarily. |
| BT-03 | Backtest Agent | Implement metrics | `modules/backtest.py`, tests | BT-02 | Expectancy, win rate, Sharpe, drawdown, trade count are computed. |
| BT-04 | Backtest Agent | Implement walk-forward split | `modules/backtest.py`, tests | BT-03 | Train/test date ranges are configurable and tested. |
| BT-05 | Backtest Agent | Add parameter optimization hooks | `modules/backtest.py`, tests | BT-03 | Min R:R, consolidation range, and volume multiplier can be swept. |
| BT-06 | Backtest Agent | Generate report artifact | `reports/`, README docs | BT-05 | Report says pass/fail against PRD thresholds. |

### Wave 3: Persistence and Journal

Goal: Store candidates, decisions, open positions, closed trades, and agent reasoning.

Parallelism: Execution Agent and Dashboard Agent need this, so do it before UI/alerts.

| Task ID | Owner | Task | Files | Depends On | Acceptance |
|---|---|---|---|---|---|
| DB-01 | Execution Agent | Define SQLAlchemy models/schema | new `modules/journal.py` or `db/`, tests | FND-02 | Tables exist for candidates, agent decisions, open positions, closed trades. |
| DB-02 | Execution Agent | Add repository/service functions | `modules/journal.py`, tests | DB-01 | Can create, update, close, and query trades in SQLite. |
| DB-03 | Execution Agent | Persist scanner candidates and rejected setups | `modules/scanner.py`, `modules/journal.py` | DB-02, SCAN-02 | Candidate history includes rejected setups and reasons. |
| DB-04 | QA Agent | Add fixture database tests | tests | DB-02 | Tests run isolated with temp SQLite DB. |

### Wave 4: Research and Risk Agents

Goal: Add explainable reasoning and adversarial validation after deterministic scanner candidates exist.

Parallelism: Research Agent and Risk Agent can start together once scanner outputs and journal schema are stable.

| Task ID | Owner | Task | Files | Depends On | Acceptance |
|---|---|---|---|---|---|
| RES-01 | Research Agent | Create `tools/web_tools.py` with mockable interfaces | `tools/web_tools.py`, tests | FND-02 | News/search/screener calls can be mocked in tests. |
| RES-02 | Research Agent | Create `agents/research_agent.py` | `agents/research_agent.py`, tests | RES-01, SCAN-02 | Agent contract returns summary, sentiment, red flags, citations, confidence. |
| RES-03 | Research Agent | Add citation and staleness requirements | `agents/research_agent.py`, `tools/web_tools.py` | RES-02 | Research result flags missing citations or stale fundamentals. |
| RISK-01 | Risk Agent | Add volatility/beta/correlation tools | `tools/analysis_tools.py`, tests | SCAN-02 | Tools return deterministic risk metrics from fixture data. |
| RISK-02 | Risk Agent | Create `agents/risk_agent.py` | `agents/risk_agent.py`, tests | RISK-01, RES-02 | Agent returns APPROVED, REJECTED, or CONDITIONAL with concerns. |
| RISK-03 | Risk Agent | Implement position size adjustment contract | `agents/risk_agent.py`, `modules/risk.py` | RISK-02 | Risk agent can reduce risk multiplier with reason. |
| ORCH-01 | Orchestrator Agent | Wire scanner -> research -> risk flow | `orchestrator/crew.py`, tests | RES-02, RISK-02 | End-to-end mocked agent flow returns final approved list. |
| ORCH-02 | Orchestrator Agent | Persist agent reasoning | `orchestrator/crew.py`, `modules/journal.py` | ORCH-01, DB-03 | Dashboard can query reasoning by symbol/run. |

### Wave 5: Dashboard

Goal: Provide the usable application surface.

Parallelism: Dashboard Agent can build with fixture data while Orchestrator Agent finalizes live flow.

| Task ID | Owner | Task | Files | Depends On | Acceptance |
|---|---|---|---|---|---|
| UI-01 | Dashboard Agent | Create Streamlit app shell | `app.py` | DB-02 | App starts locally. |
| UI-02 | Dashboard Agent | Scanner Output tab | `app.py` | SCAN-06, DB-03 | Shows symbol, pattern, entry, stop, target, R:R, size, sector, risk status. |
| UI-03 | Dashboard Agent | Filters for scanner output | `app.py` | UI-02 | Pattern, min R:R, sector, volume confirmed, approved-only filters work. |
| UI-04 | Dashboard Agent | Agent Reasoning tab | `app.py` | ORCH-02 | Shows scanner, research, and risk reasoning per symbol. |
| UI-05 | Dashboard Agent | Trade Journal tab | `app.py` | DB-02 | Shows open/closed trades and summary stats. |
| UI-06 | Dashboard Agent | Learning Insights tab placeholder | `app.py` | LEARN-02 | Shows learning recommendations and backtest validation. |
| UI-07 | QA Agent | Dashboard smoke/performance test | tests or manual checklist | UI-05 | App loads scanner output under PRD target on local data. |

### Wave 6: Execution Monitoring and Alerts

Goal: Monitor approved/open trades and send notifications.

Parallelism: Execution Agent can build monitoring after DB-02; alert delivery can be dry-run before live tokens.

| Task ID | Owner | Task | Files | Depends On | Acceptance |
|---|---|---|---|---|---|
| EXEC-01 | Execution Agent | Create `agents/execution_agent.py` | `agents/execution_agent.py`, tests | DB-02 | Agent can inspect open trades and current prices from mocked provider. |
| EXEC-02 | Execution Agent | Implement stop/target breach detection | `agents/execution_agent.py`, `modules/journal.py`, tests | EXEC-01 | Stop, target, and open cases are tested. |
| EXEC-03 | Execution Agent | Implement trailing stop to breakeven after 1R | `agents/execution_agent.py`, tests | EXEC-02 | Breakeven adjustment is journaled. |
| ALERT-01 | Execution Agent | Create `tools/alert_tools.py` | `tools/alert_tools.py`, tests | EXEC-02 | Alert formatter supports daily summary and stop breach templates. |
| ALERT-02 | Execution Agent | Add dry-run alert mode | `tools/alert_tools.py`, config | ALERT-01 | Tests verify no live message is sent in dry-run. |
| ALERT-03 | Orchestrator Agent | Wire execution monitoring command | `run_scanner.py` or new CLI | EXEC-02, ALERT-02 | CLI can run monitoring once and return status. |

### Wave 7: Learning Agent

Goal: Analyze outcomes and propose changes only after enough evidence exists.

Parallelism: Learning Agent depends on journal and backtest metrics, but can use seeded fixture trades.

| Task ID | Owner | Task | Files | Depends On | Acceptance |
|---|---|---|---|---|---|
| LEARN-01 | Learning Agent | Create outcome analytics module | new `modules/learning.py`, tests | DB-02, BT-03 | Computes win rate, avg win/loss, expectancy, drawdown, pattern stats. |
| LEARN-02 | Learning Agent | Create `agents/learning_agent.py` | `agents/learning_agent.py`, tests | LEARN-01 | Agent returns recommendations and proposed config changes. |
| LEARN-03 | Learning Agent | Require minimum sample size | `modules/learning.py`, `agents/learning_agent.py` | LEARN-02 | Fewer than 50 closed trades returns insufficient-data state. |
| LEARN-04 | Learning Agent | Backtest recommendation before proposing approval | `agents/learning_agent.py`, `modules/backtest.py` | LEARN-02, BT-05 | Recommendation includes before/after backtest metrics. |
| LEARN-05 | Dashboard Agent | Add approve/reject UI placeholder | `app.py` | UI-06, LEARN-04 | Human approval is explicit; no auto-apply by default. |

### Wave 8: Full-System QA and Hardening

Goal: Verify the whole application against PRD acceptance criteria.

Parallelism: QA Agent owns this, each workstream fixes findings in its files.

| Task ID | Owner | Task | Files | Depends On | Acceptance |
|---|---|---|---|---|---|
| QA-01 | QA Agent | Build PRD feature checklist test matrix | `tests/`, `docs/` | All prior waves | Matrix maps every PRD feature to automated/manual verification. |
| QA-02 | QA Agent | Add integration tests with mocked external APIs | `tests/` | ORCH-01, EXEC-02 | Tests run without network or real tokens. |
| QA-03 | QA Agent | Add CLI smoke tests | `tests/`, `run_scanner.py` | SCAN-06, ALERT-03 | Dry-run, scan, monitor commands have predictable outputs. |
| QA-04 | QA Agent | Add dashboard visual/manual checklist | `docs/` | UI-07 | Manual UI checklist covers all PRD tabs and filters. |
| QA-05 | QA Agent | Run full regression loop | test outputs, final report | All prior waves | Install, tests, dry run, mocked full flow, dashboard smoke all pass. |
| QA-06 | Orchestrator Agent | Update PRD/audit with final status | PRD or separate final report | QA-05 | Completed, partial, deferred items are clearly documented. |

## Parallel Agent Assignment Plan

Use this split when running multiple implementation agents:

Round 1:

- Agent A: Foundation Agent owns FND-01 to FND-05.
- Agent B: QA Agent owns FND-06 and first smoke tests.
- Main agent: reviews dependency choices and keeps the branch coherent.

Round 2:

- Agent A: Data Agent owns DATA-01 to DATA-04.
- Agent B: Scanner Agent owns SCAN-01 to SCAN-06.
- Agent C: QA Agent expands tests for config, scanner, and ingestion.
- Main agent: resolves interface contracts between data and scanner.

Round 3:

- Agent A: Backtest Agent owns BT-01 to BT-06.
- Agent B: Execution Agent owns DB-01 to DB-04.
- Main agent: ensures scanner, backtest, and journal share canonical dataclasses.

Round 4:

- Agent A: Research Agent owns RES-01 to RES-03.
- Agent B: Risk Agent owns RISK-01 to RISK-03.
- Agent C: Orchestrator Agent owns ORCH-01 to ORCH-02.
- Main agent: verifies agent contracts and persistence.

Round 5:

- Agent A: Dashboard Agent owns UI-01 to UI-07.
- Agent B: Execution Agent owns EXEC-01 to ALERT-03.
- Agent C: Learning Agent owns LEARN-01 to LEARN-05.
- Main agent: keeps user workflows consistent end to end.

Round 6:

- QA Agent owns QA-01 to QA-05.
- Main agent fixes integration issues and prepares final status.

## Interface Contracts

These contracts should be stabilized early so agents can work independently.

### Scanner Candidate

```python
{
    "symbol": "TATAMOTORS.NS",
    "sector": "Auto",
    "pattern": "consolidation_after_uptrend",
    "entry": 450.0,
    "stop": 430.0,
    "target": 500.0,
    "rr_ratio": 2.5,
    "position_shares": 200,
    "position_inr": 90000.0,
    "capital_at_risk_inr": 4000.0,
    "volume_confirmed": True,
    "hvn_support": 425.0,
    "lvn_targets": [455.0, 480.0],
    "scanner_reasoning": "...",
}
```

### Scanner Run Summary

```python
{
    "run_id": "2026-07-08-eod",
    "timestamp": "2026-07-08T16:00:00+05:30",
    "market_regime": "bull",
    "total_scanned": 5000,
    "passed_fundamental": 247,
    "passed_technical": 38,
    "passed_volume": 18,
    "passed_rr_gate": 12,
    "candidates": []
}
```

### Research Result

```python
{
    "symbol": "TATAMOTORS.NS",
    "research_summary": "...",
    "sentiment_score": 0.75,
    "confidence_score": 0.82,
    "red_flags": [],
    "citations": [
        {"date": "2026-07-05", "headline": "...", "source": "...", "url": "..."}
    ],
}
```

### Risk Decision

```python
{
    "symbol": "TATAMOTORS.NS",
    "approval_status": "APPROVED",
    "concerns": [],
    "position_size_multiplier": 0.7,
    "stop_loss_validation": "PASS",
    "confidence_after_challenge": 0.68,
    "decision_reasoning": "..."
}
```

### Execution Event

```python
{
    "symbol": "TATAMOTORS.NS",
    "event_type": "STOP_BREACHED",
    "entry_price": 450.0,
    "exit_price": 428.0,
    "pnl": -4400.0,
    "pnl_percent": -4.8,
    "alert_sent": True,
    "journal_updated": True
}
```

### Learning Recommendation

```python
{
    "finding": "Range tightening underperformed",
    "action": "INCREASE_RR_THRESHOLD",
    "config_change": {"MIN_RR": {"range_tightening": 3.5}},
    "backtest_validation": {
        "before": {"sharpe": 1.2, "expectancy": 0.034},
        "after": {"sharpe": 1.6, "expectancy": 0.051}
    },
    "auto_apply": False
}
```

## Definition of Done

The application is complete when:

- Fresh install works from documented commands.
- Unit and integration tests pass.
- Deterministic scanner runs without LLM and produces candidates plus funnel counts.
- Multi-agent orchestrator runs scanner -> research -> risk and persists reasoning.
- Backtest report exists and gates live-readiness.
- Dashboard exposes scanner output, reasoning, journal, and learning insights.
- Execution monitor updates journal and emits dry-run alerts.
- Learning Agent produces evidence-backed recommendations with human approval required.
- The final PRD checklist has no unacknowledged MVP gaps.

## Immediate Next Implementation Slice

Start with Wave 0 and the first half of Wave 1:

1. FND-01 to FND-05: make the environment reproducible.
2. SCAN-01: wire config into modules/tools.
3. SCAN-02: create deterministic scanner pipeline service.
4. DATA-04: replace mock/optional fundamentals with fixture-backed CSV support.
5. QA tests for the above.

Reason: this creates a stable spine for every later agent. Without it, Research/Risk/Execution agents would be reasoning over a scanner that is still partly demo wiring.
