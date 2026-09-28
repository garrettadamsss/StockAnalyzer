#!/usr/bin/env python3
"""Annual fundamentals and quantitative moat tests for one ticker.

    python3 scripts/fetch_metrics.py --ticker AAPL --years 10
    python3 scripts/fetch_metrics.py --ticker T --out /tmp/t_metrics.json

Prints JSON: per-fiscal-year figures, a `tests` block with suggested verdicts
for the Two Engine, Capital Efficiency, leverage, and liquidity criteria, and a
`warnings` list describing any gaps. Exits non-zero with `{"error": ...}` when
fundamentals cannot be assembled.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import finlib


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticker", required=True, help="ticker symbol, for example AAPL")
    parser.add_argument("--years", type=int, default=10, help="number of annual periods to return (default 10)")
    parser.add_argument(
        "--source",
        choices=("sec", "yfinance"),
        default="sec",
        help="sec uses SEC XBRL company facts (default); yfinance is a fallback with shorter history",
    )
    parser.add_argument("--no-cache", action="store_true", help="bypass the 24 hour local cache")
    parser.add_argument("--out", help="also write the JSON payload to this path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        payload = finlib.build_fundamentals(
            args.ticker, years=args.years, source=args.source, use_cache=not args.no_cache
        )
    except finlib.DataError as exc:
        finlib.fail(str(exc), {"ticker": args.ticker.upper(), "source": args.source})
        return

    rows = payload["fiscal_years"]
    latest = rows[-1]
    payload["as_of"] = date.today().isoformat()
    payload["period_covered"] = {"first": rows[0]["period_end"], "last": latest["period_end"]}
    payload["latest"] = latest
    payload["tests"] = finlib.score_tests(rows)
    payload["valuation_inputs"] = {
        "free_cash_flow": latest["free_cash_flow"],
        "free_cash_flow_3y_avg": finlib.average(
            [row["free_cash_flow"] for row in rows[-3:] if row["free_cash_flow"] is not None]
        ),
        "cash": latest["cash_and_investments"],
        "debt": latest["total_debt"],
        "diluted_shares": latest["diluted_shares"],
        "note": "pass these to dcf_valuation.py, or use --from-metrics with this file",
    }

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
        payload["written_to"] = str(path)

    finlib.emit(payload)


if __name__ == "__main__":
    main()
