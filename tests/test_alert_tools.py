"""
Unit tests for tools/alert_tools.py (PRD ALERT-01..02).

All tests are fully offline. The dry-run guarantee (no ``apprise`` import, no
network) is asserted explicitly by monkeypatching the import machinery.
"""

import builtins
import sys
from pathlib import Path

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.alert_tools import AlertFormatter, AlertSender


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


def test_daily_summary_contains_key_fields():
    candidates = [
        {
            "symbol": "AAA.NS",
            "pattern": "consolidation_after_uptrend",
            "entry": 100.0,
            "stop": 90.0,
            "target": 130.0,
            "rr_ratio": 3.0,
        }
    ]
    text = AlertFormatter().daily_summary(
        candidates, market_regime="bull", run_id="run123"
    )
    assert isinstance(text, str) and text.strip()
    assert "AAA.NS" in text
    assert "stop=" in text and "target=" in text
    assert "90.00" in text and "130.00" in text
    assert "bull" in text and "run123" in text


def test_daily_summary_empty():
    text = AlertFormatter.daily_summary([])
    assert "No candidates today." in text


def test_stop_breach_template_contains_symbol_and_prices():
    event = {
        "symbol": "AAA.NS",
        "event_type": "STOP_BREACHED",
        "entry_price": 100.0,
        "exit_price": 90.0,
        "pnl": -1000.0,
        "pnl_percent": -10.0,
    }
    text = AlertFormatter.stop_breach(event)
    assert text.strip()
    assert "AAA.NS" in text
    assert "STOP BREACHED" in text
    assert "90.00" in text  # stop / exit
    assert "-1000.00" in text  # pnl


def test_trade_event_template():
    event = {
        "symbol": "BBB.NS",
        "event_type": "TARGET_HIT",
        "entry_price": 100.0,
        "exit_price": 130.0,
        "pnl": 3000.0,
        "pnl_percent": 30.0,
    }
    text = AlertFormatter.trade_event(event)
    assert "TARGET_HIT" in text
    assert "BBB.NS" in text
    assert "130.00" in text


def test_formatter_accepts_object_via_getattr():
    class _Evt:
        symbol = "CCC.NS"
        event_type = "STOP_BREACHED"
        entry_price = 50.0
        exit_price = 45.0
        pnl = -500.0
        pnl_percent = -10.0

    text = AlertFormatter.stop_breach(_Evt())
    assert "CCC.NS" in text and "45.00" in text


# ---------------------------------------------------------------------------
# Sender — dry-run guarantees (ALERT-02)
# ---------------------------------------------------------------------------


def test_dry_run_send_returns_unsent_message():
    sender = AlertSender()  # dry_run defaults to True
    result = sender.send("Title", "the-body-text")
    assert result["sent"] is False
    assert result["dry_run"] is True
    assert result["channels"] == 0
    assert "the-body-text" in result["message"]


def test_dry_run_never_imports_apprise(monkeypatch):
    """Constructing dry-run and sending must not import apprise at all."""
    real_import = builtins.__import__

    def guard(name, *args, **kwargs):
        if name == "apprise" or name.startswith("apprise."):
            raise AssertionError("apprise must not be imported in dry-run mode")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)

    sender = AlertSender(dry_run=True, apprise_urls=["json://localhost"])
    result = sender.send("T", "body")
    assert result["sent"] is False
    assert result["dry_run"] is True


def test_live_mode_without_urls_does_not_import_apprise(monkeypatch):
    """dry_run=False but no URLs -> still no send, no apprise import."""
    real_import = builtins.__import__

    def guard(name, *args, **kwargs):
        if name == "apprise" or name.startswith("apprise."):
            raise AssertionError("apprise must not be imported without URLs")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)

    sender = AlertSender(dry_run=False, apprise_urls=None)
    result = sender.send("T", "body")
    assert result["sent"] is False
    assert result["channels"] == 0


def test_module_import_does_not_load_apprise():
    """Importing the module must not have pulled apprise into sys.modules."""
    # A fresh import of the module here should not load apprise transitively.
    import importlib

    import tools.alert_tools as at  # noqa: F401

    importlib.reload(at)
    assert "apprise" not in sys.modules


def test_send_daily_summary_wrapper():
    sender = AlertSender()
    result = sender.send_daily_summary(
        [{"symbol": "AAA.NS", "entry": 100, "stop": 90, "target": 130}],
        market_regime="bull",
        run_id="r1",
    )
    assert result["sent"] is False
    assert "AAA.NS" in result["message"]


def test_send_execution_event_wrapper():
    sender = AlertSender()
    event = {
        "symbol": "AAA.NS",
        "event_type": "STOP_BREACHED",
        "entry_price": 100.0,
        "exit_price": 90.0,
        "pnl": -1000.0,
        "pnl_percent": -10.0,
    }
    result = sender.send_execution_event(event)
    assert result["sent"] is False
    assert "AAA.NS" in result["message"]
    assert "STOP BREACHED" in result["message"]
