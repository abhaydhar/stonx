**Indian Stock Market Scanner**

Product Requirements Document (PRD)

Version 1.0  |  June2026

1\. Product Overview
====================

This document defines requirements for a personal stock marketscanning tool that identifies high-probability trade setups in Indian equities.The system combines fundamental screening, technical pattern detection, volumeprofile analysis, and risk/reward gating into a single automated pipeline.

_The goal is not to predictwinners with certainty — it is to systematically surface setups where risk isclearly defined, reward potential is 2-3x that risk, and volume confirmsgenuine market interest._

2\. Objectives
==============

•       Filter NSE/BSE-listed stocks through a multi-stagepipeline ending in a ranked shortlist of 5-10 actionable setups per day

•       Automate the fundamental + technical + volume screeningthat would otherwise require hours of manual chart review

•       Enforce position sizing and risk/reward disciplineprogrammatically, removing emotional decision-making

•       Track trade outcomes over time to measure and tunesystem edge

3\. Scope
=========

3.1 In Scope
------------

•       End-of-day (EOD) data pipeline from NSE/BSE

•       Fundamental screening layer using financial metrics

•       Technical pattern detection (consolidation, higherlows, range tightening)

•       Volume profile analysis (HVN/LVN identification)

•       Risk/reward gate and position sizing calculator

•       Output: ranked shortlist as Streamlit dashboard +optional Telegram alert

•       Trade journal for outcome tracking

3.2 Out of Scope (v1)
---------------------

•       Intraday data or real-time scanning

•       Automated order execution or broker integration

•       Machine learning-based pattern recognition

•       Options or derivatives screening

4\. System Architecture
=======================

The pipeline runs in five sequential stages. Each stage actsas a filter, reducing the universe before passing candidates to the next stage.

**Pipeline Flow**

**Stage**

**Name**

**Input**

**Output**

1

Data Ingestion

NSE/BSE universe (~5000 stocks)

OHLCV + fundamentals datastore

2

Fundamental Filter

Full universe

~200-300 quality stocks

3

Technical Pattern Scan

Fundamental filtered set

~30-50 pattern candidates

4

Volume Profile Analysis

Pattern candidates

~15-20 confirmed setups

5

R:R Gate + Sizing

Confirmed setups

5-10 ranked trade ideas

5\. Detailed Requirements
=========================

5.1 Stage 1 — Data Ingestion
----------------------------

**Data Sources**

•       Primary: yfinance (EOD OHLCV for NSE symbols, suffix.NS)

•       Alternative: Jugaad Trader for more reliable NSE data

•       Fundamental data: Screener.in API or pre-cachedquarterly CSV

**Requirements**

•       Fetch last 100 trading days of OHLCV for all stocks inthe filtered universe

•       Cache data locally to avoid repeated API calls (SQLiteor Parquet files)

•       Refresh cache daily after market close (3:30 PM IST)

•       Handle missing data gracefully — skip stocks with>10% missing bars

5.2 Stage 2 — Fundamental Filter
--------------------------------

Purpose: Eliminate weak businesses and illiquid stocks beforechart analysis.

**Filter**

**Criterion**

**Rationale**

Market Cap

\> ₹500 Cr

Avoid illiquid micro-caps and operator-driven stocks

Revenue Growth

YoY > 0%

Eliminate declining businesses

Debt-to-Equity

< 1.0

Avoid overleveraged companies

Promoter Holding

\> 40%

Align with management who have skin in the game

Profit after Tax

Positive (TTM)

Eliminate loss-making companies

Output: A filtered list of ~200-300 quality stocks updatedweekly (fundamentals are quarterly data).

5.3 Stage 3 — Technical Pattern Detection
-----------------------------------------

Purpose: Identify stocks in high-probability setupconfigurations using price action rules.

**Pattern 1: Consolidation AfterUptrend**

•       Prior trend: Stock up >20% in the last 60 days

•       Consolidation: Price range over last 15-20 bars < 8%from high to low

•       Not rolling over: Price above 50-day SMA

**Pattern 2: Higher LowsFormation**

•       Identify swing lows over last 30 bars

•       Require minimum 3 swing lows each higher than theprevious

•       Each low must be at least 5 bars apart

**Pattern 3: Range Tightening(Compression)**

•       Average true range (ATR) of last 10 bars < 50% ofATR over last 30 bars

•       Candle bodies shrinking: average body size last 5 bars< average last 20 bars

A stock must match at least one pattern to proceed. Outputflags which pattern(s) triggered.

5.4 Stage 4 — Volume Profile Analysis
-------------------------------------

Purpose: Confirm institutional interest and identify pricelevels where price is likely to move efficiently.

**Volume Profile Calculation**

•       Use last 60-100 days of OHLCV data

•       Divide the price range into 20 equal bins

•       Sum volume traded within each bin

•       Identify High Volume Nodes (HVN): bins with volume >150% of mean bin volume

•       Identify Low Volume Nodes (LVN): bins with volume <50% of mean bin volume

**Entry Signal Logic**

•       HVN below current price: acts as support — confirms thefloor

•       LVN above current price: thin air above — price canmove quickly through it

•       Point of Control (POC) — highest volume bin — should bebelow current price in a bullish setup

**Confirmation Rule**

•       Latest breakout or move candle must have volume >=1.5x the 20-day average volume

•       If volume is below this threshold, flag as'unconfirmed' but still include in output

5.5 Stage 5 — Risk/Reward Gate and Position Sizing
--------------------------------------------------

Purpose: Enforce trade math discipline. Only surface setupswhere the numbers make sense.

**Stop Loss Identification**

•       Primary: Below the most recent consolidation low

•       Secondary: Below the nearest HVN support level

•       Use the higher of the two as the stop (tighter risk)

**Target Identification**

•       Primary: Next significant resistance level (prior swinghigh)

•       Secondary: Upper boundary of next LVN zone above entry

**R:R Gate**

•       Calculate: (Target - Entry) / (Entry - Stop)

•       Minimum threshold: 2.5x

•       Setups below 2.5x are excluded from the final shortlist

**Position Sizing Formula**

Risk per trade = Capital × Risk % (default 1%)

Position size = Risk per trade / (Entry price - Stop price)

This keeps maximum loss per trade fixed regardless of stockprice.

6\. Output Specification
========================

6.1 Streamlit Dashboard
-----------------------

•       Table of ranked setups with: stock name, patterntriggered, entry zone, stop, target, R:R ratio, suggested position size

•       Colour coding: green for R:R > 3x, amber for 2.5-3x

•       Volume confirmation badge: confirmed / unconfirmed

•       Link to TradingView chart for each stock

•       Filter controls: by pattern type, minimum R:R, marketcap tier

6.2 Telegram Alert (Optional)
-----------------------------

•       Daily message at 4:00 PM IST listing top 5 setups

•       Format: Symbol | Pattern | Entry | Stop | Target | R:R

•       Only confirmed (volume-verified) setups included inalert

6.3 Trade Journal (CSV)
-----------------------

•       Columns: Date, Symbol, Pattern, Entry, Stop, Target,R:R, Position Size, Outcome (filled manually)

•       Append-only — never overwrite historical rows

•       Used for edge measurement after 50+ trades

7\. Technology Stack
====================

**Component**

**Library/Tool**

**Notes**

Data fetch

yfinance / Jugaad Trader

EOD OHLCV from NSE

Fundamental data

Screener.in API or CSV

Quarterly refresh

Data processing

pandas, numpy

Core pipeline logic

Technical indicators

pandas-ta

SMA, ATR, swing detection

Volume profile

Custom pandas logic

HVN/LVN bin calculation

Dashboard UI

Streamlit

Fastest path to visual output

Alerts

python-telegram-bot

Optional Telegram integration

Storage

SQLite or Parquet

Local caching of OHLCV data

Scheduling

cron or APScheduler

Daily post-market run

8\. Recommended Project Structure
=================================

stock-scanner/   ├── data/  │     ├── cache/          # SQLite or Parquet OHLCV cache   │    └── fundamentals/   # Screener.inexport CSVs   ├── modules/   │    ├── ingest.py       # Stage 1:data fetch & cache   │     ├── fundamental.py  # Stage 2: fundamental filter   │    ├── patterns.py     # Stage 3:technical pattern detection   │     ├── volume.py       # Stage 4: volume profile HVN/LVN   │    └── risk.py         # Stage 5: R:Rgate & position sizing   ├──app.py                # Streamlit dashboard   ├── alerts.py             # Telegram bot (optional)   ├── journal.csv           # Trade outcome log   ├── run\_pipeline.py       # Orchestrator — runs all stages   ├── config.py             # Capital, risk %, thresholds   └── requirements.txt

9\. Build Phases
================

**Phase**

**Deliverable**

**Est. Time**

1

Data pipeline (ingest.py) — fetch, cache, validate OHLCV

1-2 days

2

Fundamental filter (fundamental.py) — screen by cap, growth, debt

1 day

3

Pattern detection (patterns.py) — consolidation, higher lows, compression

2-3 days

4

Volume profile (volume.py) — HVN/LVN calculation and confirmation

1-2 days

5

Risk/reward + sizing (risk.py) — gate and position calculator

1 day

6

Streamlit dashboard (app.py) — ranked table with filters

1-2 days

7

Testing, tuning, and journal setup

Ongoing

Total estimated build time for working v1: 1.5 to 2 weeks offocused development.

10\. Measuring System Edge
==========================

After accumulating 50+ closed trades, calculate:

•       Win rate: % of trades that hit target before stop

•       Average win / average loss ratio

•       Expectancy = (Win rate × Avg win) - (Loss rate × Avgloss)

•       Best-performing pattern type (consolidation vs higherlows vs compression)

_A positive expectancy confirms the system has edge. Usethese metrics to drop underperforming patterns and raise the R:R threshold overtime._

11\. Configuration Parameters (config.py)
=========================================

**Parameter**

**Default Value**

**Description**

CAPITAL

1,000,000 (₹10L)

Total trading capital

RISK\_PCT

0.01 (1%)

Max % of capital to risk per trade

MIN\_RR

2.5

Minimum risk/reward ratio to include setup

MIN\_MARKET\_CAP\_CR

500

Minimum market cap in crores

VOLUME\_LOOKBACK\_DAYS

100

Days of OHLCV for volume profile

CONSOLIDATION\_DAYS

20

Lookback window for consolidation check

CONSOLIDATION\_RANGE\_PCT

0.08

Max % range to qualify as consolidation

VOLUME\_SPIKE\_MULTIPLIER

1.5

Min volume vs 20-day avg for confirmation

HVN\_THRESHOLD

1.5

Volume multiple above mean to flag as HVN

LVN\_THRESHOLD

0.5

Volume fraction below mean to flag as LVN

12\. Key Risks and Mitigations
==============================

**Risk**

**Impact**

**Mitigation**

NSE data gaps or rate limits from yfinance

Pipeline failures

Cache aggressively; fall back to Jugaad Trader

False positives from rule-based pattern detection

Noisy shortlist

Require pattern + volume confirmation together

Operator-driven moves mimicking real patterns

Bad entries

Fundamental filter and market cap floor reduce exposure

Fundamental data staleness (quarterly)

Screening on outdated data

Flag stocks with financials older than 6 months

Over-optimising thresholds on limited history

Curve fitting

Minimum 50 trades before changing parameters

_End of Document_

Indian Stock Market Scanner — PRDv1.0  | June 2026