#!/usr/bin/env python3
"""Discounted cash flow intrinsic value with a margin of safety entry price.

    python3 scripts/dcf_valuation.py --fcf 1000 --cash 2000 --debt 5000
    python3 scripts/dcf_valuation.py --from-metrics /tmp/aapl_metrics.json --price 245.50
    python3 scripts/dcf_valuation.py --from-metrics /tmp/t_metrics.json --growth 0.03 \
        --terminal-multiple 10 --sensitivity

Follows the six steps in valuation.md: project 10 years of free cash flow at the
growth rate, apply the terminal multiple to year 10, discount everything at the
required return, add cash and subtract debt, then take the margin of safety off.
Defaults are the guide's: 10% growth, 20x terminal, 15% discount, 30% margin of
safety. Overrides must be justified in the report.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import finlib

SENSITIVITY_GROWTH = (0.00, 0.05, 0.10, 0.15)
SENSITIVITY_MULTIPLES = (10, 15, 20, 25)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fcf", type=float, help="base free cash flow (year 0)")
    parser.add_argument(
        "--from-metrics",
        help="fetch_metrics.py JSON file to read fcf, cash, debt, and shares from",
    )
    parser.add_argument(
        "--fcf-basis",
        choices=("latest", "avg3"),
        default="latest",
        help="with --from-metrics, use the latest year or the three year average (default latest)",
    )
    parser.add_argument("--growth", type=float, default=0.10, help="annual free cash flow growth (default 0.10)")
    parser.add_argument("--discount", type=float, default=0.15, help="required annual return (default 0.15)")
    parser.add_argument("--terminal-multiple", type=float, default=20.0, help="exit multiple on year 10 (default 20)")
    parser.add_argument("--years", type=int, default=10, help="holding period in years (default 10)")
    parser.add_argument("--cash", type=float, help="cash and equivalents to add")
    parser.add_argument("--debt", type=float, help="total debt to subtract")
    parser.add_argument(
        "--margin-of-safety", type=float, default=0.30, help="fraction taken off intrinsic value (default 0.30)"
    )
    parser.add_argument("--shares", type=float, help="diluted shares outstanding, for per-share figures")
    parser.add_argument("--price", type=float, help="current share price, to compare against the entry price")
    parser.add_argument("--sensitivity", action="store_true", help="add an entry price grid across growth and multiples")
    return parser.parse_args()


def discounted_cash_flows(fcf: float, growth: float, discount: float, years: int) -> List[Dict[str, float]]:
    schedule = []
    for year in range(1, years + 1):
        projected = fcf * (1 + growth) ** year
        schedule.append(
            {
                "year": year,
                "projected_fcf": projected,
                "discount_factor": 1 / (1 + discount) ** year,
                "discounted_fcf": projected / (1 + discount) ** year,
            }
        )
    return schedule


def intrinsic_value(
    fcf: float,
    growth: float,
    discount: float,
    terminal_multiple: float,
    years: int,
    cash: float,
    debt: float,
) -> Dict[str, Any]:
    schedule = discounted_cash_flows(fcf, growth, discount, years)
    terminal_value = schedule[-1]["projected_fcf"] * terminal_multiple
    discounted_terminal = terminal_value / (1 + discount) ** years
    discounted_sum = sum(row["discounted_fcf"] for row in schedule)
    enterprise = discounted_sum + discounted_terminal
    return {
        "schedule": schedule,
        "sum_projected_fcf": sum(row["projected_fcf"] for row in schedule),
        "terminal_value": terminal_value,
        "discounted_terminal_value": discounted_terminal,
        "sum_discounted_fcf": discounted_sum,
        "value_before_adjustments": enterprise,
        "intrinsic_value": enterprise + cash - debt,
    }


def load_metrics(path: str, basis: str) -> Dict[str, Optional[float]]:
    payload = json.loads(Path(path).read_text())
    if "valuation_inputs" not in payload:
        raise finlib.DataError(f"{path} has no valuation_inputs block; was it produced by fetch_metrics.py?")
    inputs = payload["valuation_inputs"]
    fcf = inputs["free_cash_flow"] if basis == "latest" else inputs["free_cash_flow_3y_avg"]
    return {
        "ticker": payload.get("ticker"),
        "period_end": payload.get("latest", {}).get("period_end"),
        "fcf": fcf,
        "cash": inputs.get("cash") or 0.0,
        "debt": inputs.get("debt") or 0.0,
        "shares": inputs.get("diluted_shares"),
    }


def main() -> None:
    args = parse_args()
    context: Dict[str, Optional[float]] = {}

    if args.from_metrics:
        try:
            context = load_metrics(args.from_metrics, args.fcf_basis)
        except (finlib.DataError, OSError, ValueError) as exc:
            finlib.fail(str(exc))
            return

    fcf = args.fcf if args.fcf is not None else context.get("fcf")
    cash = args.cash if args.cash is not None else float(context.get("cash") or 0.0)
    debt = args.debt if args.debt is not None else float(context.get("debt") or 0.0)
    shares = args.shares if args.shares is not None else context.get("shares")

    if fcf is None:
        finlib.fail("no base free cash flow: pass --fcf or --from-metrics")
        return
    if fcf <= 0:
        finlib.fail(
            f"base free cash flow is {fcf:,.0f}; a DCF on negative cash flow is meaningless, "
            "normalize the base year or treat the company as too hard"
        )
        return
    if args.discount <= args.growth:
        finlib.fail("discount rate must exceed the growth rate")
        return

    result = intrinsic_value(
        fcf, args.growth, args.discount, args.terminal_multiple, args.years, cash, debt
    )
    value = result["intrinsic_value"]
    entry_price = value - value * args.margin_of_safety

    payload: Dict[str, Any] = {
        "ticker": context.get("ticker"),
        "assumptions": {
            "base_free_cash_flow": fcf,
            "base_free_cash_flow_basis": args.fcf_basis if args.from_metrics else "supplied",
            "base_period_end": context.get("period_end"),
            "growth_rate": args.growth,
            "discount_rate": args.discount,
            "terminal_multiple": args.terminal_multiple,
            "years": args.years,
            "cash": cash,
            "debt": debt,
            "margin_of_safety": args.margin_of_safety,
        },
        "projection": result["schedule"],
        "totals": {
            "sum_projected_fcf": result["sum_projected_fcf"],
            "terminal_value": result["terminal_value"],
            "total_cash_generated": result["sum_projected_fcf"] + result["terminal_value"],
            "sum_discounted_fcf": result["sum_discounted_fcf"],
            "discounted_terminal_value": result["discounted_terminal_value"],
            "value_before_cash_and_debt": result["value_before_adjustments"],
            "intrinsic_value": value,
            "entry_price": entry_price,
        },
        "warnings": [],
    }

    if args.growth == 0.10 and args.from_metrics:
        payload["warnings"].append(
            "using the default 10% growth rate; confirm the Two Engine test and business narrative support it"
        )

    if shares:
        payload["per_share"] = {
            "diluted_shares": shares,
            "intrinsic_value_per_share": value / shares,
            "entry_price_per_share": entry_price / shares,
        }
        if args.price:
            entry_per_share = entry_price / shares
            payload["per_share"]["current_price"] = args.price
            payload["per_share"]["premium_to_entry_price"] = args.price / entry_per_share - 1
            payload["per_share"]["action"] = (
                "at or below entry price" if args.price <= entry_per_share else "above entry price, wait"
            )
    elif args.price:
        payload["warnings"].append("--price ignored because --shares was not supplied")

    if args.sensitivity:
        def entry_for(growth: float, multiple: float) -> float:
            value_at = intrinsic_value(
                fcf, growth, args.discount, multiple, args.years, cash, debt
            )["intrinsic_value"]
            return value_at - value_at * args.margin_of_safety

        grid = []
        for growth in SENSITIVITY_GROWTH:
            entries = {str(multiple): entry_for(growth, multiple) for multiple in SENSITIVITY_MULTIPLES}
            row = {"growth": growth, "entries": entries}
            if shares:
                row["entries_per_share"] = {
                    multiple: entry / shares for multiple, entry in entries.items()
                }
            grid.append(row)

        payload["sensitivity"] = {
            "note": "entry price after margin of safety, whole company",
            "growth_rates": list(SENSITIVITY_GROWTH),
            "terminal_multiples": list(SENSITIVITY_MULTIPLES),
            "grid": grid,
        }

    finlib.emit(payload)


if __name__ == "__main__":
    main()
