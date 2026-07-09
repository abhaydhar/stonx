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
except Exception:  # pragma: no cover - plotly is a soft dependency for rendering
    px = None  # type: ignore

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


def render_scanner_tab(scan: Dict[str, Any]) -> None:
    """Scanner Output tab with filters (UI-02, UI-03)."""
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

    scanner_tab, reasoning_tab, journal_tab, learning_tab = st.tabs(
        ["Scanner Output", "Agent Reasoning", "Trade Journal", "Learning Insights"]
    )

    with scanner_tab:
        render_scanner_tab(scan)

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
