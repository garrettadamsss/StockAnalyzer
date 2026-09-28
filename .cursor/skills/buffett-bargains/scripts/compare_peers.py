#!/usr/bin/env python3
"""The Standout Test: gross margin of one company against same-industry peers.

    python3 scripts/compare_peers.py --ticker AAPL --peers MSFT,DELL,HPQ

Prints JSON with each ticker's latest and three-year average gross margin, the
peer median, and a suggested verdict. Peers must come from the same industry;
comparing across industries is meaningless (Walmart 24% versus Google 58%).
"""

from __future__ import annotations

import argparse
from datetime import date
from typing import Any, Dict, List, Optional

import finlib


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticker", required=True, help="the company being analyzed")
    parser.add_argument("--peers", required=True, help="comma separated same-industry peer tickers")
    parser.add_argument("--years", type=int, default=5, help="annual periods to pull per ticker (default 5)")
    parser.add_argument("--source", choices=("sec", "yfinance"), default="sec")
    parser.add_argument("--no-cache", action="store_true", help="bypass the 24 hour local cache")
    return parser.parse_args()


def median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def gross_margin_row(ticker: str, years: int, source: str, use_cache: bool) -> Dict[str, Any]:
    try:
        payload = finlib.build_fundamentals(ticker, years=years, source=source, use_cache=use_cache)
    except finlib.DataError as exc:
        return {"ticker": ticker.upper(), "error": str(exc)}

    rows = payload["fiscal_years"]
    margins = [row["gross_margin"] for row in rows if row["gross_margin"] is not None]
    latest = rows[-1]
    return {
        "ticker": payload["ticker"],
        "company": payload["company"],
        "period_end": latest["period_end"],
        "revenue": latest["revenue"],
        "gross_margin": latest["gross_margin"],
        "gross_margin_3y_avg": finlib.average(margins[-3:]),
        "operating_margin": latest["operating_margin"],
        "years": len(rows),
    }


def main() -> None:
    args = parse_args()
    use_cache = not args.no_cache
    peers = [peer.strip() for peer in args.peers.split(",") if peer.strip()]
    if not peers:
        finlib.fail("no peer tickers supplied")
        return

    subject = gross_margin_row(args.ticker, args.years, args.source, use_cache)
    peer_rows = [gross_margin_row(peer, args.years, args.source, use_cache) for peer in peers]

    warnings = [f"{row['ticker']}: {row['error']}" for row in [subject] + peer_rows if "error" in row]
    peer_margins = [row["gross_margin"] for row in peer_rows if row.get("gross_margin") is not None]
    peer_median = median(peer_margins)
    subject_margin = subject.get("gross_margin")

    if subject_margin is None or peer_median is None:
        verdict = "inconclusive"
    elif subject_margin > peer_median:
        verdict = "pass"
    else:
        verdict = "fail"

    finlib.emit(
        {
            "as_of": date.today().isoformat(),
            "source": args.source,
            "subject": subject,
            "peers": peer_rows,
            "standout_test": {
                "criterion": "gross margin exceeds same-industry competitors",
                "subject_gross_margin": subject_margin,
                "peer_median_gross_margin": peer_median,
                "peers_compared": len(peer_margins),
                "verdict": verdict,
                "notes": ["confirm the peer set is genuinely the same industry before trusting this verdict"],
            },
            "warnings": warnings,
        }
    )


if __name__ == "__main__":
    main()
