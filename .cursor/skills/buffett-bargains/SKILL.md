---
name: buffett-bargains
description: Runs a Buffett-style stock analysis from the "Find Buffett's Bargains" guide - a business one-pager from the 10-K, the nine moat categories, the Standout / Two Engine / Capital Efficiency tests, management integrity and capital allocation review, and a discounted cash flow intrinsic value with a margin of safety entry price. Use when the user asks to analyze a stock or ticker, check whether a company has a moat, compute gross margin / revenue growth / operating margin trend / ROIC, review a CEO or executive compensation, or estimate intrinsic value, fair value, or an entry price.
disable-model-invocation: true
---

# Find Buffett's Bargains

Four gates, run in order: understand the business, check for a moat, assess management, then value it. A company that fails an earlier gate is not rescued by a cheap price.

## Data sourcing policy

| Need | Source |
|------|--------|
| Multi-year revenue, margins, ROIC, debt, liquidity, DCF math | `scripts/` CLIs (SEC XBRL company facts) |
| Item 1 business description, Item 7 segment revenue and operating income | The 10-K itself (SEC EDGAR or a user-supplied file/link) |
| Shareholder letter, earnings call tone, MD&A, DEF 14A compensation | Primary documents via web research |
| Current share price, shares outstanding | Quote from web research, or `--price/--shares` passed by the user |

Never hand-wave a number that a script can compute, and never quote a number a script failed to produce. If the data is missing, the verdict is `Inconclusive` and the report says why. Aggregator blogs are not acceptable sources for any pass/fail metric; SEC filings and company IR pages are.

## Setup

Scripts need only the Python standard library. Run them from the repo root:

```bash
python3 .cursor/skills/buffett-bargains/scripts/fetch_metrics.py --ticker AAPL
```

Set a contact address once per session so SEC requests are not throttled:

```bash
export SEC_USER_AGENT="Your Name your@email.com"
```

The optional `yfinance` fallback (`--source yfinance`) requires `pip install -r requirements.txt`. Responses are cached for 24 hours under `~/.cache/buffett-bargains`; pass `--no-cache` to force a refresh.

## Workflow

Copy this checklist into the conversation and update it as you go:

```
- [ ] Step 0: Intake (ticker, peers, documents)
- [ ] Step 1: Quantitative baseline (fetch_metrics, compare_peers)
- [ ] Step 2: Understand the business (One-Pager)
- [ ] Step 3: Checking for a moat (qualitative + three tests)
- [ ] Step 4: Assess management (integrity + talent)
- [ ] Step 5: Valuation (DCF + margin of safety)
- [ ] Step 6: Synthesis and verdict
```

### Step 0: Intake

Confirm the ticker. Ask for peers only if the industry is ambiguous; otherwise propose 2 to 4 same-industry comparables and state the choice in the report. Accept any 10-K, shareholder letter, or DEF 14A the user provides rather than re-fetching it.

### Step 1: Quantitative baseline

```bash
python3 .cursor/skills/buffett-bargains/scripts/fetch_metrics.py --ticker AAPL --years 10
python3 .cursor/skills/buffett-bargains/scripts/compare_peers.py --ticker AAPL --peers MSFT,GOOGL,DELL
```

Both print JSON to stdout. `fetch_metrics.py` returns per-fiscal-year revenue, gross margin, operating margin, effective tax rate, NOPAT, equity, total debt, invested capital, ROIC, free cash flow, cash, current assets and liabilities, plus a `tests` block with a suggested verdict for each quantitative test and a `warnings` list. Treat `tests[*].verdict` as a starting point, not the final answer: read the series, and if a trend is driven by one anomalous year, say so.

Save both payloads to files when the analysis is long, so later steps quote exact figures:

```bash
python3 .cursor/skills/buffett-bargains/scripts/fetch_metrics.py --ticker AAPL --out /tmp/aapl_metrics.json
```

### Step 2: Understand the business

Read Part 1 Item 1 (business description) and Part 2 Item 7 (management's discussion, which carries segment revenue and operating income). Then write the One-Pager:

- What the company does
- One sentence summary of the business model
- Core business segments
- How the company makes money
- Most profitable segments
- Key strengths / potential competitive advantage
- Key risks or weak spots
- Growth levers
- Flow chart breaking down the segments and their revenue and operating profitability

If you cannot produce a coherent One-Pager, stop and put the business in the **"too hard" pile**. That is a valid final answer.

### Step 3: Checking for a moat

First qualitative, then quantitative.

**Qualitative test:** does the business model fit at least one of the nine moat categories: brand, switching, network effects, "secret sauce", toll, cost advantage, scale advantage, barrier to entry, monopoly. Name the category, cite the mechanism from Item 1, and judge the *durability* of the advantage, not just its existence. Definitions and examples are in [reference.md](reference.md).

**Quantitative tests** confirm or contradict the hypothesis:

| Test | Criterion | Source |
|------|-----------|--------|
| Standout | Gross margin exceeds same-industry competitors | `compare_peers.py` |
| Two Engine | Revenue growth at least 10% over the past 10 years, and operating margin improving or at least flat | `fetch_metrics.py` `tests.two_engine` |
| Capital Efficiency | ROIC at or above 15%, and improving over the past 10 years | `fetch_metrics.py` `tests.capital_efficiency` |

ROIC benchmarks: 15% or more is excellent and indicates a moat, 10% to 15% is solid, under 10% is weak with no competitive edge.

A moat hypothesis that all three tests contradict is wrong. Say so plainly instead of arguing around the numbers. One asymmetry is worth knowing: a cost advantage moat shows up as *low* gross margin held deliberately, so Costco failing the Standout Test against Walmart and Target is a feature of its model, not evidence against the moat. Explain that in the report rather than silently overriding the verdict.

### Step 4: Assess management

**Integrity** comes from prose, not ratios. Read the annual shareholder letter, the most recent earnings call, and the Business Description and Management's Discussion and Analysis sections. Ask: do they explain complex issues clearly or hide behind jargon; do they explain *why* results changed; is the outlook measured or just optimistic; are challenges acknowledged; do they take responsibility or blame external factors; after reading, do you trust them more or less. Quote at least one passage that drove the judgment.

**Compensation** is in SEC Form DEF 14A. Report how much the CEO earns in each category and what conditions trigger payment. Ownership stake plus long-term, multi-year performance-linked equity is good; little equity with a large salary and a one-year EPS cash bonus is bad.

**Talent** is capital allocation, judged in three areas:

1. Return on invested capital, from the Capital Efficiency test.
2. Dividends and buybacks. High ROIC means reinvestment beats payouts; a dividend is the right call only when the company cannot reinvest at attractive rates. Buybacks should happen only at a discount to intrinsic value. The worst outcome is reinvesting in low-return projects for the sake of growth.
3. Debt management, with two hard checks:

| Check | Formula | Criterion |
|-------|---------|-----------|
| Leverage | Total debt / shareholders' equity | Below 1 |
| Liquidity | Current assets / current liabilities | Above 1.5 |

Both come from `fetch_metrics.py` `tests.leverage` and `tests.liquidity`. High leverage is tolerable in a stable, cash-generating business and a red flag in a cyclical one, so state which case applies.

### Step 5: Valuation

Base free cash flow and the cash and debt adjustments come from `fetch_metrics.py`. Defaults follow the guide: 10% growth, 20x terminal multiple, 15% discount rate, 30% margin of safety.

```bash
python3 .cursor/skills/buffett-bargains/scripts/dcf_valuation.py \
  --fcf 98767000000 --growth 0.10 --discount 0.15 \
  --terminal-multiple 20 --years 10 \
  --cash 54697000000 --debt 98657000000 \
  --margin-of-safety 0.30 --shares 15004697000 --price 245.50
```

Use `--from-metrics /tmp/aapl_metrics.json` to pull `fcf`, `cash`, and `debt` straight from Step 1 instead of retyping them.

The 10% growth default is only legitimate when the Two Engine test and the business narrative support it. Override it otherwise, and state the override and its justification in the report. Same for the terminal multiple. Run `--sensitivity` to show entry prices across a growth and multiple grid when the estimate is fragile.

The six steps, formulas, and a worked example are in [valuation.md](valuation.md).

### Step 6: Synthesis and verdict

Fill in [templates/report.md](templates/report.md). Every quantitative claim cites the script output; every qualitative claim cites a filing, letter, or call.

## Verdict rules

| Outcome | Condition |
|---------|-----------|
| Too hard | The One-Pager could not be written |
| Pass | No moat category fits, or the quantitative tests contradict the moat, or management fails integrity |
| Watchlist | Business and management pass, but price is above the margin of safety entry price |
| Buy | All gates pass and price is at or below the margin of safety entry price |

State the entry price explicitly so the user knows what to wait for. The point is to wait patiently for a high-quality business at a steep discount, then swing hard.

## Known data limitations

- SEC company facts cover US filers. Foreign private issuers filing 20-F or 40-F have thinner tagging, and funds and trusts may return nothing. The script reports `source` and `warnings`; try `--source yfinance` as a fallback, which typically exposes only about four years of annual statements, making 10-year trend claims `Inconclusive`.
- Segment revenue and operating income are not reliably tagged in XBRL. Take them from Item 7 by hand.
- Companies that present no gross profit line (telecoms, banks, insurers) tag no cost of revenue, so the Standout Test cannot run for them. AT&T and Visa are both examples.
- XBRL tags change with accounting standards, so a 10-year series is sometimes stitched from more than one concept. The script does that deliberately, names the concepts in `concepts_used`, and warns that early and late years are not strictly comparable.
- Total debt is the sum of tagged short-term and long-term borrowings and excludes operating leases. Restated years resolve to the most recently filed value. When a year has no long-term debt tag at all, total debt, debt to equity, and ROIC come back as unavailable for that year rather than as an understated figure.
- Free cash flow is operating cash flow minus capital expenditures. Companies that tag capex unusually may show gaps; check `warnings`.
- The effective tax rate falls back to 21% for years where the tagged rate is missing or outside 0 to 50%, which happens in loss years. The count of affected years appears in `warnings`.
- ROIC uses invested capital as equity plus total debt, per the guide, so it is not comparable to definitions that net out cash.

## Additional resources

- [reference.md](reference.md) - the nine moat categories with examples, and the integrity and compensation rubrics
- [valuation.md](valuation.md) - the six DCF steps with formulas and a worked example
- [templates/report.md](templates/report.md) - the report skeleton
