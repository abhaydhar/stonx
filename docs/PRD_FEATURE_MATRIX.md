# StockScanner PRD Feature / Verification Matrix (QA-01)

Generated: 2026-07-09

This matrix maps every PRD v2 feature/requirement to the automated test(s) or
source file that verifies it. It is organized by PRD area, mirroring the
sections of `PRD_v2_code_gap_audit.md`. It reflects the repository after
Waves 0-8 of `PRD_v2_agent_task_breakdown.md` were built.

## How to reproduce

```bash
python -m pytest -q
```

- Full suite result at time of writing: **178 passed** (Python 3.12 venv).
- All tests are offline and deterministic: no network, no `ANTHROPIC_API_KEY`,
  and no heavy deps (crewai / langchain / nsepy / vectorbt / apprise) required.
- Agents are LLM-agnostic via an injectable `llm_client` (`agents/llm.py`:
  `LLMClient`, `DeterministicLLM`, `FakeLLM`, `build_llm_client`).

## Legend

| Status | Meaning |
|---|---|
| Pass | Implemented and covered by an automated test (or a shipped artifact). |
| Partial | Implemented and working, but narrower than the full PRD ambition. |
| Deferred | Framework/abstraction present, but the full-scope capability is intentionally out of MVP scope. |

`Verified by` uses `path::Class::test` for pytest items, or a file path for
non-test artifacts / manual coverage.

---

## Core Pipeline

| Requirement | Status | Verified by |
|---|---|---|
| EOD pipeline: ingestion -> fundamental -> technical -> volume -> risk -> ranking | Pass | `tests/test_integration.py::test_real_scanner_funnel_with_fake_provider`; `tests/test_scanner.py::TestDeterministicScanner`; `modules/scanner.py` (`DeterministicScanner`) |
| Config-driven pipeline (SCAN-01: overrides change behavior) | Pass | `tests/test_scanner.py::TestDeterministicScanner::test_config_overrides_change_module_behavior` |
| Universe loader (CSV, extensible) | Pass | `tests/test_scanner.py::TestDataAndFundamentals::test_universe_loader_reads_csv_fixture`; `data/universe/nse_universe.csv` |
| Full NSE/BSE universe (~5000 stocks) | Partial | Loader reads any CSV; shipped `data/universe/nse_universe.csv` has ~40 liquid NSE names, not the full ~5000 |
| OHLCV provider abstraction (yfinance + pluggable NSE) | Pass | `tests/test_scanner.py::TestDataAndFundamentals::test_provider_quality_metadata_records_source_and_missing_pct`; `modules/ingest.py` (`OHLCVProvider`, `YFinanceOHLCVProvider`) |
| NSEpy primary source | Deferred | `modules/ingest.py` provider seam allows adding it; yfinance is the shipped provider, NSEpy not implemented |
| Data quality metadata (source, adjusted, missing %, rows) | Pass | `tests/test_scanner.py::TestDataAndFundamentals::test_provider_quality_metadata_records_source_and_missing_pct`; `modules/ingest.py` (`DataQualityMetadata`) |
| Data caching (parquet) | Pass | `modules/ingest.py` (parquet cache); `requirements.txt` (pyarrow) + `README.md` dependency notes (no dedicated unit test; tests run `use_cache=False`) |
| Fundamental filter: market cap, revenue growth, debt/equity, promoter holding | Pass | `tests/test_scanner.py::TestDataAndFundamentals::test_fundamentals_read_fixture_csv_and_enforce_promoter_holding`; `data/fundamentals/fundamentals_fixture.csv` |
| Fundamental data source (CSV, promoter holding sourced) | Pass | same as above; `modules/fundamental.py` (`FundamentalFilter` CSV path) |
| Screener.in / live fundamental API | Deferred | CSV fixture source only |
| Technical patterns: consolidation, higher lows, range tightening | Pass | `tests/test_scanner.py::TestPatternDetector::test_consolidation_detected`, `::test_higher_lows_detected`, `::test_range_tightening_detected`, `::test_scan_returns_scan_result` |
| False-breakout / 2-bar hold rule (SCAN-05) | Pass | `tests/test_scanner.py::TestPatternDetector::test_false_breakout_rejected_without_two_bar_hold` (and `::test_consolidation_detected` asserts `breakout_hold_bars == 2`) |
| Volume profile HVN/LVN | Pass | `tests/test_scanner.py::TestVolumeProfiler::test_profile_builds`, `::test_hvn_support_below_price`, `::test_lvn_targets_above_price` |
| Risk/reward gate + position sizing | Pass | `tests/test_scanner.py::TestRiskManager::test_approved_valid_setup`, `::test_position_size_correct`, `::test_rejected_low_rr` |
| Portfolio heat + sector limits enforced in scan path (SCAN-04) | Pass | `tests/test_scanner.py::TestDeterministicScanner::test_scan_path_enforces_sector_limit`, `::test_scan_path_enforces_portfolio_heat_limit`; `TestRiskManager::test_portfolio_heat_limit`, `::test_sector_limit` |
| Market regime filter with bull/bear min R:R (SCAN-03) | Pass | `tests/test_scanner.py::TestDeterministicScanner::test_bear_market_uses_higher_min_rr_in_scan_path` |
| JSON + CSV output with funnel counts (SCAN-06) | Pass | `tests/test_scanner.py::TestDeterministicScanner::test_json_and_csv_outputs_are_written`; `tests/test_integration.py::test_real_scanner_funnel_with_fake_provider` |

---

## Multi-Agent Layer

| Requirement | Status | Verified by |
|---|---|---|
| Deterministic Scanner with PRD funnel counts | Pass | `tests/test_scanner.py::TestDeterministicScanner`; `modules/scanner.py` (`ScannerOutput.funnel_counts`) |
| Research Agent contract: summary, sentiment, red flags, citations, confidence (RES-02) | Pass | `tests/test_research_agent.py::test_positive_news_sentiment_and_citations`, `::test_to_dict_contract_and_bounds` |
| Research citations + staleness handling (RES-03) | Pass | `tests/test_research_agent.py::test_null_web_source_flags_no_citations_and_lowers_confidence`, `::test_stale_fundamentals_red_flag`, `::test_missing_fundamentals_date_flag` |
| Research negative-headline red flags | Pass | `tests/test_research_agent.py::test_negative_headline_records_red_flag_and_lowers_sentiment` |
| Mockable web tools (RES-01) | Pass | `tests/test_research_agent.py::test_imports_are_offline_safe`; `tools/web_tools.py` (`StubWebSource`, `NullWebSource`, `WebResearchSource`) |
| Live news scraping / real web research | Deferred | Stub source only; no live scraping |
| Risk tools: volatility, beta, correlation, ATR, max drawdown (RISK-01) | Pass | `tests/test_risk_agent.py::test_volatility_noisy_greater_than_calm`, `::test_beta_identical_is_one`, `::test_correlation_identical_and_inverted`, `::test_atr_positive_and_guarded`, `::test_max_drawdown_range_and_handcheck`, `::test_compute_risk_metrics_bundle` |
| Adversarial Risk Agent: APPROVED / REJECTED / CONDITIONAL + concerns (RISK-02) | Pass | `tests/test_risk_agent.py::test_clean_candidate_approved`, `::test_red_flag_fraud_rejected`, `::test_single_noncritical_red_flag_conditional`, `::test_extreme_volatility_rejected` |
| Stop-loss validation (ATR band, stop>=entry) | Pass | `tests/test_risk_agent.py::test_stop_above_entry_fails_and_rejected`, `::test_stop_too_tight_fails_on_atr` |
| Position-size adjustment contract w/ reason (RISK-03) | Pass | `tests/test_risk_agent.py::test_apply_size_multiplier_shares`, `::test_apply_size_multiplier_floors_and_clamps`, `::test_weak_sentiment_conditional_half_size`, `::test_high_volatility_conditional_trimmed` |
| Risk decision contract shape | Pass | `tests/test_risk_agent.py::test_to_dict_contract_keys`; batch: `::test_challenge_batch` |
| Orchestration: scanner -> research -> risk (ORCH-01) | Pass | `tests/test_orchestrator.py::test_pipeline_runs_scanner_research_risk_and_persists`; `orchestrator/pipeline.py` (`ScanResearchRiskPipeline`) |
| Persist agent reasoning, queryable by symbol/run (ORCH-02) | Pass | `tests/test_orchestrator.py::test_pipeline_runs_scanner_research_risk_and_persists` (asserts `get_agent_decisions(run_id=...)`) |
| Pipeline counts / no-persist mode | Pass | `tests/test_orchestrator.py::test_pipeline_counts_summarize_decisions`, `::test_pipeline_without_persistence_still_returns_run` |
| Agents run without LLM key (LLM-agnostic) | Pass | `tests/test_research_agent.py::test_deterministic_llm_falls_back_to_rule_based_summary`; `tests/test_risk_agent.py::test_deterministic_reasoning_default`; `agents/llm.py` |
| Live CrewAI multi-agent run (LLM) | Deferred | `orchestrator/crew.py`, `agents/scanner_agent.py` present but require crewai + `ANTHROPIC_API_KEY`; deterministic pipeline is the canonical, tested path |

---

## Dashboard, Alerts, and Journal

### Dashboard (Streamlit)

| Requirement | Status | Verified by |
|---|---|---|
| Streamlit app shell, no import-time side effects (UI-01) | Pass | `tests/test_dashboard.py::test_app_imports_without_streamlit_side_effects`; `app.py` |
| Scanner Output tab (UI-02) | Pass | `tests/test_dashboard.py::test_scan_candidates_df_columns_and_rowcount`, `::test_load_scan_output_reads_written_sample`; `app.py::render_scanner_tab` |
| Scanner filters: pattern, min R:R, sector, volume-confirmed, approved-only (UI-03) | Pass | `tests/test_dashboard.py::test_filter_candidates_min_rr_drops_low_rr`, `::test_filter_candidates_sector`, `::test_filter_candidates_pattern`, `::test_filter_candidates_approved_only`, `::test_filter_candidates_volume_confirmed_when_column_present` |
| Agent Reasoning tab (UI-04) | Pass | `tests/test_dashboard.py::test_reasoning_df`; `app.py::render_reasoning_tab` |
| Trade Journal tab: open/closed + summary (UI-05) | Pass | `tests/test_dashboard.py::test_open_positions_df`, `::test_closed_trades_df`, `::test_journal_summary` |
| Learning Insights tab (UI-06) | Pass | `tests/test_dashboard.py::test_learning_view_returns_dict_with_status`, `::test_learning_view_survives_import_failure` |
| Approve/reject placeholder, no auto-apply (LEARN-05) | Pass | `app.py::render_learning_tab` (placeholder buttons); `tests/test_learning.py` (`auto_apply is False`); manual: `docs/DASHBOARD_CHECKLIST.md` |
| Dashboard smoke / load-time target (UI-07) | Partial | Pure-function tests only in `tests/test_dashboard.py`; interactive/perf coverage is manual via `docs/DASHBOARD_CHECKLIST.md` |

### Trade Journal / Persistence

| Requirement | Status | Verified by |
|---|---|---|
| SQLAlchemy models: candidates, agent_decisions, open_positions, closed_trades (DB-01) | Pass | `tests/test_journal.py::TestSchemaAndEmptyState::test_tables_created` |
| Repository create/update/close/query (DB-02) | Pass | `tests/test_journal.py::TestTradeLifecycle::test_open_update_close_flow`, `::test_losing_trade_negative_pnl`, `::test_close_unknown_position_raises` |
| Persist candidates + rejected setups with stage/reason (DB-03) | Pass | `tests/test_journal.py::TestCandidateHistory::test_record_scan_persists_candidates_and_rejected`, `::test_record_scan_rejected_have_stage_and_reason` |
| Agent-decision persistence + JSON payload round-trip | Pass | `tests/test_journal.py::TestAgentDecisions::test_record_and_roundtrip_payload_json`, `::test_filter_by_symbol_and_run` |
| Summary stats (win rate, PnL, open count) | Pass | `tests/test_journal.py::TestSummary::test_summary_win_rate_over_closed_trades`; empty-guard `TestSchemaAndEmptyState::test_summary_empty_guards_div_by_zero` |
| Isolated fixture DB tests (DB-04) | Pass | `tests/test_journal.py::TestIsolation::test_separate_journals_do_not_share_state`; `TestSchemaAndEmptyState::test_in_memory_engine_supported` |

### Execution Monitoring + Alerts

| Requirement | Status | Verified by |
|---|---|---|
| Execution Agent inspects open trades + prices (EXEC-01) | Pass | `tests/test_execution_agent.py::test_callable_price_provider`, `::test_missing_price_is_skipped`; `agents/execution_agent.py` (`DictPriceProvider`) |
| Stop/target breach detection + open case (EXEC-02) | Pass | `tests/test_execution_agent.py::test_stop_breached_closes_position`, `::test_target_hit_closes_position`, `::test_open_event_changes_nothing` |
| Trailing stop to breakeven after +1R (EXEC-03) | Pass | `tests/test_execution_agent.py::test_trailing_stop_moves_to_breakeven`, `::test_trailing_disabled_yields_open`, `::test_trailing_not_repeated_once_at_breakeven` |
| ExecutionEvent contract | Pass | `tests/test_execution_agent.py::test_execution_event_to_dict_contract`, `::test_run_once_returns_events_and_summary` |
| Alert formatter: daily summary + stop breach + trade event (ALERT-01) | Pass | `tests/test_alert_tools.py::test_daily_summary_contains_key_fields`, `::test_stop_breach_template_contains_symbol_and_prices`, `::test_trade_event_template` |
| Dry-run alert mode, never live-sends / imports apprise (ALERT-02) | Pass | `tests/test_alert_tools.py::test_dry_run_send_returns_unsent_message`, `::test_dry_run_never_imports_apprise`, `::test_live_mode_without_urls_does_not_import_apprise`, `::test_module_import_does_not_load_apprise` |
| Breach dispatches alert to sender | Pass | `tests/test_execution_agent.py::test_stop_breach_dispatches_alert_without_live_send`, `::test_open_event_does_not_alert` |
| Wire execution-monitor CLI command (ALERT-03) | Pass | `tests/test_cli.py::test_main_dispatches_monitor`, `::test_monitor_once_closes_breached_position`, `::test_monitor_once_no_positions_is_noop` |
| Telegram / live alert delivery | Deferred | Live apprise path exists but is dry-run only in tests; no real Telegram send exercised |

---

## Backtesting and Learning

| Requirement | Status | Verified by |
|---|---|---|
| Backtest engine runs on fixture OHLCV (BT-01) | Pass | `tests/test_backtest.py::TestRun::test_run_produces_trades_and_metrics`, `::test_single_win_fixture_produces_one_winning_trade`, `::test_single_loss_fixture_produces_losing_trade` |
| Backtest shares scanner/risk logic (BT-02) | Pass | `tests/test_backtest.py::TestBacktestConfig::test_from_scanner_config_defaults`, `::test_scanner_namespace_maps_attribute_names` |
| Metrics: expectancy, win rate, Sharpe, max drawdown, profit factor, trade count (BT-03) | Pass | `tests/test_backtest.py::TestMetricsMath::test_exact_metrics_on_handcrafted_trades`, `::test_profit_factor_infinite_when_no_losses`, `::test_equity_curve_and_drawdown_helpers` |
| Empty/insufficient-data safety | Pass | `tests/test_backtest.py::TestEmptyAndInsufficient::test_empty_price_data`, `::test_insufficient_bars_skipped`, `::test_none_and_missing_columns_skipped` |
| Walk-forward split, configurable ranges (BT-04) | Pass | `tests/test_backtest.py::TestWalkForward::test_returns_expected_number_of_splits`, `::test_train_test_ranges_are_configurable_and_sequential`, `::test_signals_restricted_to_test_window` |
| Parameter optimization sweep (BT-05) | Pass | `tests/test_backtest.py::TestOptimize::test_sweep_grid_and_sort_by_expectancy`, `::test_empty_grid_runs_base_config` |
| Report artifact with per-threshold PASS/FAIL (BT-06) | Pass | `tests/test_backtest.py::TestReport::test_report_written_with_pass_fail_per_threshold`, `::test_thresholds_are_overridable`, `::test_default_thresholds_evaluate_all_four`; artifact `reports/sample_backtest_report.md` |
| Backtest over real 2020-2025 market data | Deferred | Framework + seeded synthetic fixtures only; no bundled real historical dataset |
| Trade outcome analytics: win rate, avg win/loss, expectancy, drawdown, pattern/sector stats (LEARN-01) | Pass | `tests/test_learning.py::TestAnalyzeTrades::test_exact_metrics_with_losing_streak`, `::test_by_pattern_breakdown_two_patterns`, `::test_by_sector_breakdown`; `TestAnalyzeJournal::test_journal_totals_match` |
| Learning Agent recommendations + config change (LEARN-02) | Pass | `tests/test_learning.py::TestRecommendations::test_sixty_trades_produce_recommendations`, `::test_low_win_rate_triggers_volume_recommendation`, `::test_propose_config_changes_merges` |
| Minimum sample-size gate (50 trades) (LEARN-03) | Pass | `tests/test_learning.py::TestMinimumSample::test_insufficient_data_below_min_trades`, `::test_custom_min_trades_threshold`; `TestAnalyzeJournal::test_agent_accepts_journal_directly` |
| Backtest recommendation before/after metrics (LEARN-04) | Pass | `tests/test_learning.py::TestBacktestValidation::test_recommendation_has_before_after_metrics` |
| Human approval required, no auto-apply | Pass | `tests/test_learning.py::TestRecommendations` (`rec.auto_apply is False`); `app.py::render_learning_tab` placeholder |

---

## Config and Environment

| Requirement | Status | Verified by |
|---|---|---|
| Pydantic config (`ScannerConfig`) | Pass | `config.py`; `tests/test_config.py` |
| Config loads without `ANTHROPIC_API_KEY` (FND-05) | Pass | `tests/test_config.py::test_config_loads_without_anthropic_key`, `::test_config_ignores_unrelated_dotenv_keys` |
| Capital, risk %, min R:R, heat, max positions, sector limit | Pass | `config.py`; `.env.example`; exercised via `tests/test_scanner.py::TestDeterministicScanner` |
| Fundamental/technical/volume thresholds configurable and wired (SCAN-01) | Pass | `tests/test_scanner.py::TestDeterministicScanner::test_config_overrides_change_module_behavior` |
| Agent model names in config | Pass | `config.py`; `.env.example` (`SCANNER_AGENT_MODEL`, `RESEARCH_AGENT_MODEL`, ...) |
| API keys via environment + `.env.example` (FND-04) | Pass | `.env.example`; `tests/test_config.py` |
| Supported Python version documented (FND-01) | Pass | `README.md` (Python 3.12.13); `.python-version` |
| Reproducible requirements (FND-02, FND-03) | Pass | `requirements.txt` (nsepy pinned to 0.8, pyarrow added); `README.md` dependency notes |
| Install/test smoke-command docs (FND-06) | Pass | `README.md` (Setup + Smoke Checks) |

---

## Full-System QA (Wave 8)

| Task | Status | Verified by |
|---|---|---|
| QA-01 PRD feature/verification matrix | Pass | this document (`docs/PRD_FEATURE_MATRIX.md`) |
| QA-02 integration tests with mocked externals | Pass | `tests/test_integration.py::test_pipeline_to_execution_monitor_end_to_end`, `::test_real_scanner_funnel_with_fake_provider` |
| QA-03 CLI smoke tests (dry-run/scan/monitor dispatch) | Pass | `tests/test_cli.py::test_main_dispatches_deterministic`, `::test_main_dispatches_pipeline`, `::test_main_dispatches_monitor`, `::test_pipeline_scan_uses_injected_pipeline` |
| QA-04 dashboard manual checklist | Pass | `docs/DASHBOARD_CHECKLIST.md` |
| QA-05 full regression loop | Pass | `python -m pytest -q` -> 178 passed |
| QA-06 final status report | Pass | `docs/FINAL_STATUS.md` |
