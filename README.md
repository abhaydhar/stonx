# StockScanner

StockScanner is a PRD v2 implementation of an AI-assisted swing-trading system
for NSE (National Stock Exchange of India) equities. It combines a
**deterministic technical scanner** with an optional **multi-agent LLM layer**
(research, risk, execution, learning), a **historical backtester**, a
**SQLite-backed trade journal**, and a **Streamlit dashboard**.

The core design principle: every piece of trading logic (patterns, volume
profile, R:R, position sizing, portfolio heat) is **deterministic, pure, and
unit-tested**. The LLM agents *wrap* that logic and add reasoning; they are
never required for the numbers to be correct. As a result the whole system runs
offline — with no API key, no network, and without the heavy agent
dependencies installed.

## Features

- **Deterministic scanner** — a fundamental → data → pattern → volume → risk
  funnel that produces ranked trade candidates with entry/stop/target,
  R:R ratio, and position sizing. No LLM required.
- **Pattern detection** — consolidation-after-uptrend breakouts, higher-lows,
  and range-tightening (volatility compression), with a false-breakout 2-bar
  hold rule.
- **Volume profile** — High Volume Nodes (HVN → stop anchors) and Low Volume
  Nodes (LVN → targets) computed from price-bin histograms.
- **Risk & position sizing** — regime-adjusted minimum R:R (bull 2.5x / bear
  3.5x), fixed-fractional sizing (1% risk), portfolio heat limit (≤5% total open
  risk), and sector diversification caps.
- **Market regime detection** — Nifty 200-SMA trend filter that tightens the
  risk gate in bear markets.
- **Multi-agent pipeline** — Research, Risk, Execution, and Learning agents.
  Each accepts an injectable LLM client and degrades to a deterministic
  rule-based fallback, so the pipeline runs offline.
- **Historical backtester** — event-driven backtester that reuses the exact
  scanner logic, with walk-forward validation, threshold optimization, and
  Markdown report generation.
- **Trade journal / persistence** — SQLite (via SQLAlchemy 2.0) store for scan
  candidates, per-agent reasoning, open positions, and closed trades, plus
  aggregate performance stats.
- **Streamlit dashboard** — tabs for Scanner Output, Agent Reasoning, Trade
  Journal, and Learning Insights.
- **Alerting** — dependency-light alert formatting and delivery (dry-run by
  default; optional apprise/Telegram for live sends).

## Architecture

```
run_scanner.py            CLI entry point (dry-run / deterministic / full LLM scan)
app.py                    Streamlit dashboard
config.py                 Pydantic-settings configuration (env-driven)

modules/                  Deterministic, LLM-free core (pure + unit-tested)
  ingest.py               OHLCV providers, CSV universe loader, data-quality metadata
  fundamental.py          Market cap / growth / debt / promoter-holding screen
  patterns.py             Breakout / higher-lows / range-tightening detection
  volume.py               Volume profile (HVN support / LVN targets)
  risk.py                 R:R gate, fixed-fractional sizing, portfolio heat, sectors
  scanner.py              DeterministicScanner funnel + JSON/CSV output
  backtest.py             Event-driven backtester, walk-forward, optimize, reports
  journal.py              SQLAlchemy trade journal (candidates, decisions, trades)
  learning.py             Closed-trade outcome analytics

agents/                   LLM agents (injectable client, deterministic fallback)
  llm.py                  LLMClient protocol, DeterministicLLM, FakeLLM, build_llm_client
  base.py                 CrewAI/LangChain base agent
  scanner_agent.py        CrewAI wrapper around the 5-stage scan
  research_agent.py       News/fundamentals research (sentiment, red flags, citations)
  risk_agent.py           Adversarial second-opinion risk review + size multiplier
  execution_agent.py      Open-position monitor (stop/target/trailing)
  learning_agent.py       Backtest-validated config recommendations (human-approved)

tools/                    LangChain @tool wrappers + deterministic helpers
  data_tools.py           Data ingestion / fundamentals tools
  analysis_tools.py       Pattern / volume / risk / regime tools
  risk_tools.py           Volatility, beta, ATR, drawdown metrics
  web_tools.py            Mockable web-research source interfaces
  alert_tools.py          AlertFormatter + AlertSender (dry-run by default)

orchestrator/
  pipeline.py             Deterministic scanner→research→risk pipeline (+ journal persistence)
  crew.py                 Legacy CrewAI orchestrator (LLM-driven path)

data/universe/            NSE universe CSV
tests/                    pytest suite for every module/agent
```

## Supported Python

Use **Python 3.12** (`.python-version` = 3.12.13). The repository includes
`.python-version` for tools that can read it.

## Setup

From the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill `ANTHROPIC_API_KEY` in `.env` **only** when running the full LLM agent
scan. Core unit tests, `python run_scanner.py --dry-run`, and
`python run_scanner.py --deterministic` do not require any LLM keys.

## Usage

### Run the scanner (CLI)

```powershell
# Test the module pipeline on one stock, no LLM, no output files
python run_scanner.py --dry-run

# Deterministic scan over the configured universe → writes JSON + CSV to ./data
python run_scanner.py --deterministic

# Deterministic scan with overrides
python run_scanner.py --deterministic --symbols RELIANCE.NS,TCS.NS --limit 5 --market-regime bear

# Full multi-agent LLM scan (requires ANTHROPIC_API_KEY)
python run_scanner.py
python run_scanner.py --verbose   # full CrewAI agent trace
```

| Flag | Description |
| --- | --- |
| `--dry-run` | Test module imports and the single-stock pipeline without the LLM. |
| `--deterministic` | Run the deterministic scanner service and write JSON/CSV output. |
| `--symbols` | Comma-separated symbols for the deterministic scan (default: configured universe). |
| `--limit` | Limit the number of symbols scanned. |
| `--market-regime` | Override market regime (`bull` / `bear`). |
| `--output-dir` | Directory for deterministic JSON/CSV outputs (default `./data`). |
| `--verbose` | Enable the full CrewAI agent trace (full scan only). |

`--dry-run` and `--deterministic` skip LLM calls but still fetch market data
through yfinance, so they need network access. The default `python
run_scanner.py` path requires `ANTHROPIC_API_KEY`.

### Launch the dashboard

```powershell
streamlit run app.py
```

Point the sidebar at a `scan_results_*.json` file and the journal DB URL
(default `sqlite:///./data/stonx.db`), then explore the Scanner Output, Agent
Reasoning, Trade Journal, and Learning Insights tabs.

### Run the deterministic pipeline programmatically

```python
from orchestrator.pipeline import ScanResearchRiskPipeline

result = ScanResearchRiskPipeline().run(limit=20)
for decision in result.approved:
    print(decision.symbol, decision.approval_status, decision.rr_ratio)
```

The pipeline runs scanner → research → risk fully offline (deterministic LLM
fallback) and persists candidates and per-agent reasoning to the trade journal.

## Configuration

All tunables live in `config.py` (`ScannerConfig`, Pydantic-settings) and can be
overridden via `.env` — see `.env.example` for the full list. Highlights:

- **Capital & risk**: `CAPITAL`, `RISK_PCT`, `MIN_RR`, `PORTFOLIO_HEAT_LIMIT`,
  `MAX_CONCURRENT_POSITIONS`.
- **Fundamental filters**: `MIN_MARKET_CAP_CR`, `MIN_REVENUE_GROWTH`,
  `MAX_DEBT_TO_EQUITY`, `MIN_PROMOTER_HOLDING`.
- **Technical / volume**: consolidation, uptrend, volume-spike, and
  volume-profile parameters.
- **Market regime**: `NIFTY_SMA_PERIOD`, `BULL_MARKET_MIN_RR`,
  `BEAR_MARKET_MIN_RR`.
- **Agent models**: per-agent Claude model names (`SCANNER_AGENT_MODEL`, etc.).
- **Persistence / paths**: `DATABASE_URL`, `DATA_CACHE_DIR`, `FUNDAMENTALS_DIR`.
- **Alerting**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## Testing

```powershell
python -m pytest -q
```

The suite covers the scanner, config, backtester, journal, orchestrator, and
each agent. All tests run **without an API key, without network, and without the
heavy agent dependencies** (crewai, langchain, vectorbt, etc.) — agents use
injectable/fake LLM clients and deterministic fallbacks.

For a dependency resolver check without installing:

```powershell
python -m pip install --dry-run --ignore-installed -r requirements.txt
```

## Dependency Notes

- Deterministic core and mockable-agent tests only need a lightweight subset
  (pandas, numpy, pydantic, sqlalchemy, yfinance, streamlit, plotly, pytest).
- Heavy dependencies (`crewai`, `langchain`, `langchain-anthropic`, `vectorbt`,
  `backtesting`, `apscheduler`, `psycopg2-binary`, `python-telegram-bot`) are
  required only for the full LLM-driven path and are imported **lazily**. Agent,
  backtest, and alert code stays importable and unit-testable without them.
- `nsepy` is pinned to `0.8` because `0.9.1` is not published for the tested
  Python 3.12 resolver path.
- `pyarrow` is included because `modules/ingest.py` writes parquet cache files.
- `pandas-ta` and `TA-Lib` are intentionally **not** part of the default
  install: `pandas-ta==0.3.14b` is no longer resolvable on PyPI, and TA-Lib
  requires native build support on Windows/Python 3.12. Reintroduce them only
  when code imports them and CI provides the native libraries.

## Project Status

The deterministic scanner spine (Waves 0–1) plus the multi-agent pipeline,
backtester, persistence, alerting, and dashboard (Waves 2–7) are implemented and
covered by the test suite. See `SESSION_STATE.md` for the detailed build log and
`PRD_v2_agent_task_breakdown.md` / `PRD_v2_code_gap_audit.md` for the full PRD
task breakdown and interface contracts.

> **Disclaimer:** StockScanner is a research and educational tool. Nothing it
> produces is financial advice. Validate any setup independently before trading.
