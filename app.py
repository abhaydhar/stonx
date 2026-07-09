"""
StockScanner dashboard (PRD Wave 5: UI-01..UI-07, LEARN-05).

A Streamlit dashboard over the deterministic scanner output, the trade journal,
per-agent reasoning, and (optionally) the learning agent's recommendations.

Run:
    streamlit run app.py

Design contract
---------------
This module MUST import cleanly with NO Streamlit rendering at import time. All
``st.*`` calls live inside functions; the page entrypoint is ``main()`` guarded
by ``if __name__ == "__main__": main()``. The data-shaping functions
(``load_scan_output``, ``scan_candidates_df``, ``filter_candidates``,
``reasoning_df``, ``open_positions_df``, ``closed_trades_df``,
``journal_summary``, ``learning_view``) are PURE and importable without
launching Streamlit -- they are what the tests target. The learning agent is
imported lazily inside ``learning_view`` so a missing/incomplete module never
breaks ``import app``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

try:  # optional at import time; only used inside render functions
    import plotly.express as px
    import plotly.graph_objects as go
except Exception:  # pragma: no cover - plotly is a soft dependency for rendering
    px = None  # type: ignore
    go = None  # type: ignore

import streamlit as st

logger = logging.getLogger(__name__)

# Display column contracts ---------------------------------------------------

SCAN_DISPLAY_COLUMNS: List[str] = [
    "symbol",
    "pattern",
    "entry",
    "stop",
    "target",
    "rr_ratio",
    "position_shares",
    "sector",
    "risk_status",
]

REASONING_COLUMNS: List[str] = [
    "symbol",
    "agent_name",
    "decision",
    "confidence",
    "reasoning",
]

DEFAULT_DATA_DIR = "./data"
DEFAULT_DB_URL = "sqlite:///./data/stonx.db"


# ===========================================================================
# Pure data functions (unit-tested; never call Streamlit)
# ===========================================================================


def load_scan_output(path: Optional[str | Path]) -> Dict[str, Any]:
    """Read a scanner-output JSON file.

    Returns the parsed dict, or an empty dict if the path is missing, empty,
    unreadable, or not valid JSON. Never raises.
    """
    if not path:
        return {}
    try:
        file_path = Path(path)
        if not file_path.is_file():
            return {}
        with file_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:  # noqa: BLE001 - defensive: any IO/parse error -> {}
        logger.debug("[dashboard] failed to load scan output %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def scan_candidates_df(scan: Optional[Dict[str, Any]]) -> pd.DataFrame:
    """Return scanner candidates as a DataFrame with the display columns (UI-02).

    Columns are fixed to ``SCAN_DISPLAY_COLUMNS`` regardless of the input; extra
    candidate fields are dropped and missing fields are filled with NaN.
    """
    candidates = (scan or {}).get("candidates", []) or []
    df = pd.DataFrame(candidates)
    if df.empty:
        return pd.DataFrame(columns=SCAN_DISPLAY_COLUMNS)
    return df.reindex(columns=SCAN_DISPLAY_COLUMNS)


def filter_candidates(
    df: Optional[pd.DataFrame],
    pattern: Optional[str] = None,
    min_rr: Optional[float] = None,
    sector: Optional[str] = None,
    volume_confirmed: Optional[bool] = None,
    approved_only: bool = False,
) -> pd.DataFrame:
    """Filter a candidates DataFrame (UI-03).

    Pure and robust: an empty/None DataFrame or missing columns never raise --
    a filter whose column is absent is simply skipped. ``approved_only`` keeps
    only rows where ``risk_status == 'approved'``.
    """
    if df is None:
        return pd.DataFrame()
    out = df.copy()
    if out.empty:
        return out

    if pattern is not None and "pattern" in out.columns:
        out = out[out["pattern"] == pattern]
    if sector is not None and "sector" in out.columns:
        out = out[out["sector"] == sector]
    if min_rr is not None and "rr_ratio" in out.columns:
        rr = pd.to_numeric(out["rr_ratio"], errors="coerce")
        out = out[rr >= float(min_rr)]
    if volume_confirmed is not None and "volume_confirmed" in out.columns:
        out = out[out["volume_confirmed"] == volume_confirmed]
    if approved_only and "risk_status" in out.columns:
        out = out[out["risk_status"] == "approved"]
    return out


def reasoning_df(
    journal: Any,
    run_id: Optional[str] = None,
    symbol: Optional[str] = None,
) -> pd.DataFrame:
    """Per-agent reasoning as a DataFrame (UI-04).

    Columns: symbol, agent_name, decision, confidence, reasoning.
    """
    try:
        rows = journal.get_agent_decisions(run_id=run_id, symbol=symbol)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully to empty
        logger.debug("[dashboard] get_agent_decisions failed: %s", exc)
        rows = []
    df = pd.DataFrame(rows or [])
    if df.empty:
        return pd.DataFrame(columns=REASONING_COLUMNS)
    return df.reindex(columns=REASONING_COLUMNS)


def open_positions_df(journal: Any) -> pd.DataFrame:
    """Open positions as a DataFrame (UI-05)."""
    try:
        rows = journal.get_open_positions()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[dashboard] get_open_positions failed: %s", exc)
        rows = []
    return pd.DataFrame(rows or [])


def closed_trades_df(journal: Any) -> pd.DataFrame:
    """Closed trades as a DataFrame (UI-05)."""
    try:
        rows = journal.get_closed_trades()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[dashboard] get_closed_trades failed: %s", exc)
        rows = []
    return pd.DataFrame(rows or [])


def _clean(value: Any, default: Any = None) -> Any:
    """NaN/None -> default; pass everything else through unchanged."""
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    return value


def open_position_from_candidate(
    journal: Any,
    candidate: Dict[str, Any],
    shares: Optional[int] = None,
    run_id: Optional[str] = None,
) -> int:
    """Log a scanner candidate as a new open position (UI-07).

    Maps scan-output candidate fields onto ``TradeJournal.open_position``.
    ``shares`` overrides the candidate's own ``position_shares`` when given.
    Returns the new position id; raises whatever the journal raises.
    """
    return journal.open_position(
        symbol=candidate["symbol"],
        entry_price=float(candidate["entry"]),
        stop_price=float(candidate["stop"]),
        target_price=float(candidate["target"]),
        shares=int(shares if shares is not None else _clean(candidate.get("position_shares"), 0)),
        sector=_clean(candidate.get("sector"), "Unknown"),
        pattern=_clean(candidate.get("pattern")),
        run_id=run_id,
    )


def close_trade_from_position(
    journal: Any,
    position_id: int,
    exit_price: float,
    exit_reason: str = "manual",
) -> Dict[str, Any]:
    """Close an open position at a user-supplied exit price (UI-08).

    Thin wrapper over ``TradeJournal.close_trade`` so the dashboard has a
    single, pure, testable entry point; ``outcome`` is left to the journal
    to derive from the resulting pnl sign.
    """
    return journal.close_trade(position_id, exit_price=float(exit_price), exit_reason=exit_reason)


def journal_summary(journal: Any) -> Dict[str, Any]:
    """Aggregate journal performance stats (UI-05). Never raises."""
    try:
        return dict(journal.summary())
    except Exception as exc:  # noqa: BLE001
        logger.debug("[dashboard] summary failed: %s", exc)
        return {}


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Attribute/key accessor that works on both dicts and objects."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _normalize_recommendation(rec: Any) -> Dict[str, Any]:
    """Coerce a learning recommendation (dict or object) into a plain dict."""
    keys = ("finding", "action", "config_change", "backtest_validation", "auto_apply")
    return {key: _get(rec, key) for key in keys}


def stock_symbol_options() -> List[str]:
    """Return "SYMBOL — Name" options for the Stock Review autocomplete (UI-09).

    Backed by the cached NSE symbol list; empty if that file is missing.
    """
    from modules.stock_review import load_symbol_universe  # local import keeps app import light

    df = load_symbol_universe()
    if df.empty:
        return []
    return [
        f"{row.symbol} — {row.name}" if pd.notna(row.name) else str(row.symbol)
        for row in df.itertuples()
    ]


def run_stock_review_cached(symbol: str, months: int = 6) -> Dict[str, Any]:
    """Thin wrapper so the review module is imported lazily, close to app.py's contract."""
    from modules.stock_review import run_stock_review  # local import keeps app import light

    return run_stock_review(symbol, months=months)


def learning_view(journal: Any) -> Dict[str, Any]:
    """Run the learning agent and return a normalized report dict (UI-06).

    The learning agent is imported LAZILY here. On ImportError (module not yet
    written / missing crewai etc.) or any runtime error, this degrades to
    ``{"status": "unavailable", "recommendations": []}``. Never raises.
    """
    unavailable = {"status": "unavailable", "recommendations": []}
    try:
        from agents.learning_agent import LearningAgent  # lazy import
    except Exception as exc:  # noqa: BLE001 - module may not exist yet
        logger.debug("[dashboard] learning agent unavailable: %s", exc)
        return unavailable

    try:
        report = LearningAgent().analyze(journal)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[dashboard] learning analyze failed: %s", exc)
        return unavailable

    recommendations = _get(report, "recommendations", []) or []
    return {
        "status": _get(report, "status", "ok"),
        "trades_analyzed": _get(report, "trades_analyzed"),
        "stats": _get(report, "stats", {}) or {},
        "recommendations": [_normalize_recommendation(rec) for rec in recommendations],
    }


# ===========================================================================
# Filesystem / journal helpers (no Streamlit)
# ===========================================================================


def latest_scan_path(data_dir: str | Path = DEFAULT_DATA_DIR) -> Optional[str]:
    """Return the newest ``scan_results_*.json`` under ``data_dir``, or None."""
    try:
        directory = Path(data_dir)
        if not directory.is_dir():
            return None
        matches = sorted(
            directory.glob("scan_results_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return str(matches[0]) if matches else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[dashboard] latest_scan_path failed: %s", exc)
        return None


def build_journal(db_url: Optional[str] = None) -> Any:
    """Construct a TradeJournal, falling back to the default sqlite URL."""
    from modules.journal import TradeJournal  # local import keeps app import light

    return TradeJournal(db_url=db_url or DEFAULT_DB_URL)


# ===========================================================================
# Streamlit render functions (call st.* -- only invoked from main())
# ===========================================================================


def render_scanner_tab(scan: Dict[str, Any], journal: Any = None) -> None:
    """Scanner Output tab with filters (UI-02, UI-03) and trade entry (UI-07)."""
    st.subheader("Scanner Output")

    if not scan:
        st.info("No scan output loaded. Point the sidebar at a scan_results_*.json file.")
        return

    regime = scan.get("market_regime", {}) or {}
    counts = scan.get("funnel_counts", {}) or {}
    top = st.columns(4)
    top[0].metric("Market regime", str(regime.get("regime", "-")))
    top[1].metric("Universe", counts.get("universe_total", "-"))
    top[2].metric("Approved", counts.get("approved", "-"))
    top[3].metric("Rejected", counts.get("rejected", "-"))
    if scan.get("timestamp"):
        st.caption(f"Scan timestamp: {scan['timestamp']}")

    df = scan_candidates_df(scan)

    with st.expander("Filters", expanded=True):
        cols = st.columns(3)
        patterns = sorted(p for p in df.get("pattern", pd.Series(dtype=object)).dropna().unique())
        sectors = sorted(s for s in df.get("sector", pd.Series(dtype=object)).dropna().unique())

        pattern_choice = cols[0].selectbox("Pattern", ["All", *patterns])
        sector_choice = cols[1].selectbox("Sector", ["All", *sectors])
        min_rr = cols[2].number_input("Min R:R", min_value=0.0, value=0.0, step=0.5)

        toggles = st.columns(2)
        approved_only = toggles[0].checkbox("Approved only", value=False)
        volume_confirmed = toggles[1].checkbox("Volume confirmed only", value=False)
        st.caption(
            "Volume-confirmed filter applies only when the scan data carries a "
            "'volume_confirmed' column; otherwise it is a no-op."
        )

    filtered = filter_candidates(
        df,
        pattern=None if pattern_choice == "All" else pattern_choice,
        min_rr=min_rr if min_rr > 0 else None,
        sector=None if sector_choice == "All" else sector_choice,
        volume_confirmed=True if volume_confirmed else None,
        approved_only=approved_only,
    )

    st.write(f"Showing {len(filtered)} of {len(df)} candidates")
    st.dataframe(filtered, use_container_width=True, hide_index=True)

    st.markdown("**Log a trade**")
    if journal is None:
        st.caption("Trade Journal is unavailable, so trades can't be logged right now.")
    elif filtered.empty:
        st.caption("No candidates to log.")
    else:
        try:
            open_symbols = {pos.get("symbol") for pos in journal.get_open_positions()}
        except Exception as exc:  # noqa: BLE001
            logger.debug("[dashboard] get_open_positions failed: %s", exc)
            open_symbols = set()

        header = st.columns([2, 2, 2, 2, 2, 2, 2])
        for col, label in zip(header, ["Symbol", "Pattern", "Entry", "Stop", "Target", "Shares", ""]):
            col.markdown(f"*{label}*")

        for idx, row in filtered.reset_index(drop=True).iterrows():
            candidate = row.to_dict()
            symbol = candidate.get("symbol")
            cols = st.columns([2, 2, 2, 2, 2, 2, 2])
            cols[0].write(symbol)
            cols[1].write(_clean(candidate.get("pattern"), "-"))
            cols[2].write(candidate.get("entry"))
            cols[3].write(candidate.get("stop"))
            cols[4].write(candidate.get("target"))
            default_shares = int(_clean(candidate.get("position_shares"), 0) or 0)
            shares = cols[5].number_input(
                "Shares",
                min_value=0,
                value=default_shares,
                step=1,
                key=f"shares_{symbol}_{idx}",
                label_visibility="collapsed",
            )
            already_open = symbol in open_symbols
            if cols[6].button(
                "Already open" if already_open else "Enter Trade",
                key=f"enter_{symbol}_{idx}",
                disabled=already_open,
            ):
                try:
                    position_id = open_position_from_candidate(
                        journal, candidate, shares=shares
                    )
                    st.success(f"Logged {symbol} as open position #{position_id}.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Failed to log {symbol}: {exc}")

    if px is not None and counts:
        try:
            funnel = pd.DataFrame({"stage": list(counts.keys()), "count": list(counts.values())})
            st.plotly_chart(
                px.bar(funnel, x="stage", y="count", title="Scan funnel"),
                use_container_width=True,
            )
        except Exception as exc:  # noqa: BLE001 - chart is best-effort
            logger.debug("[dashboard] funnel chart failed: %s", exc)


def render_reasoning_tab(journal: Any) -> None:
    """Agent Reasoning tab (UI-04)."""
    st.subheader("Agent Reasoning")
    cols = st.columns(2)
    symbol = cols[0].text_input("Filter by symbol (optional)").strip() or None
    run_id = cols[1].text_input("Filter by run_id (optional)").strip() or None

    df = reasoning_df(journal, run_id=run_id, symbol=symbol)
    if df.empty:
        st.info("No agent decisions recorded yet.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_journal_tab(journal: Any) -> None:
    """Trade Journal tab: open/closed trades and summary stats (UI-05)."""
    st.subheader("Trade Journal")

    summary = journal_summary(journal)
    if summary:
        cols = st.columns(5)
        cols[0].metric("Open", summary.get("open_count", 0))
        cols[1].metric("Closed", summary.get("total_closed", 0))
        cols[2].metric("Win rate", f"{summary.get('win_rate', 0.0) * 100:.1f}%")
        cols[3].metric("Total PnL", f"{summary.get('total_pnl', 0.0):,.0f}")
        cols[4].metric("Avg PnL %", f"{summary.get('avg_pnl_percent', 0.0):.2f}%")

    st.markdown("**Open positions**")
    open_df = open_positions_df(journal)
    if open_df.empty:
        st.info("No open positions.")
    else:
        st.dataframe(open_df, use_container_width=True, hide_index=True)

        st.markdown("**Close a trade**")
        header = st.columns([2, 2, 2, 2, 2, 1])
        for col, label in zip(header, ["Symbol", "Entry", "Current stop", "Target", "Exit price", ""]):
            col.markdown(f"*{label}*")

        for _, pos in open_df.reset_index(drop=True).iterrows():
            position_id = int(pos["id"])
            symbol = pos.get("symbol")
            entry_price = float(pos.get("entry_price", 0.0))
            cols = st.columns([2, 2, 2, 2, 2, 1])
            cols[0].write(symbol)
            cols[1].write(entry_price)
            cols[2].write(pos.get("current_stop"))
            cols[3].write(pos.get("target_price"))
            exit_price = cols[4].number_input(
                "Exit price",
                min_value=0.0,
                value=entry_price,
                step=0.05,
                key=f"exit_price_{position_id}",
                label_visibility="collapsed",
            )
            if cols[5].button("Close Trade", key=f"close_{position_id}"):
                try:
                    result = close_trade_from_position(journal, position_id, exit_price)
                    st.success(
                        f"Closed {symbol} at {exit_price:.2f} "
                        f"(pnl {result.get('pnl', 0.0):,.2f}, outcome {result.get('outcome')})."
                    )
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Failed to close {symbol}: {exc}")

    st.markdown("**Closed trades**")
    closed_df = closed_trades_df(journal)
    if closed_df.empty:
        st.info("No closed trades.")
    else:
        st.dataframe(closed_df, use_container_width=True, hide_index=True)
        if px is not None and "pnl" in closed_df.columns and "symbol" in closed_df.columns:
            try:
                st.plotly_chart(
                    px.bar(closed_df, x="symbol", y="pnl", title="Closed-trade PnL"),
                    use_container_width=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[dashboard] pnl chart failed: %s", exc)


def render_learning_tab(journal: Any) -> None:
    """Learning Insights tab + approve/reject placeholder (UI-06, LEARN-05)."""
    st.subheader("Learning Insights")

    view = learning_view(journal)
    status = view.get("status")

    if status == "unavailable":
        st.warning(
            "Learning module is unavailable (not yet installed or dependencies "
            "missing). Recommendations will appear here once it is ready."
        )
        return
    if status == "insufficient_data":
        analyzed = view.get("trades_analyzed")
        st.info(f"Not enough closed trades to learn from yet (analyzed: {analyzed}).")
        return

    stats = view.get("stats") or {}
    if stats:
        st.json(stats)

    recommendations = view.get("recommendations") or []
    if not recommendations:
        st.info("No recommendations at this time.")
        return

    st.warning(
        "Human approval is required. Approve/Reject below are placeholders -- "
        "no configuration change is auto-applied."
    )

    for idx, rec in enumerate(recommendations):
        finding = rec.get("finding") or f"Recommendation {idx + 1}"
        with st.expander(str(finding), expanded=False):
            if rec.get("action"):
                st.write(f"**Action:** {rec['action']}")
            if rec.get("config_change"):
                st.write("**Proposed config change:**")
                st.json(rec["config_change"])
            if rec.get("backtest_validation"):
                st.write("**Backtest validation:**")
                st.json(rec["backtest_validation"])
            st.caption(f"auto_apply = {rec.get('auto_apply')}")

            buttons = st.columns(2)
            if buttons[0].button("Approve", key=f"approve_{idx}"):
                st.success("Recorded approval (placeholder -- not auto-applied).")
            if buttons[1].button("Reject", key=f"reject_{idx}"):
                st.info("Recorded rejection (placeholder -- no change made).")


def render_stock_review_tab() -> None:
    """Stock Review tab: multi-agent deep dive on one symbol (UI-09).

    The symbol picker is a searchable selectbox constrained to the cached NSE
    symbol list, so a scrip can't be misspelled -- Streamlit filters options
    as the user types, giving autocomplete for free. "Run Analysis" fans the
    seven deterministic agents in modules.stock_review out and renders the
    consolidated report.
    """
    st.subheader("Stock Review")

    options = stock_symbol_options()
    if not options:
        st.warning(
            "No symbol list found at data/universe/nse_all_equities.csv. "
            "Refresh that file to enable the stock picker."
        )
        return

    cols = st.columns([3, 1, 1])
    choice = cols[0].selectbox("Search a stock (symbol or company name)", options, index=None, placeholder="Type to search...")
    months = cols[1].number_input("Lookback (months)", min_value=1, max_value=24, value=6, step=1)
    run_clicked = cols[2].button("Run Analysis", disabled=choice is None)

    if run_clicked and choice:
        symbol = choice.split(" — ")[0]
        with st.spinner(f"Running 7 agents for {symbol}..."):
            st.session_state["stock_review_report"] = run_stock_review_cached(symbol, months=int(months))

    report = st.session_state.get("stock_review_report")
    if not report:
        st.caption("Pick a stock and click Run Analysis to see the consolidated report.")
        return

    _render_stock_review_report(report)


def _render_stock_review_report(report: Dict[str, Any]) -> None:
    agents = report.get("agents", {})
    st.markdown(f"### {report.get('symbol')}")
    st.caption(f"Run at {report.get('timestamp')}")

    consensus = report.get("consensus", {})
    cols = st.columns(3)
    cols[0].metric("Bullish signals", consensus.get("bullish_signals", 0))
    cols[1].metric("Bearish signals", consensus.get("bearish_signals", 0))
    cols[2].metric("Neutral signals", consensus.get("neutral_signals", 0))
    st.caption(
        "Signal tally from the technical agents (EMA/VWAP, RSI, pattern recognizer) only -- "
        "informational, not a recommendation. Fundamentals and news are shown separately below."
    )

    _render_analyst_verdict_section(report.get("analyst_verdict", {}))
    _render_financial_report_section(agents.get("financial_report", {}))
    _render_pnl_section(agents.get("pnl_checker", {}))
    _render_candlestick_section(agents.get("candlesticks", {}))
    _render_pattern_section(agents.get("pattern_recognizer", {}))
    _render_ema_vwap_rsi_section(agents.get("ema_vwap", {}), agents.get("rsi", {}))
    _render_news_section(agents.get("news_validation", {}))


def _render_analyst_verdict_section(agent: Dict[str, Any]) -> None:
    """LLM synthesis of all 7 agents (UI-10). Absent gracefully when no GEMINI_API_KEY is set."""
    st.markdown("**Analyst verdict**")
    status = agent.get("status")
    if status == "unavailable":
        st.caption(
            "LLM reasoning is off (no GEMINI_API_KEY configured). Set it in .env to enable "
            "narrative synthesis, news sentiment, and this consolidated verdict."
        )
        return
    if status == "error":
        st.caption(f"Analyst verdict unavailable: {agent.get('error')}.")
        return
    narrative = (agent.get("data") or {}).get("narrative")
    if narrative:
        st.info(narrative)


def _agent_status_caption(agent: Dict[str, Any], label: str) -> bool:
    """Show a caption for non-ok statuses; returns True if the section should render data."""
    status = agent.get("status")
    if status == "error":
        st.caption(f"{label}: unavailable ({agent.get('error')}).")
        return False
    if status == "no_data" or status is None:
        st.caption(f"{label}: no data available.")
        return False
    return True


def _render_financial_report_section(agent: Dict[str, Any]) -> None:
    with st.expander("Financial report", expanded=True):
        if not _agent_status_caption(agent, "Financial report"):
            return
        data = agent.get("data", {})
        cols = st.columns(4)
        cols[0].metric("Price", data.get("current_price"))
        cols[1].metric("Market cap", f"{data.get('market_cap'):,.0f}" if data.get("market_cap") else "-")
        cols[2].metric("Trailing P/E", data.get("trailing_pe"))
        cols[3].metric("Forward P/E", data.get("forward_pe"))
        cols2 = st.columns(4)
        cols2[0].metric("EPS (TTM)", data.get("trailing_eps"))
        cols2[1].metric("ROE", data.get("return_on_equity"))
        cols2[2].metric("Debt/Equity", data.get("debt_to_equity"))
        cols2[3].metric("Div yield", data.get("dividend_yield"))
        st.caption(
            f"Sector: {data.get('sector', '-')}  |  Industry: {data.get('industry', '-')}  |  "
            f"52w range: {data.get('fifty_two_week_low', '-')} - {data.get('fifty_two_week_high', '-')}"
        )
        if data.get("narrative"):
            st.markdown(f"**Analyst read:** {data['narrative']}")


def _render_pnl_section(agent: Dict[str, Any]) -> None:
    with st.expander("P&L checker (income statement)", expanded=False):
        if not _agent_status_caption(agent, "P&L checker"):
            return
        data = agent.get("data", {})
        cols = st.columns(2)
        cols[0].metric("Revenue growth (YoY)", f"{data.get('revenue_growth_pct'):.1f}%" if data.get("revenue_growth_pct") is not None else "-")
        cols[1].metric("Net margin", f"{data.get('net_margin_pct'):.1f}%" if data.get("net_margin_pct") is not None else "-")
        history = data.get("history") or []
        if history:
            st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)


def _render_candlestick_section(agent: Dict[str, Any]) -> None:
    with st.expander("Candlesticks", expanded=False):
        if not _agent_status_caption(agent, "Candlesticks"):
            return
        data = agent.get("data", {})
        cols = st.columns(3)
        cols[0].metric("Period change", f"{data.get('period_change_pct'):.2f}%" if data.get("period_change_pct") is not None else "-")
        cols[1].metric("Period high", data.get("period_high"))
        cols[2].metric("Period low", data.get("period_low"))

        candles = data.get("candles") or []
        if not candles:
            return
        candles_df = pd.DataFrame(candles)
        if go is not None:
            try:
                fig = go.Figure(
                    data=[
                        go.Candlestick(
                            x=candles_df["date"],
                            open=candles_df["open"],
                            high=candles_df["high"],
                            low=candles_df["low"],
                            close=candles_df["close"],
                        )
                    ]
                )
                fig.update_layout(xaxis_rangeslider_visible=False, height=400)
                st.plotly_chart(fig, use_container_width=True)
            except Exception as exc:  # noqa: BLE001 - chart is best-effort
                logger.debug("[dashboard] candlestick chart failed: %s", exc)
        else:
            st.dataframe(candles_df, use_container_width=True, hide_index=True)


def _render_pattern_section(agent: Dict[str, Any]) -> None:
    with st.expander("Pattern recognizer", expanded=False):
        if not _agent_status_caption(agent, "Pattern recognizer"):
            return
        data = agent.get("data", {})
        st.write(f"**Best pattern:** {data.get('best_pattern') or 'None detected'}")
        patterns = data.get("patterns") or []
        if patterns:
            st.dataframe(pd.DataFrame(patterns), use_container_width=True, hide_index=True)


def _render_ema_vwap_rsi_section(ema_vwap: Dict[str, Any], rsi: Dict[str, Any]) -> None:
    with st.expander("EMA / VWAP / RSI", expanded=True):
        ema_ok = _agent_status_caption(ema_vwap, "EMA/VWAP")
        rsi_ok = _agent_status_caption(rsi, "RSI")
        if not ema_ok and not rsi_ok:
            return
        cols = st.columns(5)
        if ema_ok:
            data = ema_vwap.get("data", {})
            cols[0].metric("EMA20", data.get("ema20"))
            cols[1].metric("EMA50", data.get("ema50"))
            cols[2].metric("VWAP", data.get("vwap"))
            cols[3].metric("Trend bias", data.get("trend_bias"))
        if rsi_ok:
            rsi_data = rsi.get("data", {})
            cols[4].metric("RSI (14)", f"{rsi_data.get('rsi'):.1f} ({rsi_data.get('classification')})" if rsi_data.get("rsi") is not None else "-")


def _render_news_section(agent: Dict[str, Any]) -> None:
    with st.expander("Real-world news validation", expanded=False):
        if not _agent_status_caption(agent, "News validation"):
            return
        data = agent.get("data", {})
        st.caption(
            f"{data.get('total_articles', 0)} articles found, "
            f"{data.get('recent_articles', 0)} within the last {data.get('lookback_days', 30)} days. "
            "Tone is a naive keyword heuristic, not NLP sentiment."
        )
        tone_counts = data.get("tone_counts") or {}
        if tone_counts:
            cols = st.columns(3)
            cols[0].metric("Positive", tone_counts.get("positive", 0))
            cols[1].metric("Negative", tone_counts.get("negative", 0))
            cols[2].metric("Neutral", tone_counts.get("neutral", 0))
        llm_sentiment = data.get("llm_sentiment")
        if llm_sentiment:
            st.markdown(f"**LLM sentiment read:** {llm_sentiment['label']} — {llm_sentiment['rationale']}")
        for article in data.get("articles") or []:
            title = article.get("title") or "(untitled)"
            url = article.get("url")
            header = f"[{title}]({url})" if url else title
            st.markdown(f"- {header} — *{article.get('tone')}* ({article.get('provider') or 'unknown'}, {article.get('published') or 'n/a'})")


# ===========================================================================
# Entrypoint
# ===========================================================================


def main() -> None:
    """Wire the sidebar and the four dashboard tabs. Streamlit entrypoint."""
    st.set_page_config(page_title="StockScanner Dashboard", layout="wide")
    st.title("StockScanner Dashboard")

    with st.sidebar:
        st.header("Data sources")
        default_scan = latest_scan_path() or f"{DEFAULT_DATA_DIR}/scan_results.json"
        scan_path = st.text_input("Scan output JSON", value=default_scan)
        db_url = st.text_input("Journal DB URL", value=DEFAULT_DB_URL)
        st.caption("Point these at the scanner output and journal DB, then explore the tabs.")

    scan = load_scan_output(scan_path)

    journal = None
    journal_error: Optional[str] = None
    try:
        journal = build_journal(db_url)
    except Exception as exc:  # noqa: BLE001 - show a friendly message, keep app alive
        journal_error = str(exc)

    scanner_tab, reasoning_tab, journal_tab, learning_tab, review_tab = st.tabs(
        ["Scanner Output", "Agent Reasoning", "Trade Journal", "Learning Insights", "Stock Review"]
    )

    with scanner_tab:
        render_scanner_tab(scan, journal)

    with review_tab:
        render_stock_review_tab()

    if journal is None:
        for tab in (reasoning_tab, journal_tab, learning_tab):
            with tab:
                st.error(f"Journal unavailable: {journal_error}")
        return

    with reasoning_tab:
        render_reasoning_tab(journal)
    with journal_tab:
        render_journal_tab(journal)
    with learning_tab:
        render_learning_tab(journal)


if __name__ == "__main__":
    main()
