#!/usr/bin/env python
"""Sync BTS Marketing Carrier On-Time Performance data into the analytical layer.

Examples:
    python scripts/sync_bts.py --months 36
    python scripts/sync_bts.py --start 2024-01 --end 2026-06
    python scripts/sync_bts.py --months 36 --force --no-prune
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flightops.config import DEFAULT_HISTORY_MONTHS
from flightops.data.pipeline import ReconciliationError, run_sync
from flightops.data.schema import SchemaError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--months", type=int, default=None, help=f"latest N published months (default {DEFAULT_HISTORY_MONTHS})")
    parser.add_argument("--start", help="first month, YYYY-MM (overrides --months)")
    parser.add_argument("--end", help="last month, YYYY-MM (default: latest published)")
    parser.add_argument("--force", action="store_true", help="reprocess months that are already loaded")
    parser.add_argument("--no-prune", action="store_true", help="keep processed months outside the window")
    parser.add_argument("--offline", action="store_true", help="use only cached raw files; no network")
    parser.add_argument("--purge-raw", action="store_true", help="delete each raw ZIP after processing")
    args = parser.parse_args(argv)

    months = args.months if (args.months or args.start) else DEFAULT_HISTORY_MONTHS
    try:
        run_sync(
            months=months,
            start=args.start,
            end=args.end,
            force=args.force,
            prune=not args.no_prune,
            offline=args.offline,
            purge_raw=args.purge_raw,
        )
    except (SchemaError, ReconciliationError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
