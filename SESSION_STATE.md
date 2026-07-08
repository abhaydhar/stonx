# StockScanner Build Session State

**Last Updated**: 2026-07-08  
**Build Phase**: MVP Phase 1 - Core Pipeline Foundation  
**Progress**: 30% (Day 1 of estimated 18-20 day build)

---

## 🎯 Current Objective

Building **Scanner Agent** (proof of concept) + **CrewAI orchestration template** to demonstrate multi-agent architecture working end-to-end.

---

## ✅ Completed Work

### 1. Requirements & Architecture (100% Complete)
- [PRD_v2_with_recommendations.md](PRD_v2_with_recommendations.md) - Comprehensive PRD with multi-agent architecture
- **Key Decision**: Multi-agent architecture moved to MVP (not post-MVP) to avoid costly refactor
- **Architecture**: 5 agents (Scanner, Research, Risk, Execution, Learning) orchestrated via CrewAI

### 2. Project Setup (80% Complete)

#### Files Created:
- [requirements.txt](requirements.txt) - All dependencies (CrewAI, LangChain, pandas, yfinance, etc.)
- [config.py](config.py) - Pydantic-based type-safe configuration
- [modules/__init__.py](modules/__init__.py) - Module initialization
- [modules/ingest.py](modules/ingest.py) - Data ingestion with caching

#### Folder Structure:
```
stonx/
├── agents/           [TO CREATE]
├── modules/          [PARTIAL - has __init__.py, ingest.py]
├── tools/            [TO CREATE]
├── orchestrator/     [TO CREATE]
├── data/
│   ├── cache/        [TO CREATE]
│   └── fundamentals/ [TO CREATE]
├── logs/             [TO CREATE]
└── tests/            [TO CREATE]
```

### 3. Configuration (100% Complete)
- **Capital Management**: ₹10L default, 1% risk per trade, 5% portfolio heat limit
- **Technical Parameters**: 100-day lookback, 8% consolidation range, 1.5x volume spike
- **Agent Models**: Haiku (Scanner/Execution), Sonnet (Research/Risk), Opus (Learning)
- **API Keys**: Configured via .env file (ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN)

### 4. Data Ingestion Module (100% Complete)
**File**: [modules/ingest.py](modules/ingest.py)
- ✅ Fetches OHLCV data via yfinance
- ✅ Caches data as Parquet files (1-day TTL)
- ✅ Handles missing data (skips if >10% gaps)
- ✅ NSE universe: 30 liquid stocks for MVP (expandable)
- ✅ Placeholder for fundamental data (Screener.in integration pending)

---

## 🚧 In Progress

### Current Task: Building Core Modules for Scanner Agent

**Next 4 modules to create**:
1. **modules/patterns.py** - Technical pattern detection (consolidation, higher lows, compression)
2. **modules/volume.py** - Volume profile analysis (HVN/LVN calculation)
3. **modules/risk.py** - R:R gate + position sizing
4. **modules/fundamental.py** - Fundamental filters (market cap, debt, promoter holding)

---

## 📋 Remaining Tasks (In Order)

### Phase 1: Core Modules (Estimated: 3 days)
- [ ] Create `modules/fundamental.py` - Fundamental screening logic
- [ ] Create `modules/patterns.py` - 3 pattern detectors (consolidation, higher lows, compression)
- [ ] Create `modules/volume.py` - Volume profile HVN/LVN calculator
- [ ] Create `modules/risk.py` - R:R gate + position sizing

### Phase 2: LangChain Tools (Estimated: 1 day)
- [ ] Create `tools/__init__.py`
- [ ] Create `tools/data_tools.py` - Wrap modules as LangChain tools
- [ ] Create `tools/analysis_tools.py` - Volatility, correlation tools

### Phase 3: Scanner Agent (Estimated: 1 day)
- [ ] Create `agents/__init__.py`
- [ ] Create `agents/base.py` - BaseAgent class
- [ ] Create `agents/scanner_agent.py` - Main Scanner Agent implementation

### Phase 4: CrewAI Orchestration (Estimated: 1 day)
- [ ] Create `orchestrator/__init__.py`
- [ ] Create `orchestrator/crew.py` - CrewAI setup with Scanner Agent
- [ ] Create `run_scanner.py` - Entry point script

### Phase 5: Testing & Validation (Estimated: 1 day)
- [ ] Test data ingestion on 30 NSE stocks
- [ ] Test pattern detection on sample data
- [ ] Test end-to-end Scanner Agent execution
- [ ] Create `tests/test_scanner_agent.py`

---

## 🏗️ Architecture Decisions Log

### Decision 1: Multi-Agent from MVP (Priority Change)
**Date**: 2026-07-08  
**Rationale**: Avoid costly refactor later. Agent-based architecture provides:
- Natural separation of concerns
- Individual agent testability
- Scalability (add agents without touching core)
- Explainability (each agent's reasoning visible)

### Decision 2: CrewAI over LangGraph
**Date**: 2026-07-08  
**Rationale**: CrewAI simpler for MVP, provides:
- Higher-level abstractions (Agent, Task, Crew)
- Built-in task dependencies
- Less boilerplate than LangGraph
- Can migrate to LangGraph later if needed

### Decision 3: Parquet for Caching (not SQLite)
**Date**: 2026-07-08  
**Rationale**: 
- Faster read/write for time-series data
- Better compression
- Pandas-native format
- SQLite reserved for trade journal (transactional data)

### Decision 4: Start with 30 Liquid Stocks (not 5000)
**Date**: 2026-07-08  
**Rationale**: 
- MVP validation faster with smaller universe
- 30 liquid stocks = ~90% of NSE trading volume
- Easily expandable to full universe later
- Reduces API rate limit issues

---

## 🔑 Key Technical Details

### Data Flow (Current Implementation)
```
DataIngestion.fetch_ohlcv(symbol)
  ↓
1. Check cache (Parquet file < 1 day old)
  ↓ (cache miss)
2. Fetch from yfinance
  ↓
3. Validate (skip if >10% missing data)
  ↓
4. Save to cache (Parquet format)
  ↓
5. Return DataFrame
```

### Pattern Detection Logic (To Implement)
```
For each stock:
  1. Check fundamental filters (market cap, debt, promoter holding)
  2. Detect technical patterns:
     - Consolidation after uptrend
     - Higher lows formation
     - Range tightening (compression)
  3. Calculate volume profile (HVN/LVN)
  4. Validate R:R ratio (>2.5x)
  5. Calculate position size (1% risk)
```

### Agent Orchestration Flow (To Implement)
```
CrewAI Orchestrator
  ↓
Scanner Agent
  ├─ Tool: fetch_ohlcv_data
  ├─ Tool: detect_patterns
  ├─ Tool: calculate_volume_profile
  └─ Tool: validate_risk_reward
  ↓
Output: List[dict] with 5-10 trade setups
```

---

## 📦 Dependencies Status

### Installed (via requirements.txt):
- ✅ pydantic + pydantic-settings (config management)
- ✅ pandas + numpy (data processing)
- ✅ yfinance (NSE data)
- ✅ crewai + langchain + anthropic (agent framework)
- ⏳ pandas-ta / TA-Lib (technical indicators) - not yet used
- ⏳ streamlit (dashboard) - not yet used
- ⏳ backtesting.py (validation) - not yet used

### Environment Variables Required:
```bash
# .env file
ANTHROPIC_API_KEY=sk-ant-...          # Required
TELEGRAM_BOT_TOKEN=123456:ABC...      # Optional
TELEGRAM_CHAT_ID=123456789            # Optional
DATABASE_URL=sqlite:///./data/stonx.db  # Has default
```

---

## 🚨 Known Issues & Blockers

### Issue 1: Shell Not Available
**Problem**: Windows environment, no Posix shell (Bash tool unavailable)  
**Impact**: Cannot use bash commands for folder creation  
**Workaround**: Using Write tool to create files directly (creates parent dirs automatically)  
**Status**: RESOLVED

### Issue 2: NSE Data Quality (yfinance)
**Problem**: yfinance for NSE data has gaps, corporate actions not always adjusted  
**Impact**: May need fallback data source  
**Mitigation**: 
- Implemented 10% missing data threshold (skip stocks)
- TODO: Add NSEpy as fallback (later phase)
**Status**: MONITORING

### Issue 3: Fundamental Data Source
**Problem**: No Screener.in API integration yet  
**Impact**: Fundamental filters using placeholder data  
**Mitigation**: Using mock data for MVP, will integrate Screener.in API or CSV export later  
**Status**: DEFERRED (Post-MVP)

---

## 🎓 Context for New Session

### What We're Building:
An **intelligent multi-agent stock scanner** for Indian equities (NSE/BSE) that:
1. Scans 5000+ stocks through 5-stage pipeline (fundamental → technical → volume → risk)
2. Uses AI agents to reason about setups (not just rule-based)
3. Provides explainable recommendations (why each stock was picked)
4. Learns from trade outcomes to improve over time

### Why Multi-Agent Architecture:
- **Scanner Agent**: Runs technical pipeline, finds patterns
- **Research Agent**: Validates with news/fundamentals (to be built)
- **Risk Agent**: Challenges setups adversarially (to be built)
- **Execution Agent**: Monitors trades, alerts on stop breach (to be built)
- **Learning Agent**: Analyzes outcomes, tunes thresholds (to be built)

### Current Focus:
Building **Scanner Agent POC** - the foundation that other agents will build upon. Once Scanner Agent works end-to-end, we'll add Research/Risk agents.

---

## 🚀 Quick Start for New Session

### 1. Resume Context
```bash
# Read these files first:
- SESSION_STATE.md (this file)
- PRD_v2_with_recommendations.md (architecture)
- config.py (configuration)
- modules/ingest.py (data pipeline)
```

### 2. Verify Environment
```bash
# Check dependencies
pip list | grep -E "(crewai|langchain|anthropic|pandas)"

# Check .env file has ANTHROPIC_API_KEY
cat .env | grep ANTHROPIC_API_KEY
```

### 3. Next Steps (In Order)
```bash
# Create these 4 modules next:
1. modules/fundamental.py
2. modules/patterns.py
3. modules/volume.py
4. modules/risk.py

# Then create tools:
5. tools/data_tools.py

# Then create Scanner Agent:
6. agents/scanner_agent.py

# Then create orchestrator:
7. orchestrator/crew.py

# Finally, test end-to-end:
8. run_scanner.py
```

---

## 📊 Progress Metrics

**Time Spent**: ~2 hours (setup + data ingestion)  
**Lines of Code**: ~350 (config.py + ingest.py)  
**Modules Complete**: 1/5 (ingest.py)  
**Agents Complete**: 0/5  
**Estimated Remaining**: 16-18 days

**Next Milestone**: Scanner Agent working end-to-end (3-4 days)

---

## 💡 Tips for Continuation

1. **Start with modules/patterns.py** - most complex, will inform other modules
2. **Use PRD Section 5.3** as reference for pattern detection logic
3. **Keep modules pure functions** - agents will wrap them as tools
4. **Test each module independently** before integrating
5. **Use logging extensively** - helps debug agent tool calls

---

## 📝 Notes & Observations

- **Data Quality**: yfinance works but has occasional gaps; monitor for production readiness
- **Agent Costs**: Using Haiku for Scanner Agent to keep costs low (~$0.25/1M input tokens)
- **Caching Critical**: Without caching, would hit yfinance rate limits quickly
- **Pattern Library**: Starting with 3 patterns (consolidation, higher lows, compression), will expand to 8-10 later

---

**End of Session State**

Last action: Created data ingestion module with caching  
Next action: Create pattern detection module  
Ready to resume: ✅
