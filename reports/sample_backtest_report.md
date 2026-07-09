# Backtest Report

- Generated: 2026-07-09T06:49:25
- Period: 2022-01-03T00:00:00 -> 2022-03-23T00:00:00
- Overall: PASS

## Configuration

| Parameter | Value |
|---|---|
| min_rr | 1.5 |
| consolidation_range_pct | 0.08 |
| volume_spike_multiplier | 1.5 |
| max_holding_days | 15 |
| warmup_bars | 49 |
| capital | 1000000.0 |
| risk_pct | 0.01 |

## Metrics

| Metric | Value |
|---|---|
| Total trades | 6 |
| Wins | 4 |
| Losses | 2 |
| Win rate | 0.6667 |
| Avg win % | 0.0486 |
| Avg loss % | -0.0291 |
| Expectancy | 0.0227 |
| Profit factor | 3.3481 |
| Sharpe | 8.9687 |
| Max drawdown | 0.0573 |
| Avg bars held | 3.6667 |

## PRD Threshold Check

| Criterion | Result | Detail |
|---|---|---|
| Expectancy | PASS | 0.0227 (require > 0.0000) |
| Win rate | PASS | 0.6667 (require >= 0.4000) |
| Sharpe | PASS | 8.9687 (require >= 1.0000) |
| Max drawdown | PASS | 0.0573 (require <= 0.2000) |

## Trades

| Symbol | Pattern | Entry date | Entry | Stop | Target | Exit date | Exit | PnL % | R:R | Bars | Outcome |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DEMO_WIN_0.NS | range_tightening | 2022-03-11T00:00:00 | 100.0388 | 96.96 | 105.34 | 2022-03-18T00:00:00 | 105.34 | 0.0530 | 1.7219 | 5 | win |
| DEMO_WIN_1.NS | range_tightening | 2022-03-11T00:00:00 | 99.8739 | 96.94 | 104.3 | 2022-03-17T00:00:00 | 104.3 | 0.0443 | 1.5086 | 4 | win |
| DEMO_WIN_2.NS | range_tightening | 2022-03-11T00:00:00 | 99.9202 | 97.17 | 104.51 | 2022-03-17T00:00:00 | 104.51 | 0.0459 | 1.6689 | 4 | win |
| DEMO_WIN_3.NS | range_tightening | 2022-03-11T00:00:00 | 100.1151 | 96.98 | 105.25 | 2022-03-18T00:00:00 | 105.25 | 0.0513 | 1.6379 | 5 | win |
| DEMO_LOSS_0.NS | range_tightening | 2022-03-11T00:00:00 | 100.0389 | 97.08 | 105.28 | 2022-03-15T00:00:00 | 97.08 | -0.0296 | 1.7713 | 2 | loss |
| DEMO_LOSS_1.NS | range_tightening | 2022-03-11T00:00:00 | 100.0129 | 97.16 | 104.42 | 2022-03-15T00:00:00 | 97.16 | -0.0285 | 1.5448 | 2 | loss |
