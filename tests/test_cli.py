"""
CLI smoke tests (QA-03) for run_scanner.py.

Verifies that `main()` dispatches each flag to the right function and that the
injection-friendly `monitor_once` / `pipeline_scan` helpers behave predictably
with mocked collaborators (no network, no LLM).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_scanner
from agents.execution_agent import DictPriceProvider
from modules.journal import TradeJournal


def _run_main(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_scanner.py", *argv])
    with pytest.raises(SystemExit) as exc:
        run_scanner.main()
    return exc.value.code


def test_main_dispatches_deterministic(monkeypatch):
    called = {}
    monkeypatch.setattr(run_scanner, "deterministic_scan", lambda **kw: called.update(kw) or "ok")
    code = _run_main(["--deterministic", "--symbols", "AAA.NS,BBB.NS", "--market-regime", "bull"], monkeypatch)
    assert code == 0
    assert called["symbols"] == ["AAA.NS", "BBB.NS"]
    assert called["market_regime"] == "bull"


def test_main_dispatches_pipeline(monkeypatch):
    called = {}
    monkeypatch.setattr(run_scanner, "pipeline_scan", lambda **kw: called.update(kw) or "ok")
    code = _run_main(["--pipeline", "--limit", "5"], monkeypatch)
    assert code == 0
    assert called["limit"] == 5


def test_main_dispatches_monitor(monkeypatch):
    called = {}
    monkeypatch.setattr(run_scanner, "monitor_once", lambda **kw: called.update(kw) or {"events": []})
    code = _run_main(["--monitor"], monkeypatch)
    assert code == 0
    assert called["dry_run"] is True


def test_monitor_once_closes_breached_position(tmp_path):
    journal = TradeJournal(db_url=f"sqlite:///{tmp_path}/cli.db")
    journal.open_position(
        symbol="AAA.NS", sector="IT", entry_price=100.0, stop_price=90.0,
        target_price=140.0, shares=100,
    )

    status = run_scanner.monitor_once(
        journal=journal,
        price_provider=DictPriceProvider({"AAA.NS": 85.0}),
        dry_run=True,
    )

    assert len(status["events"]) == 1
    assert status["events"][0].event_type == "STOP_BREACHED"
    assert journal.get_open_positions() == []
    assert status["summary"]["total_closed"] == 1


def test_monitor_once_no_positions_is_noop(tmp_path):
    journal = TradeJournal(db_url=f"sqlite:///{tmp_path}/cli2.db")
    status = run_scanner.monitor_once(journal=journal, price_provider=DictPriceProvider({}), dry_run=True)
    assert status["events"] == []
    assert status["summary"]["open_count"] == 0


def test_pipeline_scan_uses_injected_pipeline(tmp_path):
    class _FakeResult:
        run_id = "RUN1"
        counts = {"scanned_candidates": 1, "approved": 1}
        class _Dec:
            symbol = "AAA.NS"; approval_status = "APPROVED"; pattern = "p"
            rr_ratio = 3.0; position_size_multiplier = 1.0; adjusted_shares = 100
            sentiment_score = 0.7
        approved = [_Dec()]

    class _FakePipeline:
        def run(self, **kw):
            return _FakeResult()

    result = run_scanner.pipeline_scan(pipeline=_FakePipeline())
    assert result.run_id == "RUN1"
    assert result.counts["approved"] == 1
