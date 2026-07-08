"""
run_scanner.py — Entry point for the StockScanner MVP

Usage:
    python run_scanner.py              # full scan
    python run_scanner.py --dry-run   # skip LLM, test module imports only
    python run_scanner.py --verbose   # full CrewAI trace

Environment:
    Requires .env file with ANTHROPIC_API_KEY set.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path regardless of where script is called from
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Load .env before any module imports that need API keys
# ---------------------------------------------------------------------------
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"scanner_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("run_scanner")


# ---------------------------------------------------------------------------
# Dry-run mode: test imports and a single stock pipeline without LLM
# ---------------------------------------------------------------------------

def dry_run():
    """
    Test all modules end-to-end on a single stock (RELIANCE.NS) without
    invoking the LLM — validates data pipeline and module imports.
    """
    logger.info("=" * 60)
    logger.info("DRY RUN — testing modules without LLM")
    logger.info("=" * 60)

    symbol = "RELIANCE.NS"

    # 1. Data ingestion
    from modules.ingest import DataIngestion
    ingestion = DataIngestion()
    df = ingestion.fetch_ohlcv(symbol)
    if df is None:
        logger.error(f"[dry_run] Data ingestion failed for {symbol}")
        return False
    logger.info(f"[dry_run] Ingest OK — {len(df)} bars for {symbol}")

    # 2. Fundamental filter
    from modules.fundamental import FundamentalFilter
    ff = FundamentalFilter()
    result = ff.screen(symbol)
    logger.info(
        f"[dry_run] Fundamental: {'PASS' if result.passed else 'FAIL — ' + str(result.rejection_reason)}"
    )

    # 3. Pattern detection
    from modules.patterns import PatternDetector
    pd_mod = PatternDetector()
    scan = pd_mod.scan(symbol, df)
    logger.info(
        f"[dry_run] Patterns: passed={scan.passed}, best={scan.best_pattern}"
    )
    for p in scan.patterns:
        logger.info(f"         {p.pattern_name}: detected={p.detected}, conf={p.confidence} | {p.notes}")

    # 4. Volume profile
    from modules.volume import VolumeProfiler
    vp = VolumeProfiler()
    profile, hvn, lvns = vp.analyse(symbol, df)
    if profile:
        logger.info(
            f"[dry_run] Volume profile: HVN support={hvn}, LVN targets={lvns[:3]}"
        )

    # 5. Risk / Reward
    from modules.risk import RiskManager, RiskSetup
    rm = RiskManager()
    if profile and hvn and lvns:
        setup = RiskSetup(
            symbol=symbol,
            entry_price=profile.current_price,
            stop_price=hvn,
            target_price=lvns[0] if lvns else profile.current_price * 1.10,
            sector="Energy",
        )
        risk_result = rm.validate(setup)
        logger.info(
            f"[dry_run] Risk gate: approved={risk_result.approved} | "
            f"R:R={risk_result.rr_ratio} | {risk_result.notes or risk_result.rejection_reason}"
        )

    logger.info("[dry_run] All modules operational - OK")
    return True


# ---------------------------------------------------------------------------
# Full scan via CrewAI
# ---------------------------------------------------------------------------

def full_scan(verbose: bool = False):
    """Run the full Scanner Agent pipeline via CrewAI."""
    logger.info("=" * 60)
    logger.info("STONX SCANNER — Starting full scan")
    logger.info("=" * 60)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error(
            "ANTHROPIC_API_KEY not found in environment.  "
            "Create a .env file with ANTHROPIC_API_KEY=sk-ant-..."
        )
        sys.exit(1)

    from orchestrator.crew import StonxCrew

    crew = StonxCrew(verbose=verbose)
    output = crew.run()

    if output["error"]:
        logger.error(f"Scan failed: {output['error']}")
        sys.exit(1)

    setups = output["setups"]
    logger.info(f"\n{'=' * 60}")
    logger.info(f"SCAN COMPLETE — {len(setups)} setups found")
    logger.info("=" * 60)

    if setups:
        print("\n🔍 TOP TRADE SETUPS\n" + "-" * 40)
        for setup in setups:
            print(
                f"#{setup.get('rank', '?')} {setup.get('symbol', '?')} | "
                f"Pattern: {setup.get('pattern', '?')} | "
                f"R:R: {setup.get('rr_ratio', '?')}x | "
                f"Entry: ₹{setup.get('entry', '?')} | "
                f"Stop: ₹{setup.get('stop', '?')} | "
                f"Target: ₹{setup.get('target', '?')}"
            )
            if setup.get("rationale"):
                print(f"   → {setup['rationale']}")
        print()

        # Save results to JSON
        results_path = PROJECT_ROOT / "data" / f"scan_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        results_path.parent.mkdir(exist_ok=True)
        with open(results_path, "w") as f:
            json.dump({"timestamp": datetime.now().isoformat(), "setups": setups}, f, indent=2)
        logger.info(f"Results saved to {results_path}")
    else:
        logger.info("No trade setups found in today's scan.")
        print("\n⚠  No setups found today.  Raw agent output:\n")
        print(output["raw_output"][:2000])

    return setups


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="StockScanner — AI-powered NSE trade setup finder"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test module pipeline without LLM (no API calls)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable full CrewAI agent trace",
    )
    args = parser.parse_args()

    if args.dry_run:
        success = dry_run()
        sys.exit(0 if success else 1)
    else:
        full_scan(verbose=args.verbose)


if __name__ == "__main__":
    main()
