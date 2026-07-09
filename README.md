# StockScanner

StockScanner is an early PRD v2 implementation for scanning NSE trade setups.
The current codebase contains deterministic scanner modules, a Scanner Agent
wrapper, and unit tests for the core pattern, volume, and risk logic.

## Supported Python

Use Python 3.12.13. The repository includes `.python-version` for tools that
can read it.

## Setup

From the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill `ANTHROPIC_API_KEY` in `.env` only when running the full LLM agent scan.
Core unit tests and `python run_scanner.py --dry-run` do not require LLM keys.

## Smoke Checks

```powershell
python -m pytest -q
python run_scanner.py --dry-run
```

`--dry-run` skips LLM calls but still fetches market data through yfinance, so
it needs network access. The full `python run_scanner.py` path requires
`ANTHROPIC_API_KEY`.

For dependency resolver checks without installing packages, modern pip can run:

```powershell
python -m pip install --dry-run --ignore-installed -r requirements.txt
```

## Dependency Notes

- `nsepy` is pinned to `0.8` because `0.9.1` is not published for the tested
  Python 3.12 resolver path.
- `pyarrow` is included because `modules/ingest.py` writes parquet cache files.
- `pandas-ta` and `TA-Lib` are not part of the default install yet. The current
  scanner code does not import them; reintroduce them when the implementation
  actually needs those libraries and CI has the native TA-Lib build support.
