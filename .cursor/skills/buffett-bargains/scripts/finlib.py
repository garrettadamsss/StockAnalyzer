"""Shared fundamentals layer for the buffett-bargains scripts.

Primary source is the SEC XBRL company facts API, which carries 10+ years of
annual figures, needs no API key, and no third-party package. `yfinance` is an
optional fallback for tickers the SEC does not cover well; it typically exposes
only about four years of annual statements.

Definitions follow the investing guide:
    NOPAT = Operating Income x (1 - Effective Tax Rate)
    Invested Capital = Equity + Total Debt
    ROIC = NOPAT / Invested Capital
Invested capital is therefore not netted down by cash.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")
CACHE_TTL_SECONDS = 24 * 3600

REVENUE = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
    "RevenuesNetOfInterestExpense",
)
COST_OF_REVENUE = (
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "CostOfGoodsSold",
    "CostOfServices",
)
OPERATING_INCOME = ("OperatingIncomeLoss",)
PRETAX_INCOME = (
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
)
TAX_EXPENSE = ("IncomeTaxExpenseBenefit",)
OPERATING_CASH_FLOW = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)
CAPEX = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsToAcquirePropertyPlantAndEquipmentExcludingInterestCapitalized",
)
EQUITY = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)
LONG_TERM_DEBT = (
    "LongTermDebtNoncurrent",
    "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebt",
)
CURRENT_DEBT_TOTAL = ("DebtCurrent",)
CURRENT_DEBT_PARTS = ("LongTermDebtCurrent", "CommercialPaper", "ShortTermBorrowings")
CASH = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
)
SHORT_TERM_INVESTMENTS = (
    "ShortTermInvestments",
    "MarketableSecuritiesCurrent",
    "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
    "OtherShortTermInvestments",
)
CURRENT_ASSETS = ("AssetsCurrent",)
CURRENT_LIABILITIES = ("LiabilitiesCurrent",)
DILUTED_SHARES = ("WeightedAverageNumberOfDilutedSharesOutstanding",)

DEFAULT_TAX_RATE = 0.21


class DataError(Exception):
    """Raised when fundamentals cannot be assembled for a ticker."""


# --- plumbing ---------------------------------------------------------------


def _user_agent() -> str:
    agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if agent:
        return agent
    return "buffett-bargains-skill (set SEC_USER_AGENT to your name and email)"


def _cache_dir() -> Path:
    root = os.environ.get("BUFFETT_CACHE_DIR")
    path = Path(root) if root else Path.home() / ".cache" / "buffett-bargains"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fetch_json(url: str, cache_key: Optional[str] = None, use_cache: bool = True) -> Any:
    cache_file = _cache_dir() / f"{cache_key}.json" if cache_key else None
    if cache_file and use_cache and cache_file.exists():
        if time.time() - cache_file.stat().st_mtime < CACHE_TTL_SECONDS:
            try:
                return json.loads(cache_file.read_text())
            except ValueError:
                cache_file.unlink(missing_ok=True)

    request = urllib.request.Request(url, headers={"User-Agent": _user_agent(), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise DataError(f"HTTP {exc.code} from {url}") from exc
    except urllib.error.URLError as exc:
        raise DataError(f"network error reaching {url}: {exc.reason}") from exc

    payload = json.loads(raw)
    if cache_file:
        cache_file.write_text(raw)
    return payload


def emit(payload: Dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def fail(message: str, partial: Optional[Dict[str, Any]] = None) -> None:
    emit({"error": message, "partial": partial or {}})
    sys.exit(1)


# --- SEC extraction ---------------------------------------------------------


def resolve_cik(ticker: str, use_cache: bool = True) -> Tuple[str, str]:
    """Return (zero-padded CIK, company title) for a ticker symbol."""
    directory = fetch_json(SEC_TICKERS_URL, cache_key="company_tickers", use_cache=use_cache)
    wanted = ticker.upper().replace(".", "-")
    for entry in directory.values():
        if entry.get("ticker", "").upper() == wanted:
            return str(entry["cik_str"]).zfill(10), entry.get("title", "")
    raise DataError(f"ticker {ticker} not found in the SEC ticker directory (foreign issuer or fund?)")


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def _concept_series(
    facts: Dict[str, Any],
    concept: str,
    *,
    taxonomy: str = "us-gaap",
    unit: str = "USD",
    duration: bool,
) -> Dict[str, float]:
    """Annual values for one concept, keyed by period end date.

    Restated periods resolve to the most recently filed value.
    """
    node = facts.get("facts", {}).get(taxonomy, {}).get(concept)
    if not node:
        return {}
    entries = node.get("units", {}).get(unit)
    if not entries:
        return {}

    best: Dict[str, Tuple[str, float]] = {}
    for entry in entries:
        if entry.get("form") not in ANNUAL_FORMS:
            continue
        end = entry.get("end")
        value = entry.get("val")
        if end is None or value is None:
            continue
        if duration:
            start = entry.get("start")
            if not start:
                continue
            span = (_parse_date(end) - _parse_date(start)).days
            if not 330 <= span <= 400:
                continue
        elif entry.get("start"):
            continue
        filed = entry.get("filed", "")
        previous = best.get(end)
        if previous is None or filed >= previous[0]:
            best[end] = (filed, float(value))

    return {end: value for end, (_, value) in best.items()}


def _merge(
    facts: Dict[str, Any],
    concepts: Sequence[str],
    *,
    duration: bool,
    unit: str = "USD",
) -> Tuple[Dict[str, float], Dict[str, str]]:
    """Highest-priority concept first, with other concepts filling missing years.

    Returns the merged series and, for each period end, the concept it came from.
    Tags change across accounting standards (ASC 606 replaced SalesRevenueNet,
    for example), so a single concept often covers only part of a 10 year window.
    """
    merged: Dict[str, float] = {}
    provenance: Dict[str, str] = {}
    for concept in concepts:
        series = _concept_series(facts, concept, unit=unit, duration=duration)
        for end, value in series.items():
            if end not in merged:
                merged[end] = value
                provenance[end] = concept
    return merged, provenance


def _nearest_key(series: Dict[str, Any], target: str, tolerance_days: int = 21) -> Optional[str]:
    """Key whose period end is closest to `target`, within a tolerance."""
    if not series:
        return None
    if target in series:
        return target
    target_date = _parse_date(target)
    closest = min(series, key=lambda end: abs((_parse_date(end) - target_date).days))
    if abs((_parse_date(closest) - target_date).days) <= tolerance_days:
        return closest
    return None


def _nearest(series: Dict[str, float], target: str, tolerance_days: int = 21) -> Optional[float]:
    """Value whose period end is closest to `target`, within a tolerance."""
    key = _nearest_key(series, target, tolerance_days)
    return series[key] if key is not None else None


def _concepts_in_window(provenance: Dict[str, str], period_ends: Sequence[str]) -> List[str]:
    """Concepts that supplied values for the reported period ends, in order."""
    ordered: List[str] = []
    for end in period_ends:
        match = _nearest_key(provenance, end)
        concept = provenance.get(match) if match else None
        if concept and concept not in ordered:
            ordered.append(concept)
    return ordered


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _sum_present(values: Iterable[Optional[float]]) -> Optional[float]:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _effective_tax_rate(tax: Optional[float], pretax: Optional[float]) -> Tuple[float, bool]:
    """Effective rate clamped to a sane band, plus whether the default was used."""
    rate = _ratio(tax, pretax)
    if rate is None or not 0.0 <= rate <= 0.5:
        return DEFAULT_TAX_RATE, True
    return rate, False


def build_from_sec(ticker: str, years: int = 10, use_cache: bool = True) -> Dict[str, Any]:
    cik, title = resolve_cik(ticker, use_cache=use_cache)
    facts = fetch_json(
        SEC_FACTS_URL.format(cik=cik), cache_key=f"facts_{cik}", use_cache=use_cache
    )
    warnings: List[str] = []

    revenue, revenue_concepts = _merge(facts, REVENUE, duration=True)
    if not revenue:
        raise DataError(f"no annual revenue concept tagged for {ticker} (CIK {cik})")

    cost_of_revenue, cost_concepts = _merge(facts, COST_OF_REVENUE, duration=True)
    operating_income, operating_income_concepts = _merge(facts, OPERATING_INCOME, duration=True)
    pretax, _ = _merge(facts, PRETAX_INCOME, duration=True)
    tax, _ = _merge(facts, TAX_EXPENSE, duration=True)
    ocf, _ = _merge(facts, OPERATING_CASH_FLOW, duration=True)
    capex, capex_concepts = _merge(facts, CAPEX, duration=True)
    diluted_shares, _ = _merge(facts, DILUTED_SHARES, duration=True, unit="shares")

    equity, equity_concepts = _merge(facts, EQUITY, duration=False)
    long_term_debt, lt_concepts = _merge(facts, LONG_TERM_DEBT, duration=False)
    current_debt, current_debt_concepts = _merge(facts, CURRENT_DEBT_TOTAL, duration=False)
    current_debt_parts = [
        _concept_series(facts, concept, duration=False) for concept in CURRENT_DEBT_PARTS
    ]
    cash, _ = _merge(facts, CASH, duration=False)
    short_term_investments, _ = _merge(facts, SHORT_TERM_INVESTMENTS, duration=False)
    current_assets, _ = _merge(facts, CURRENT_ASSETS, duration=False)
    current_liabilities, _ = _merge(facts, CURRENT_LIABILITIES, duration=False)

    any_debt_tagged = bool(long_term_debt or current_debt or any(current_debt_parts))
    if not any_debt_tagged:
        warnings.append("no debt concepts tagged; treating the company as debt free for invested capital")

    rows: List[Dict[str, Any]] = []
    period_ends = sorted(revenue)[-years:]
    defaulted_tax_years = 0
    debt_gap_years: List[str] = []
    long_term_debt_reported = any(_nearest(long_term_debt, end) is not None for end in period_ends)

    for end in period_ends:
        year_revenue = revenue[end]
        year_cost = _nearest(cost_of_revenue, end)
        year_operating_income = _nearest(operating_income, end)
        year_tax_rate, tax_defaulted = _effective_tax_rate(_nearest(tax, end), _nearest(pretax, end))
        defaulted_tax_years += 1 if tax_defaulted else 0

        year_equity = _nearest(equity, end)
        year_current_debt = _nearest(current_debt, end)
        if year_current_debt is None:
            year_current_debt = _sum_present(_nearest(part, end) for part in current_debt_parts)
        year_long_term_debt = _nearest(long_term_debt, end)
        if year_long_term_debt is None and long_term_debt_reported:
            # A current-debt-only total would understate leverage badly, so
            # report nothing rather than a partial figure.
            debt_gap_years.append(end)
            total_debt = None
        else:
            total_debt = _sum_present([year_current_debt, year_long_term_debt])

        nopat = year_operating_income * (1 - year_tax_rate) if year_operating_income is not None else None
        if year_equity is None or (total_debt is None and any_debt_tagged):
            invested_capital = None
        else:
            invested_capital = year_equity + (total_debt or 0.0)

        year_ocf = _nearest(ocf, end)
        year_capex = _nearest(capex, end)
        if year_ocf is None:
            free_cash_flow = None
        elif year_capex is None:
            free_cash_flow = year_ocf
        else:
            free_cash_flow = year_ocf - abs(year_capex)

        year_cash = _nearest(cash, end)
        year_investments = _nearest(short_term_investments, end)

        rows.append(
            {
                "period_end": end,
                "revenue": year_revenue,
                "cost_of_revenue": year_cost,
                "gross_margin": _ratio(year_revenue - year_cost, year_revenue) if year_cost is not None else None,
                "operating_income": year_operating_income,
                "operating_margin": _ratio(year_operating_income, year_revenue),
                "effective_tax_rate": year_tax_rate,
                "nopat": nopat,
                "equity": year_equity,
                "total_debt": total_debt,
                "invested_capital": invested_capital,
                "roic": _ratio(nopat, invested_capital),
                "operating_cash_flow": year_ocf,
                "capital_expenditures": abs(year_capex) if year_capex is not None else None,
                "free_cash_flow": free_cash_flow,
                "cash_and_equivalents": year_cash,
                "short_term_investments": year_investments,
                "cash_and_investments": _sum_present([year_cash, year_investments]),
                "current_assets": _nearest(current_assets, end),
                "current_liabilities": _nearest(current_liabilities, end),
                "debt_to_equity": _ratio(total_debt, year_equity),
                "current_ratio": _ratio(_nearest(current_assets, end), _nearest(current_liabilities, end)),
                "diluted_shares": _nearest(diluted_shares, end),
            }
        )

    concepts_used = {
        "revenue": _concepts_in_window(revenue_concepts, period_ends),
        "cost_of_revenue": _concepts_in_window(cost_concepts, period_ends),
        "operating_income": _concepts_in_window(operating_income_concepts, period_ends),
        "capital_expenditures": _concepts_in_window(capex_concepts, period_ends),
        "equity": _concepts_in_window(equity_concepts, period_ends),
        "long_term_debt": _concepts_in_window(lt_concepts, period_ends),
        "current_debt": _concepts_in_window(current_debt_concepts, period_ends)
        or ["sum of " + ", ".join(CURRENT_DEBT_PARTS)],
    }
    unavailable = {
        "cost_of_revenue": "gross margin is unavailable, so the Standout Test cannot run "
        "(common for telecoms, banks, and insurers that present no gross profit line)",
        "operating_income": "operating margin and ROIC are unavailable",
        "capital_expenditures": "free cash flow falls back to operating cash flow",
        "equity": "ROIC and debt to equity are unavailable",
    }
    for field, consequence in unavailable.items():
        if not concepts_used[field]:
            warnings.append(
                f"no {field.replace('_', ' ')} concept tagged for the reported years; {consequence}"
            )
    for label, concepts in concepts_used.items():
        if len(concepts) > 1:
            warnings.append(
                f"{label.replace('_', ' ')} history spans multiple XBRL concepts "
                f"({', '.join(concepts)}); definitions changed part way through the period, "
                "so early and late years are not strictly comparable"
            )
    if debt_gap_years:
        warnings.append(
            "long term debt is untagged for " + ", ".join(debt_gap_years) + "; total debt, "
            "debt to equity, and ROIC are reported as unavailable for those years rather than "
            "understated"
        )
    if defaulted_tax_years:
        warnings.append(
            f"effective tax rate defaulted to {DEFAULT_TAX_RATE:.0%} in {defaulted_tax_years} of "
            f"{len(rows)} years because tagged tax or pretax income was missing or implausible"
        )
    if len(rows) < years:
        warnings.append(f"only {len(rows)} annual periods available, requested {years}")

    return {
        "ticker": ticker.upper(),
        "company": title,
        "cik": cik,
        "source": "sec-companyfacts",
        "concepts_used": concepts_used,
        "fiscal_years": rows,
        "warnings": warnings,
    }


# --- yfinance fallback ------------------------------------------------------


def build_from_yfinance(ticker: str, years: int = 10) -> Dict[str, Any]:
    try:
        import yfinance  # noqa: PLC0415 - optional dependency
    except ImportError as exc:
        raise DataError(
            "yfinance is not installed; run `pip install -r requirements.txt` or use --source sec"
        ) from exc

    handle = yfinance.Ticker(ticker)
    income = handle.income_stmt
    balance = handle.balance_sheet
    cashflow = handle.cashflow
    if income is None or income.empty:
        raise DataError(f"yfinance returned no annual income statement for {ticker}")

    def grab(frame: Any, labels: Sequence[str], column: Any) -> Optional[float]:
        if frame is None or frame.empty:
            return None
        for label in labels:
            if label in frame.index:
                value = frame.loc[label, column] if column in frame.columns else None
                if value is not None and value == value:  # filters NaN
                    return float(value)
        return None

    rows: List[Dict[str, Any]] = []
    for column in sorted(income.columns)[-years:]:
        end = str(getattr(column, "date", lambda: column)())[:10]
        revenue = grab(income, ["Total Revenue", "Operating Revenue"], column)
        if revenue in (None, 0):
            continue
        cost = grab(income, ["Cost Of Revenue", "Reconciled Cost Of Revenue"], column)
        operating_income = grab(income, ["Operating Income", "EBIT"], column)
        tax = grab(income, ["Tax Provision"], column)
        pretax = grab(income, ["Pretax Income"], column)
        tax_rate, _ = _effective_tax_rate(tax, pretax)

        equity_value = grab(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"], column)
        total_debt = grab(balance, ["Total Debt"], column)
        if total_debt is None:
            total_debt = _sum_present(
                [grab(balance, ["Long Term Debt"], column), grab(balance, ["Current Debt"], column)]
            )
        nopat = operating_income * (1 - tax_rate) if operating_income is not None else None
        invested_capital = _sum_present([equity_value, total_debt]) if equity_value is not None else None

        ocf_value = grab(cashflow, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"], column)
        capex_value = grab(cashflow, ["Capital Expenditure"], column)
        free_cash_flow = None
        if ocf_value is not None:
            free_cash_flow = ocf_value - abs(capex_value) if capex_value is not None else ocf_value

        current_assets_value = grab(balance, ["Current Assets"], column)
        current_liabilities_value = grab(balance, ["Current Liabilities"], column)
        cash_value = grab(balance, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"], column)
        investments_value = grab(balance, ["Other Short Term Investments"], column)

        rows.append(
            {
                "period_end": end,
                "revenue": revenue,
                "cost_of_revenue": cost,
                "gross_margin": _ratio(revenue - cost, revenue) if cost is not None else None,
                "operating_income": operating_income,
                "operating_margin": _ratio(operating_income, revenue),
                "effective_tax_rate": tax_rate,
                "nopat": nopat,
                "equity": equity_value,
                "total_debt": total_debt,
                "invested_capital": invested_capital,
                "roic": _ratio(nopat, invested_capital),
                "operating_cash_flow": ocf_value,
                "capital_expenditures": abs(capex_value) if capex_value is not None else None,
                "free_cash_flow": free_cash_flow,
                "cash_and_equivalents": cash_value,
                "short_term_investments": investments_value,
                "cash_and_investments": _sum_present([cash_value, investments_value]),
                "current_assets": current_assets_value,
                "current_liabilities": current_liabilities_value,
                "debt_to_equity": _ratio(total_debt, equity_value),
                "current_ratio": _ratio(current_assets_value, current_liabilities_value),
                "diluted_shares": grab(income, ["Diluted Average Shares"], column),
            }
        )

    if not rows:
        raise DataError(f"yfinance returned no usable annual periods for {ticker}")

    return {
        "ticker": ticker.upper(),
        "company": str(handle.info.get("longName", "")) if hasattr(handle, "info") else "",
        "cik": None,
        "source": "yfinance",
        "concepts_used": {},
        "fiscal_years": rows,
        "warnings": [
            "yfinance annual statements usually cover about four years, so 10-year trend claims are inconclusive"
        ],
    }


def build_fundamentals(ticker: str, years: int = 10, source: str = "sec", use_cache: bool = True) -> Dict[str, Any]:
    if source == "yfinance":
        return build_from_yfinance(ticker, years)
    if source == "sec":
        return build_from_sec(ticker, years, use_cache=use_cache)
    raise DataError(f"unknown source {source!r}; expected 'sec' or 'yfinance'")


# --- test scoring -----------------------------------------------------------


def _series(rows: Sequence[Dict[str, Any]], field: str) -> List[float]:
    return [row[field] for row in rows if row.get(field) is not None]


def average(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def trend(values: Sequence[float], tolerance: float) -> Optional[str]:
    """Compare the mean of the last three observations against the first three."""
    if len(values) < 4:
        return None
    head = average(values[:3])
    tail = average(values[-3:])
    if head is None or tail is None:
        return None
    if tail - head > tolerance:
        return "improving"
    if head - tail > tolerance:
        return "declining"
    return "flat"


def cagr(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2 or values[0] <= 0:
        return None
    periods = len(values) - 1
    return (values[-1] / values[0]) ** (1 / periods) - 1


def roic_band(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    if value >= 0.15:
        return "excellent, indicates a moat"
    if value >= 0.10:
        return "solid"
    return "weak, no competitive edge"


def score_tests(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Suggested verdicts for the guide's quantitative criteria."""
    revenues = _series(rows, "revenue")
    operating_margins = _series(rows, "operating_margin")
    roics = _series(rows, "roic")
    latest = rows[-1] if rows else {}
    thin_history = len(rows) < 8

    revenue_cagr = cagr(revenues)
    margin_trend = trend(operating_margins, tolerance=0.005)
    revenue_leg = revenue_cagr is not None and revenue_cagr >= 0.10
    margin_leg = margin_trend in ("improving", "flat")

    two_engine_notes: List[str] = []
    if revenue_cagr is None:
        two_engine_verdict = "inconclusive"
        two_engine_notes.append("revenue history too short to compute a growth rate")
    elif margin_trend is None:
        two_engine_verdict = "inconclusive"
        two_engine_notes.append("fewer than four years of operating margin, trend undetermined")
    elif revenue_leg and margin_leg:
        two_engine_verdict = "pass"
    else:
        two_engine_verdict = "fail"
        if not revenue_leg:
            two_engine_notes.append(f"revenue growth {revenue_cagr:.1%} is below the 10% criterion")
        if not margin_leg:
            two_engine_notes.append("operating margin is declining")
    if thin_history and two_engine_verdict != "inconclusive":
        two_engine_notes.append(f"based on {len(rows)} years rather than 10; treat the trend as provisional")

    latest_roic = latest.get("roic")
    roic_trend = trend(roics, tolerance=0.01)
    capital_notes: List[str] = []
    if latest_roic is None or len(roics) < 4:
        capital_verdict = "inconclusive"
        capital_notes.append("fewer than four years of ROIC available")
    elif latest_roic >= 0.15 and roic_trend in ("improving", "flat"):
        capital_verdict = "pass"
        if roic_trend == "flat":
            capital_notes.append("ROIC clears 15% but is flat rather than improving")
    else:
        capital_verdict = "fail"
        if latest_roic < 0.15:
            capital_notes.append(f"latest ROIC {latest_roic:.1%} is below the 15% criterion")
        if roic_trend == "declining":
            capital_notes.append("ROIC is declining over the period")
    if thin_history and capital_verdict != "inconclusive":
        capital_notes.append(f"based on {len(roics)} years rather than 10; treat the trend as provisional")

    debt_to_equity = latest.get("debt_to_equity")
    current_ratio = latest.get("current_ratio")

    return {
        "two_engine": {
            "criterion": "revenue growth >= 10% over 10 years and operating margin flat or improving",
            "years": len(rows),
            "revenue_cagr": revenue_cagr,
            "revenue_first": revenues[0] if revenues else None,
            "revenue_last": revenues[-1] if revenues else None,
            "operating_margin_first3_avg": average(operating_margins[:3]),
            "operating_margin_last3_avg": average(operating_margins[-3:]),
            "operating_margin_trend": margin_trend,
            "verdict": two_engine_verdict,
            "notes": two_engine_notes,
        },
        "capital_efficiency": {
            "criterion": "ROIC >= 15% and improving over 10 years",
            "years": len(roics),
            "latest_roic": latest_roic,
            "roic_3y_avg": average(roics[-3:]),
            "roic_first3_avg": average(roics[:3]),
            "roic_trend": roic_trend,
            "band": roic_band(latest_roic),
            "verdict": capital_verdict,
            "notes": capital_notes,
        },
        "leverage": {
            "criterion": "debt to equity below 1",
            "debt_to_equity": debt_to_equity,
            "total_debt": latest.get("total_debt"),
            "equity": latest.get("equity"),
            "verdict": "inconclusive" if debt_to_equity is None else ("pass" if debt_to_equity < 1 else "fail"),
            "notes": ["high leverage is tolerable in stable cash-generating businesses, a red flag in cyclical ones"],
        },
        "liquidity": {
            "criterion": "current ratio above 1.5",
            "current_ratio": current_ratio,
            "current_assets": latest.get("current_assets"),
            "current_liabilities": latest.get("current_liabilities"),
            "verdict": "inconclusive" if current_ratio is None else ("pass" if current_ratio > 1.5 else "fail"),
            "notes": [],
        },
    }
