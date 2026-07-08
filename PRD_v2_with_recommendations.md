# Indian Stock Market Scanner

**Product Requirements Document (PRD) - Version 2.0**

**June 2026 - With Analysis, Recommendations & Multi-Agent Architecture**

---

## DOCUMENT CHANGE LOG

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | June 2026 | Initial PRD - 5-stage pipeline specification | Original |
| 2.0 | July 2026 | Added: Comprehensive analysis, competitive review, multi-agent architecture (MVP priority), critical gap analysis, technology recommendations | Senior Python Developer Review |

---

# EXECUTIVE SUMMARY (v2.0)

**Original Vision**: Rule-based EOD scanner with 5-stage funnel (fundamental → technical → volume → risk)

**Enhanced Vision**: **Multi-agent AI system** where specialized agents collaborate to surface high-probability trade setups, with each agent bringing domain expertise (scanning, research, risk validation, execution monitoring, continuous learning).

**Key Architectural Change**: Move from monolithic pipeline to **orchestrated agent system from MVP**, avoiding costly refactoring later.

**Why Multi-Agent from Start**:
- ✅ Natural separation of concerns (each agent = one expertise domain)
- ✅ Easier to test and validate individual components
- ✅ Scalable: add new agents (e.g., Options Agent) without touching core
- ✅ Each agent can use different models/tools optimally
- ✅ Built-in observability: see which agent's analysis influenced final decision

---

# 1. Product Overview (Enhanced)

This document defines requirements for an **intelligent, multi-agent stock market scanning system** that identifies high-probability trade setups in Indian equities. The system combines fundamental screening, technical pattern detection, volume profile analysis, and risk/reward gating through **specialized AI agents** that collaborate to surface actionable insights.

**Core Philosophy** (unchanged): Not about predicting winners with certainty — systematically surface setups where risk is clearly defined, reward potential is 2-3x that risk, and volume confirms genuine market interest.

**New Capability**: Agents provide **natural language reasoning** for why each setup is flagged, making the system educational and transparent (not a black box).

---

# 2. Objectives (Enhanced)

- Filter NSE/BSE-listed stocks through a multi-stage pipeline ending in a ranked shortlist of 5-10 actionable setups per day
- **[NEW]** Provide human-readable rationale for each setup via Research Agent analysis
- Automate the fundamental + technical + volume screening that would otherwise require hours of manual chart review
- **[NEW]** Continuously learn from trade outcomes via Learning Agent feedback loop
- Enforce position sizing and risk/reward discipline programmatically, removing emotional decision-making
- **[NEW]** Challenge each setup via adversarial Risk Agent before final approval
- Track trade outcomes over time to measure and tune system edge
- **[NEW]** Monitor live trades and alert on stop loss breaches via Execution Agent

---

# 3. Multi-Agent Architecture (MVP CORE)

## 3.1 Agent System Overview

The system uses **5 specialized agents** orchestrated via LangChain/CrewAI:

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent Orchestrator                        │
│                  (LangChain/CrewAI)                         │
└─────────────────────────────────────────────────────────────┘
         │              │              │              │
    ┌────▼────┐    ┌───▼───┐    ┌────▼────┐    ┌───▼───┐
    │ Scanner │    │Research│    │  Risk   │    │Execute│
    │  Agent  │───▶│ Agent  │───▶│  Agent  │───▶│ Agent │
    └─────────┘    └────────┘    └─────────┘    └───────┘
         │                                            │
         │                                            │
         └──────────────┐                ┌───────────┘
                        ▼                ▼
                   ┌────────────────────────┐
                   │    Learning Agent      │
                   │  (Outcome Analysis)    │
                   └────────────────────────┘
```

## 3.2 Agent Roles & Responsibilities

### **Agent 1: Scanner Agent** 🔍
**Purpose**: Run the 5-stage technical pipeline (fundamental → technical → volume → risk → ranking)

**Tools Available**:
- Data ingestion functions (yfinance, NSEpy)
- Technical indicator library (pandas-ta, TA-Lib)
- Volume profile calculator
- Pattern detection algorithms

**Input**: Full NSE/BSE universe (~5000 stocks)

**Output**: 
```python
{
  "candidates": [
    {
      "symbol": "TATAMOTORS.NS",
      "pattern": "consolidation_after_uptrend",
      "entry": 450.0,
      "stop": 430.0,
      "target": 500.0,
      "rr_ratio": 2.5,
      "volume_confirmed": True,
      "hvn_support": 425.0,
      "lvn_zones": [455.0, 480.0]
    }
  ],
  "total_scanned": 5000,
  "passed_fundamental": 247,
  "passed_technical": 38,
  "passed_volume": 18,
  "passed_rr_gate": 12
}
```

**Model**: Claude Haiku 4.5 (fast, cost-effective for rule execution)

---

### **Agent 2: Research Agent** 📊
**Purpose**: Deep-dive analysis on each shortlisted stock (news, financials, sector trends)

**Tools Available**:
- Web search (recent news via DuckDuckGo/SerpAPI)
- Screener.in API (latest financials, peer comparison)
- NSE announcements scraper
- Insider trading activity checker

**Input**: Scanner Agent's candidate list (12 stocks)

**Output**:
```python
{
  "symbol": "TATAMOTORS.NS",
  "research_summary": "Q1 results beat estimates by 15%. Launched new EV model. Promoter increased stake by 2% last month. Auto sector showing recovery post-monsoon. No major debt concerns.",
  "sentiment_score": 0.75,  # -1 to 1
  "red_flags": [],
  "recent_news": [
    {"date": "2026-07-05", "headline": "Tata Motors Q1 profit up 40% YoY", "source": "ET"},
    {"date": "2026-07-01", "headline": "JLR sales surge in Europe", "source": "Moneycontrol"}
  ],
  "confidence_score": 0.82  # How confident in this setup
}
```

**Model**: Claude Sonnet 4.5 (reasoning required for news synthesis)

**Runs**: In parallel for all candidates (12 concurrent research tasks)

---

### **Agent 3: Risk Agent** ⚠️
**Purpose**: Adversarially challenge each setup ("Why is R:R not higher?", "What could go wrong?")

**Tools Available**:
- Historical volatility analyzer
- Correlation checker (sector/market beta)
- Drawdown simulator
- Black swan event database (past similar setups that failed)

**Input**: Scanner output + Research Agent's analysis

**Output**:
```python
{
  "symbol": "TATAMOTORS.NS",
  "risk_assessment": {
    "approval_status": "APPROVED",  # APPROVED | REJECTED | CONDITIONAL
    "concerns": [
      "High beta (1.4) — will amplify market downside",
      "Sector correlation: 3 other auto stocks in shortlist — concentrated risk"
    ],
    "suggested_adjustments": {
      "position_size_multiplier": 0.7,  # Reduce from 1% to 0.7% risk
      "reason": "High volatility + sector concentration"
    },
    "stop_loss_validation": "PASS",  # Stop is below HVN — solid support
    "max_adverse_excursion": "12%",  # Historical worst-case before stop hit
    "confidence_after_challenge": 0.68  # Lower than Research Agent (adversarial)
  }
}
```

**Model**: Claude Sonnet 4.5 (critical reasoning + risk analysis)

**Key Behavior**: **Skeptical by design** — bias toward rejection unless setup is robust

---

### **Agent 4: Execution Agent** 🎯
**Purpose**: Monitor open trades, alert on stop/target breaches, suggest exits

**Tools Available**:
- Live price fetcher (yfinance real-time or NSE API)
- Trade journal database (SQLite/PostgreSQL)
- Telegram/Email/SMS alerting (Twilio, Apprise)

**Input**: Approved trades from Risk Agent

**Responsibilities**:
1. **Daily Monitoring**: Check if open positions hit stop or target
2. **Alerts**: Send Telegram message if stop breached
3. **Trailing Stop Management**: Move stop to breakeven after 1R profit
4. **Journaling**: Auto-update trade journal with actual outcomes

**Output**:
```python
{
  "symbol": "TATAMOTORS.NS",
  "status": "STOP_BREACHED",
  "entry_date": "2026-07-08",
  "entry_price": 450.0,
  "stop": 430.0,
  "exit_price": 428.0,  # Stopped out
  "pnl": -4400.0,  # (200 shares × -22)
  "pnl_percent": -4.8,
  "holding_period_days": 3,
  "alert_sent": True,
  "journal_updated": True
}
```

**Model**: Claude Haiku 4.5 (simple rule execution)

**Runs**: Daily at 3:30 PM IST + on-demand via cron every 30 mins during market hours

---

### **Agent 5: Learning Agent** 🧠
**Purpose**: Analyze closed trades, identify winning/losing patterns, tune thresholds

**Tools Available**:
- Trade journal database (all historical trades)
- Statistical analysis library (scipy, statsmodels)
- Backtesting framework (backtesting.py)
- Pattern performance tracker

**Input**: Trade journal (50+ closed trades minimum)

**Output**:
```python
{
  "analysis_period": "2026-Q2",
  "total_trades": 67,
  "win_rate": 0.58,  # 58%
  "avg_win": 8.2,  # %
  "avg_loss": -3.1,  # %
  "expectancy": 0.034,  # 3.4% positive expectancy
  "best_pattern": "consolidation_after_uptrend",  # 72% win rate
  "worst_pattern": "range_tightening",  # 42% win rate
  "recommendations": [
    "Increase min R:R for 'range_tightening' pattern from 2.5x to 3.5x",
    "Reduce position size for high beta stocks (>1.3) by 20%",
    "Avoid auto sector when Nifty Auto index is below 50-day SMA (5/7 losses)"
  ],
  "config_changes_proposed": {
    "MIN_RR": {"range_tightening": 3.5},  # Pattern-specific override
    "SECTOR_CORRELATION_LIMIT": 2  # Max 2 stocks from same sector
  }
}
```

**Model**: Claude Opus 4.8 (deep analytical reasoning)

**Runs**: Weekly on Sundays (batch analysis)

---

## 3.3 Agent Orchestration Flow (CrewAI)

```python
from crewai import Agent, Task, Crew

# Define agents
scanner = Agent(
    role="Technical Scanner",
    goal="Find stocks matching technical patterns with volume confirmation",
    backstory="Expert technical analyst with 10 years NSE experience",
    tools=[data_ingestion, pattern_detector, volume_profiler],
    llm=ChatAnthropic(model="claude-haiku-4-5")
)

researcher = Agent(
    role="Fundamental Researcher",
    goal="Validate technical setups with fundamental and sentiment analysis",
    backstory="CFA with expertise in Indian equity markets",
    tools=[web_search, screener_api, news_scraper],
    llm=ChatAnthropic(model="claude-sonnet-4-5")
)

risk_manager = Agent(
    role="Risk Validator",
    goal="Challenge every setup and identify what could go wrong",
    backstory="Former risk manager at hedge fund, skeptical by nature",
    tools=[volatility_analyzer, correlation_checker],
    llm=ChatAnthropic(model="claude-sonnet-4-5")
)

# Define tasks
scan_task = Task(
    description="Scan NSE universe for technical setups with R:R > 2.5x",
    agent=scanner,
    expected_output="List of 10-15 candidate stocks with entry/stop/target"
)

research_task = Task(
    description="For each candidate, research recent news, financials, sentiment",
    agent=researcher,
    expected_output="Enriched candidate list with research summary and confidence scores",
    context=[scan_task]  # Depends on scanner output
)

risk_task = Task(
    description="Adversarially validate each setup, approve/reject/adjust",
    agent=risk_manager,
    expected_output="Final approved list of 5-10 trades with position sizing",
    context=[scan_task, research_task]
)

# Create crew
crew = Crew(
    agents=[scanner, researcher, risk_manager],
    tasks=[scan_task, research_task, risk_task],
    verbose=True
)

# Execute
result = crew.kickoff()
```

---

## 3.4 Why This Architecture Beats Monolithic Pipeline

| **Aspect** | **Monolithic (v1.0)** | **Multi-Agent (v2.0)** |
|-----------|---------------------|---------------------|
| **Code Organization** | 5 modules, tight coupling | 5 agents, loose coupling |
| **Testing** | Integration tests only | Each agent testable independently |
| **Scalability** | Add feature = modify pipeline | Add agent without touching existing |
| **Explainability** | Black box (why did it pick this?) | Each agent's reasoning visible |
| **Model Optimization** | One model for everything | Right model per agent (Haiku for speed, Opus for deep analysis) |
| **Failure Isolation** | Pipeline breaks = system down | One agent fails, others continue |
| **Continuous Learning** | Manual threshold tuning | Learning Agent auto-tunes via feedback loop |
| **Human Oversight** | Review final output only | Inspect each agent's contribution |

---

# 4. Scope (Updated)

## 4.1 In Scope (MVP - v2.0)

✅ **Core Agents**:
- Scanner Agent (5-stage pipeline)
- Research Agent (news + fundamentals)
- Risk Agent (adversarial validation)
- Execution Agent (trade monitoring)
- Learning Agent (outcome analysis)

✅ **Infrastructure**:
- Agent orchestration (CrewAI or LangChain)
- Data pipeline (EOD OHLCV from NSE/BSE)
- Volume profile analysis (HVN/LVN)
- Backtesting framework (backtesting.py)
- Trade journal (PostgreSQL)
- Streamlit dashboard with agent reasoning display

✅ **Risk Management**:
- Position sizing (1% risk per trade)
- Portfolio heat limit (max 5% total risk)
- Sector diversification (max 2 stocks per sector)
- Market regime filter (Nifty 50 trend)

## 4.2 Out of Scope (Post-MVP)

❌ Intraday data or real-time scanning (future: add Real-Time Agent)
❌ Automated order execution or broker integration (future: add Execution API Agent)
❌ Options or derivatives screening (future: add Options Agent)
❌ Reinforcement learning-based optimization (future: upgrade Learning Agent)

---

# 5. Detailed Requirements (v1.0 - Unchanged)

## 5.1 Stage 1 — Data Ingestion
*(Same as v1.0 - see original PRD)*

## 5.2 Stage 2 — Fundamental Filter
*(Same as v1.0 - see original PRD)*

## 5.3 Stage 3 — Technical Pattern Detection
*(Same as v1.0 - see original PRD)*

## 5.4 Stage 4 — Volume Profile Analysis
*(Same as v1.0 - see original PRD)*

## 5.5 Stage 5 — Risk/Reward Gate and Position Sizing
*(Same as v1.0 - see original PRD)*

**[ENHANCEMENT]**: Add portfolio-level constraints:
- **MAX_CONCURRENT_POSITIONS**: 10 (never hold more than 10 stocks)
- **PORTFOLIO_HEAT_LIMIT**: 5% (sum of all position risks ≤ 5% of capital)
- **SECTOR_CORRELATION_LIMIT**: 2 (max 2 stocks from same sector)

---

# 6. Critical Gaps from v1.0 & Mitigations

## 6.1 Missing Capabilities (Now Fixed in v2.0)

| **Gap in v1.0** | **Impact** | **Fix in v2.0** |
|----------------|-----------|----------------|
| ❌ No backtesting | Could lose money before realizing system is unprofitable | ✅ Backtesting framework (backtesting.py) mandatory before live trading |
| ❌ No market regime awareness | Patterns fail in bear markets | ✅ Market regime filter (Nifty 50 trend) in Scanner Agent |
| ❌ Static pattern thresholds | 8% consolidation range may not fit all volatility regimes | ✅ Learning Agent tunes thresholds based on trade outcomes |
| ❌ No sentiment integration | Could buy stock day before bad news | ✅ Research Agent scrapes news and insider trading activity |
| ❌ No portfolio risk management | Could end up with 10 positions = 10% portfolio risk | ✅ Portfolio heat limit (5%) + sector diversification rules |
| ❌ Manual trade tracking | No automatic stop loss breach detection | ✅ Execution Agent monitors trades and alerts on stop breach |
| ❌ Limited pattern library (3 patterns) | Misses many valid setups | ✅ Expand to 8-10 patterns (v2.1 enhancement) |
| ❌ yfinance data unreliability | NSE data has gaps, splits not adjusted | ✅ Use NSEpy as primary, yfinance as fallback |
| ❌ No false breakout protection | Many breakouts fail within 2-3 days | ✅ Scanner Agent checks breakout sustainability (2-bar hold rule) |

## 6.2 New Capabilities in v2.0

✅ **Explainable AI**: Each agent provides reasoning (not a black box)
✅ **Adversarial Validation**: Risk Agent challenges every setup
✅ **Continuous Learning**: Learning Agent auto-tunes based on outcomes
✅ **Live Trade Monitoring**: Execution Agent tracks open positions
✅ **Natural Language Insights**: Research Agent writes human-readable summaries

---

# 7. Competitive Landscape Analysis

## 7.1 Direct Competitors (India-Focused)

| **Platform** | **Strengths** | **Pricing** | **Our Edge** |
|-------------|--------------|-------------|-------------|
| **ChartInk** | Real-time scanners, 100+ prebuilt scans | ₹999/mo | We have: Volume profile, multi-agent AI reasoning, free/self-hosted |
| **Streak (Zerodha)** | Algo trading, backtesting, broker integration | Free for Zerodha users | We have: HVN/LVN analysis, multi-agent validation, no broker lock-in |
| **StockEdge** | Fundamental + technical scans | ₹499/mo | We have: Agentic AI with explainability, custom pattern library |
| **Tickertape** | Stock screener, portfolio analytics | ₹999/mo | We have: Multi-agent system, continuous learning from outcomes |

## 7.2 International Competitors (Advanced)

| **Platform** | **Strengths** | **Why They're Better** | **Our Counter** |
|-------------|--------------|----------------------|----------------|
| **TradingView** | Global, 100+ indicators, Pine Script | Real-time, huge community | We have: AI agents, Indian market focus, volume profile |
| **TrendSpider** | AI pattern recognition, multi-timeframe | ML-based patterns, automated backtesting | We have: Multi-agent reasoning, portfolio risk management |
| **Trade Ideas** | Holly AI, real-time premarket scanning | Professional-grade, institutional data | We have: Free/open source, full customization, agentic architecture |

## 7.3 Competitive Positioning

**Unique Value Proposition**: 
> "The only **open-source multi-agent AI scanner** for Indian equities that explains *why* it picked each trade, learns from outcomes, and enforces institutional-grade risk management."

**Target Users**:
- Retail traders seeking systematic edge (not gut feel)
- Algo traders wanting customizable, explainable system
- Developers/quants who want to extend agent capabilities

---

# 8. Open Source Tools & Libraries

## 8.1 Core Dependencies

| **Category** | **Library** | **Purpose** | **Why Essential** |
|-------------|-----------|-----------|------------------|
| **Agent Framework** | CrewAI / LangGraph | Multi-agent orchestration | Core architecture |
| **LLM Integration** | LangChain + Anthropic SDK | Claude API integration | Agent reasoning |
| **Data Ingestion** | NSEpy, yfinance | NSE/BSE OHLCV data | Primary data source |
| **Technical Analysis** | TA-Lib, pandas-ta | Indicators (SMA, ATR, RSI) | Pattern detection |
| **Backtesting** | backtesting.py, vectorbt | Strategy validation | Risk-free testing before live |
| **Volume Profile** | market-profile (custom) | HVN/LVN calculation | Core scanner logic |
| **Risk Management** | PyPortfolioOpt | Portfolio optimization | Position sizing across setups |
| **Dashboard** | Streamlit | UI for agent outputs | User interface |
| **Database** | PostgreSQL (SQLAlchemy) | Trade journal, cache | Persistent storage |
| **Alerting** | Apprise, Twilio | Multi-channel alerts | Critical for Execution Agent |

## 8.2 Advanced Enhancement Libraries (Post-MVP)

| **Library** | **Use Case** | **Priority** |
|-----------|-----------|-----------|
| **stumpy** | Auto-discover recurring price patterns (motif detection) | Medium |
| **prophet (Meta)** | Forecast post-breakout trend direction | Low |
| **tsfresh** | Auto-extract 100+ time series features for ML | Medium |
| **Stable-Baselines3** | RL-based Learning Agent (advanced) | Low (v3.0) |
| **Arctic (Man Group)** | High-performance time series DB | Medium (if adding intraday) |
| **Airflow** | Pipeline orchestration (replace cron) | Low |

---

# 9. Technology Stack (Updated for v2.0)

| **Component** | **Library/Tool** | **Notes** |
|--------------|----------------|---------|
| **Agent Orchestration** | **CrewAI** | Multi-agent framework (simpler than LangGraph for MVP) |
| **LLM** | **Claude 4.x via Anthropic SDK** | Haiku (Scanner/Execution), Sonnet (Research/Risk), Opus (Learning) |
| **Data Fetch** | **NSEpy** (primary), yfinance (fallback) | NSEpy is more reliable for NSE data |
| **Fundamental Data** | Screener.in API or CSV | Quarterly refresh |
| **Data Processing** | pandas, numpy | Core pipeline logic |
| **Technical Indicators** | **TA-Lib** (fast), pandas-ta (fallback) | Pattern detection |
| **Volume Profile** | Custom pandas logic | HVN/LVN bin calculation |
| **Backtesting** | **backtesting.py** | Validate before live trading |
| **Database** | **PostgreSQL** (prod), SQLite (dev) | Trade journal, OHLCV cache |
| **Dashboard UI** | Streamlit | Multi-tab: Scanner output, Agent reasoning, Trade journal |
| **Alerts** | **Apprise** (multi-channel), Telegram | Execution Agent notifications |
| **Scheduling** | APScheduler | Daily post-market run (3:30 PM IST) |
| **Configuration** | Pydantic (type-safe config) | Replaces raw dicts |
| **Logging** | structlog | Structured, queryable logs |
| **Testing** | pytest, pytest-mock | Each agent tested independently |

---

# 10. Project Structure (v2.0 - Agent-Based)

```
stonx/
├── agents/
│   ├── __init__.py
│   ├── base.py               # BaseAgent class
│   ├── scanner_agent.py      # Agent 1: Technical scanning
│   ├── research_agent.py     # Agent 2: News & fundamentals
│   ├── risk_agent.py         # Agent 3: Adversarial validation
│   ├── execution_agent.py    # Agent 4: Trade monitoring
│   └── learning_agent.py     # Agent 5: Outcome analysis
│
├── modules/                   # Core logic (used by agents as tools)
│   ├── __init__.py
│   ├── ingest.py             # Data fetch & cache
│   ├── fundamental.py        # Fundamental filter
│   ├── patterns.py           # Technical pattern detection
│   ├── volume.py             # Volume profile HVN/LVN
│   ├── risk.py               # R:R gate & position sizing
│   └── backtest.py           # Backtesting framework
│
├── tools/                     # LangChain tools for agents
│   ├── __init__.py
│   ├── data_tools.py         # yfinance, NSEpy wrappers
│   ├── web_tools.py          # News scraper, Screener.in API
│   ├── analysis_tools.py     # Volatility, correlation, stats
│   └── alert_tools.py        # Telegram, email, SMS
│
├── orchestrator/
│   ├── __init__.py
│   └── crew.py               # CrewAI orchestration logic
│
├── data/
│   ├── cache/                # OHLCV cache (Parquet files)
│   ├── fundamentals/         # Screener.in exports
│   └── models/               # SQLAlchemy ORM models
│
├── app.py                    # Streamlit dashboard (multi-tab)
├── run_scanner.py            # Daily orchestrator entry point
├── config.py                 # Pydantic config (capital, risk%, thresholds)
├── requirements.txt
├── tests/
│   ├── test_agents.py        # Each agent's unit tests
│   ├── test_modules.py       # Core logic tests
│   └── test_integration.py   # End-to-end crew execution
│
├── notebooks/
│   └── backtest_analysis.ipynb  # Jupyter: test patterns on historical data
│
├── logs/                     # structlog output
└── README.md
```

---

# 11. Build Phases (v2.0 - Agent-First)

| **Phase** | **Deliverable** | **Est. Time** | **Dependencies** |
|----------|----------------|-------------|----------------|
| **0** | Project setup: virtualenv, dependencies, config.py | 0.5 day | None |
| **1** | Data pipeline (modules/ingest.py) — fetch, cache, validate OHLCV | 1 day | NSEpy, yfinance |
| **2** | Fundamental filter (modules/fundamental.py) | 0.5 day | Screener.in CSV |
| **3** | Pattern detection (modules/patterns.py) — 3 patterns to start | 2 days | TA-Lib, pandas-ta |
| **4** | Volume profile (modules/volume.py) — HVN/LVN calculation | 1 day | Custom pandas logic |
| **5** | Risk module (modules/risk.py) — R:R gate, position sizing | 1 day | None |
| **6** | **Scanner Agent** (agents/scanner_agent.py) — orchestrates stages 1-5 | 1 day | CrewAI, modules 1-5 |
| **7** | **Research Agent** (agents/research_agent.py) — news + fundamentals | 1.5 days | Web scraping, Screener.in API |
| **8** | **Risk Agent** (agents/risk_agent.py) — adversarial validation | 1 day | Volatility analysis tools |
| **9** | **Execution Agent** (agents/execution_agent.py) — trade monitoring | 1 day | Apprise, Telegram bot |
| **10** | **Learning Agent** (agents/learning_agent.py) — outcome analysis | 1.5 days | Trade journal DB, scipy |
| **11** | Crew orchestration (orchestrator/crew.py) — wire all agents | 1 day | CrewAI |
| **12** | Streamlit dashboard (app.py) — multi-tab UI | 2 days | Streamlit |
| **13** | Backtesting framework (modules/backtest.py) | 1.5 days | backtesting.py |
| **14** | Testing + tuning (pytest suite, integration tests) | 2 days | All modules |
| **15** | Documentation (README, agent docs, API reference) | 1 day | None |

**Total Estimated Time**: **18-20 days** (3.5-4 weeks of focused development)

---

# 12. Configuration Parameters (config.py - Enhanced)

```python
from pydantic import BaseSettings, Field

class ScannerConfig(BaseSettings):
    """Type-safe configuration using Pydantic"""
    
    # Capital & Risk
    CAPITAL: int = Field(1_000_000, description="Total trading capital (₹)")
    RISK_PCT: float = Field(0.01, description="Max % of capital to risk per trade")
    MIN_RR: float = Field(2.5, description="Minimum risk/reward ratio")
    PORTFOLIO_HEAT_LIMIT: float = Field(0.05, description="Max total portfolio risk (5%)")
    MAX_CONCURRENT_POSITIONS: int = Field(10, description="Max open positions")
    
    # Fundamental Filters
    MIN_MARKET_CAP_CR: int = Field(500, description="Min market cap (₹ Crores)")
    MIN_REVENUE_GROWTH: float = Field(0.0, description="Min YoY revenue growth")
    MAX_DEBT_TO_EQUITY: float = Field(1.0, description="Max debt-to-equity ratio")
    MIN_PROMOTER_HOLDING: float = Field(0.40, description="Min promoter holding %")
    
    # Technical Parameters
    VOLUME_LOOKBACK_DAYS: int = Field(100, description="Days of OHLCV for volume profile")
    CONSOLIDATION_DAYS: int = Field(20, description="Lookback for consolidation check")
    CONSOLIDATION_RANGE_PCT: float = Field(0.08, description="Max % range for consolidation")
    VOLUME_SPIKE_MULTIPLIER: float = Field(1.5, description="Min volume vs 20-day avg")
    
    # Volume Profile
    HVN_THRESHOLD: float = Field(1.5, description="Volume multiple for HVN")
    LVN_THRESHOLD: float = Field(0.5, description="Volume fraction for LVN")
    
    # Market Regime (NEW)
    NIFTY_SMA_PERIOD: int = Field(200, description="Nifty 50 SMA for trend filter")
    BULL_MARKET_MIN_RR: float = Field(2.5, description="Min R:R in bull market")
    BEAR_MARKET_MIN_RR: float = Field(3.5, description="Min R:R in bear market")
    
    # Sector Diversification (NEW)
    SECTOR_CORRELATION_LIMIT: int = Field(2, description="Max stocks from same sector")
    
    # Agent Configuration (NEW)
    SCANNER_AGENT_MODEL: str = Field("claude-haiku-4-5", description="Model for Scanner Agent")
    RESEARCH_AGENT_MODEL: str = Field("claude-sonnet-4-5", description="Model for Research Agent")
    RISK_AGENT_MODEL: str = Field("claude-sonnet-4-5", description="Model for Risk Agent")
    EXECUTION_AGENT_MODEL: str = Field("claude-haiku-4-5", description="Model for Execution Agent")
    LEARNING_AGENT_MODEL: str = Field("claude-opus-4-8", description="Model for Learning Agent")
    
    # API Keys
    ANTHROPIC_API_KEY: str = Field(..., env="ANTHROPIC_API_KEY")
    TELEGRAM_BOT_TOKEN: str = Field("", env="TELEGRAM_BOT_TOKEN")
    
    class Config:
        env_file = ".env"
```

---

# 13. Measuring System Edge (Enhanced)

## 13.1 Metrics to Track

After accumulating **50+ closed trades**, calculate:

**Traditional Metrics**:
- Win rate: % of trades that hit target before stop
- Average win / average loss ratio
- Expectancy = (Win rate × Avg win) - (Loss rate × Avg loss)
- Sharpe ratio, Sortino ratio, max drawdown

**Pattern-Level Performance**:
- Best-performing pattern (consolidation vs higher lows vs compression)
- Pattern-specific win rates
- Average holding period per pattern

**Agent-Level Performance (NEW)**:
- Research Agent sentiment score correlation with outcomes (did high-confidence setups actually win more?)
- Risk Agent rejection accuracy (did rejected setups actually fail more often?)
- Learning Agent recommendation effectiveness (did threshold changes improve edge?)

## 13.2 Learning Agent Feedback Loop

```python
# Example Learning Agent output after 100 trades:
{
  "recommendations": [
    {
      "finding": "Consolidation pattern has 72% win rate (best performer)",
      "action": "INCREASE_ALLOCATION",
      "config_change": {"CONSOLIDATION_PRIORITY_MULTIPLIER": 1.3}
    },
    {
      "finding": "Range tightening has 42% win rate (worst performer)",
      "action": "INCREASE_RR_THRESHOLD",
      "config_change": {"MIN_RR": {"range_tightening": 3.5}}
    },
    {
      "finding": "Auto sector: 5/7 losses when Nifty Auto < 50-day SMA",
      "action": "ADD_SECTOR_FILTER",
      "config_change": {"SECTOR_TREND_FILTER": {"AUTO": "nifty_auto_above_sma50"}}
    }
  ],
  "auto_apply": False,  # Requires human approval before changing config
  "backtest_validation": {
    "before_changes": {"sharpe": 1.2, "expectancy": 0.034},
    "after_changes": {"sharpe": 1.6, "expectancy": 0.051}
  }
}
```

**Human-in-the-Loop**: Learning Agent proposes changes → Backtests on historical data → Human approves → Config updated

---

# 14. Key Risks and Mitigations (v2.0)

| **Risk** | **Impact** | **Mitigation (v2.0)** |
|---------|-----------|---------------------|
| NSE data gaps / rate limits | Pipeline failures | ✅ NSEpy primary, yfinance fallback, aggressive caching |
| False positives from rule-based patterns | Noisy shortlist | ✅ Require pattern + volume + Research Agent confirmation |
| Operator-driven moves mimicking patterns | Bad entries | ✅ Fundamental filter + Risk Agent challenge |
| Fundamental data staleness (quarterly) | Screening on outdated data | ✅ Research Agent fetches latest news, flags old financials |
| Over-optimizing thresholds on limited history | Curve fitting | ✅ Learning Agent requires 50+ trades + backtest validation |
| LLM hallucination in Research Agent | Wrong analysis | ✅ Require citations (news links), skeptical Risk Agent validates |
| API costs (Claude API) | Budget overrun | ✅ Use Haiku for high-frequency agents, Opus only for Learning Agent |
| Agent reasoning bottleneck | Slow scanner (can't run in real-time) | ✅ Acceptable for EOD scanner, optimize later with caching |

---

# 15. Backtest-First Development (NEW - Critical)

## 15.1 Why Backtest Before Live Trading

**Problem**: v1.0 PRD says "measure edge after 50+ live trades" → you could lose significant capital before realizing system is unprofitable.

**Solution**: Backtest on 2020-2025 historical data (5 years) before going live.

## 15.2 Backtesting Framework

```python
# modules/backtest.py

from backtesting import Backtest, Strategy
from backtesting.lib import crossover
import pandas as pd

class MultiAgentStrategy(Strategy):
    """
    Backtesting strategy that simulates Scanner Agent pattern detection
    and Risk Agent position sizing
    """
    
    # Parameters (will be optimized via grid search)
    consolidation_range_pct = 0.08
    min_rr = 2.5
    volume_spike_multiplier = 1.5
    
    def init(self):
        # Pre-calculate indicators
        self.sma50 = self.I(lambda x: pd.Series(x).rolling(50).mean(), self.data.Close)
    
    def next(self):
        # Check if pattern detected (simplified for backtest)
        if self._is_consolidation_breakout():
            # Calculate stop and target (from volume profile)
            stop = self._find_hvn_support()
            target = self._find_lvn_target()
            rr_ratio = (target - self.data.Close[-1]) / (self.data.Close[-1] - stop)
            
            if rr_ratio >= self.min_rr:
                # Position sizing: 1% risk
                risk_per_trade = 0.01 * self.equity
                position_size = risk_per_trade / (self.data.Close[-1] - stop)
                
                self.buy(size=position_size, sl=stop, tp=target)
    
    def _is_consolidation_breakout(self):
        # Simplified pattern detection logic
        # (Real implementation would match Scanner Agent exactly)
        pass

# Run backtest
def run_backtest(symbol, start_date, end_date):
    data = load_historical_data(symbol, start_date, end_date)
    
    bt = Backtest(data, MultiAgentStrategy, cash=1_000_000, commission=0.001)
    stats = bt.run()
    
    print(stats)
    bt.plot()
    
    return stats

# Optimize parameters
def optimize_thresholds():
    bt = Backtest(data, MultiAgentStrategy, cash=1_000_000)
    
    stats = bt.optimize(
        consolidation_range_pct=[0.06, 0.08, 0.10],
        min_rr=[2.0, 2.5, 3.0, 3.5],
        volume_spike_multiplier=[1.3, 1.5, 1.8],
        maximize='Sharpe Ratio',  # Or 'Expectancy'
        constraint=lambda p: p.Win_Rate >= 0.50  # Must have 50%+ win rate
    )
    
    print(stats)
    return stats._strategy
```

## 15.3 Backtest Validation Checklist

Before going live, backtest must show:
- ✅ **Positive expectancy** (avg win × win rate > avg loss × loss rate)
- ✅ **Win rate ≥ 50%** (for 2.5x R:R, need >40% to break even)
- ✅ **Sharpe ratio > 1.0** (preferably > 1.5)
- ✅ **Max drawdown < 20%** (if you can't stomach 20% drawdown, reduce position size)
- ✅ **Minimum 200 trades** in backtest period (statistical significance)
- ✅ **Walk-forward testing** (train on 2020-2023, test on 2024-2025 — avoid overfitting)

**If backtest fails any of these → do NOT go live → tune patterns/thresholds → re-backtest**

---

# 16. Dashboard Specification (v2.0 - Multi-Tab)

## 16.1 Tab 1: Scanner Output

**Table Columns**:
- Symbol (with TradingView link)
- Pattern(s) Triggered
- Entry Zone
- Stop Loss
- Target
- R:R Ratio (color: green >3x, amber 2.5-3x)
- Position Size (shares + ₹ amount)
- Volume Confirmed (✅/❌ badge)
- Sector
- Research Agent Confidence (0-100%)
- Risk Agent Status (APPROVED / CONDITIONAL / REJECTED)

**Filters**:
- Pattern type (consolidation / higher lows / compression)
- Min R:R ratio (slider: 2.0 - 5.0)
- Sector (multi-select)
- Volume confirmed only (toggle)
- Risk Agent approved only (toggle)

## 16.2 Tab 2: Agent Reasoning (Explainability)

For each stock in shortlist, show:

**Scanner Agent Output**:
```
Pattern: Consolidation after uptrend
- Prior 60-day gain: +28%
- Consolidation range: 6.2% (last 20 bars)
- Price vs 50-day SMA: +4.5% (bullish)
- Volume profile: HVN at ₹425, LVN zones at ₹455-₹480
```

**Research Agent Output**:
```
Recent News:
- [2026-07-05] Q1 profit up 40% YoY (ET)
- [2026-07-01] JLR sales surge in Europe (Moneycontrol)

Fundamentals:
- Market cap: ₹2,400 Cr
- Debt/Equity: 0.65
- Promoter holding: 58%
- PAT (TTM): ₹120 Cr

Sentiment Score: 0.75 / 1.0 (Bullish)
Confidence: 82%
```

**Risk Agent Output**:
```
Concerns:
- High beta (1.4) — will amplify market downside
- Sector correlation: 2 other auto stocks in shortlist

Adjustments:
- Position size reduced to 0.7% risk (from 1%)
- Stop loss validated: Below HVN at ₹425 (solid support)

Final Verdict: APPROVED (with reduced position size)
```

## 16.3 Tab 3: Trade Journal

**Columns**:
- Date Entered
- Symbol
- Pattern
- Entry Price
- Stop Loss
- Target
- R:R Ratio
- Position Size
- Status (Open / Closed)
- Exit Price
- P&L (₹ + %)
- Holding Period (days)
- Outcome (Hit Target / Stopped Out / Manual Exit)

**Filters**:
- Date range
- Pattern type
- Status (open / closed)
- Outcome (winners / losers)

**Summary Stats** (below table):
- Total trades: 67
- Win rate: 58%
- Avg win: 8.2%
- Avg loss: -3.1%
- Expectancy: +3.4%
- Sharpe ratio: 1.6

## 16.4 Tab 4: Learning Agent Insights

**Display**:
- Last analysis date
- Pattern performance comparison (bar chart: win rate by pattern)
- Recommendations from Learning Agent
- Proposed config changes (with approve/reject buttons)
- Backtest validation results (before/after proposed changes)

---

# 17. Telegram Alert Format

**Daily Alert (4:00 PM IST)** — sent by Execution Agent:

```
🔍 StockScanner Daily Alert
Date: 08-Jul-2026

Top 5 Setups:

1. TATAMOTORS.NS 🟢
   Pattern: Consolidation breakout
   Entry: ₹450 | Stop: ₹430 | Target: ₹500
   R:R: 2.5x | Size: 200 shares
   Research: Q1 beat, EV launch (82% confidence)

2. BHARTIARTL.NS 🟢
   Pattern: Higher lows
   Entry: ₹820 | Stop: ₹790 | Target: ₹900
   R:R: 2.7x | Size: 150 shares
   Research: 5G rollout ahead of schedule (78% confidence)

[... 3 more setups ...]

💡 Market Regime: BULLISH (Nifty above 200-day SMA)
⚠️ Portfolio Heat: 3.2% / 5.0% (safe to add positions)

📊 View full analysis: http://localhost:8501
```

**Stop Loss Breach Alert** — sent immediately:

```
🚨 STOP LOSS BREACH

Symbol: TATAMOTORS.NS
Entry: ₹450 (05-Jul-2026)
Stop: ₹430
Exit: ₹428 (stopped out)

P&L: -₹4,400 (-4.8%)
Holding period: 3 days

Status: Trade closed automatically
Journal updated: ✅
```

---

# 18. Development Priorities (What to Build First)

## 18.1 MVP Phase 1 (Weeks 1-2): Core Pipeline Without Agents

**Goal**: Get end-to-end pipeline working (data → patterns → output) before adding agent layer.

**Tasks**:
1. ✅ Data ingestion (modules/ingest.py) — fetch NSE OHLCV
2. ✅ Fundamental filter (modules/fundamental.py)
3. ✅ Pattern detection (modules/patterns.py) — implement 3 patterns
4. ✅ Volume profile (modules/volume.py) — HVN/LVN calculation
5. ✅ Risk module (modules/risk.py) — R:R gate + position sizing
6. ✅ Backtest framework (modules/backtest.py) — validate on 2020-2025 data
7. ✅ Simple script (run_scanner.py) that runs all modules sequentially

**Output**: CSV file with 5-10 candidate stocks (no agents yet, no dashboard)

**Validation**: Backtest shows positive expectancy, Sharpe > 1.0

---

## 18.2 MVP Phase 2 (Weeks 3-4): Add Agent Layer

**Goal**: Wrap core pipeline with agents, add Research & Risk validation.

**Tasks**:
1. ✅ Scanner Agent (agents/scanner_agent.py) — wraps Phase 1 pipeline
2. ✅ Research Agent (agents/research_agent.py) — web scraping + news
3. ✅ Risk Agent (agents/risk_agent.py) — adversarial validation
4. ✅ Execution Agent (agents/execution_agent.py) — trade monitoring
5. ✅ Crew orchestration (orchestrator/crew.py) — wire 4 agents
6. ✅ Streamlit dashboard (app.py) — Tabs 1-3 (Scanner output, reasoning, journal)
7. ✅ Telegram bot (tools/alert_tools.py) — daily alerts + stop breach alerts

**Output**: Full multi-agent system with explainable reasoning

**Validation**: Run on last 10 trading days, manually verify agent outputs make sense

---

## 18.3 Post-MVP (Month 2+): Learning Agent & Enhancements

**Tasks**:
1. ✅ Learning Agent (agents/learning_agent.py) — outcome analysis
2. ✅ Dashboard Tab 4 (Learning Agent insights)
3. ✅ Expand pattern library (add 5 more patterns)
4. ✅ Add sentiment analysis (Twitter/Reddit scraping)
5. ✅ Add options screening (separate Options Agent)
6. ✅ Real-time scanning (intraday data + WebSocket feeds)
7. ✅ Broker integration (automated order execution)

---

# 19. Success Criteria (How to Know If This Works)

## 19.1 Technical Success Metrics

**Before Going Live** (backtesting on 2020-2025 data):
- ✅ Positive expectancy (>2%)
- ✅ Sharpe ratio > 1.5
- ✅ Win rate ≥ 50%
- ✅ Max drawdown < 20%
- ✅ Minimum 200 trades in backtest

**After 50 Live Trades**:
- ✅ Live performance matches backtest (±20%)
- ✅ Win rate still ≥ 50%
- ✅ No pattern has <40% win rate (drop underperformers)
- ✅ Average holding period < 10 days (this is a swing trading system, not buy-and-hold)

## 19.2 Agent Performance Metrics

**Research Agent**:
- High-confidence setups (>80%) should win ≥65% of the time
- Low-confidence setups (<50%) should be rejected by Risk Agent

**Risk Agent**:
- Rejected setups should fail ≥60% of the time (validates skepticism)
- Approved setups should win ≥55% of the time

**Learning Agent**:
- Proposed config changes should improve backtest Sharpe by ≥10%
- Threshold recommendations should be validated on out-of-sample data

## 19.3 User Experience Metrics

- Dashboard loads in <5 seconds (scanner output)
- Agent reasoning is understandable to non-technical users
- Telegram alerts arrive within 5 minutes of market close
- Stop loss breach alerts are instant (<1 minute)

---

# 20. Next Steps: Implementation Roadmap

## Week 1: Foundation
- [ ] Project setup (virtualenv, dependencies, folder structure)
- [ ] Config.py with Pydantic (type-safe configuration)
- [ ] Data ingestion module (NSEpy + yfinance fallback)
- [ ] PostgreSQL database setup (trade journal schema)
- [ ] Logging infrastructure (structlog)

## Week 2: Core Logic
- [ ] Fundamental filter module
- [ ] Technical pattern detection (3 patterns)
- [ ] Volume profile calculation (HVN/LVN)
- [ ] Risk module (R:R gate + position sizing)
- [ ] Backtesting framework setup

## Week 3: Agent Development
- [ ] Scanner Agent (wraps Week 2 modules)
- [ ] Research Agent (web scraping tools)
- [ ] Risk Agent (adversarial validation)
- [ ] Execution Agent (trade monitoring)
- [ ] CrewAI orchestration

## Week 4: UI & Integration
- [ ] Streamlit dashboard (Tabs 1-3)
- [ ] Telegram bot integration
- [ ] End-to-end testing
- [ ] Documentation (README, agent docs)

## Week 5+: Enhancement
- [ ] Learning Agent
- [ ] Expand pattern library
- [ ] Sentiment analysis
- [ ] Production deployment (cloud hosting, scheduling)

---

# 21. Conclusion

**Version 2.0 Summary**:

We've transformed the original rule-based scanner (v1.0) into a **multi-agent AI system** that:

1. ✅ **Separates concerns** — each agent has one job, does it well
2. ✅ **Provides explainability** — see why each stock was picked
3. ✅ **Learns from outcomes** — Learning Agent auto-tunes thresholds
4. ✅ **Enforces discipline** — Risk Agent challenges every setup
5. ✅ **Monitors live trades** — Execution Agent alerts on stop breaches
6. ✅ **Scales gracefully** — add new agents without touching core logic

**Key Advantages Over v1.0**:
- 🚀 **Agentic architecture from MVP** (no costly refactor later)
- 🧠 **Natural language reasoning** (not a black box)
- 🔄 **Continuous learning** (adapts to changing markets)
- 🛡️ **Adversarial validation** (catches weak setups before you trade them)
- 📊 **Portfolio risk management** (heat limits, sector diversification)

**Build Time**: 3-4 weeks for full MVP (vs 2 weeks for v1.0, but **10x more capable**)

**Next Action**: Start with **Week 1 Foundation** — set up project structure, dependencies, and data pipeline. Once data flows cleanly, build agents on top.

---

_END OF PRD v2.0_

**Questions or Ready to Start Building?** 🚀

Let's create the most sophisticated open-source stock scanner for Indian markets, with institutional-grade risk management and cutting-edge multi-agent AI architecture.
