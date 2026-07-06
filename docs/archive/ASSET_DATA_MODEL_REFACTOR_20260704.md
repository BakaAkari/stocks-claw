# Asset Data Model Refactor Design - 2026-07-04

> ARCHIVED / SUPERSEDED: this research draft led to S2. The active schema is
> `stocks/DATA_MODEL.md`; the active task status is `EXECUTION_PLAN.md`.
> Do not treat this document as a current contract.

> Status: research/design artifact. This document is not an active contract.
> Current contracts remain `stocks/DATA_MODEL.md`, `ARCHITECTURE.md`,
> `AGENT_GUIDE.md`, and the code. The purpose here is to decide what personal
> holding facts deserve structure, what should be derived, and what should be
> left out before a future implementation slice is opened.

## 1. Background

The current system has moved from a market-context toolkit toward a personal
investment analyst system. Its useful output depends on one thing the market
data layer cannot infer: the user's real portfolio constraints and exposures.

The current `FinancialAsset` model is intentionally thin:

- identity: `name`, `platform`
- value: `amount`, `currency`, runtime `amount_cny`
- classification: `asset_type`
- optional holding mapping: `instrument_key`, `quantity`, `tradable`
- free text: `notes`

That model was enough for the first "minimum holding mapping" slice. It is not
enough for the next level of advice because the sanitized portfolio sample
contains multiple valuation methods, liquidity regimes, and exposure overlaps:

- exchange-traded A-share ETFs
- US stocks and ETFs
- US short Treasury ETF used like cash but not actually cash
- CNY OTC funds and QDII feeder funds with NAV delay
- money-market products and bank wealth-management products
- bank precious metals without complete gram/cost data
- USD insurance with low liquidity and different usable-value concepts

The refactor goal is not to create an accounting system. The goal is a compact
financial memory schema that gives the analyst enough structure to answer:

- What can move?
- What cannot move?
- What market exposure does each position create?
- Which facts are source-of-truth user memory?
- Which facts should be recomputed from market data each run?
- Which missing fields should block or weaken advice?

## 2. Design Principles

1. Persist only source facts or user-confirmed facts.
   Prices, market values, PnL, weights, exposure totals, and CNY conversions
   should be derived when sufficient inputs exist.

2. Split "what it is" from "what it behaves like".
   A product can be a stock ETF, gold-linked exposure, liquid, non-tradable, or
   a cash buffer independently. One `asset_type` string cannot carry all of that.

3. Make liquidity first-class.
   A personal analyst should not treat insurance, bank wealth products,
   money-market funds, SGOV, and cash as one interchangeable cash bucket.

4. Keep the first implementation as JSON, not a database.
   The project is for one user. A single versioned local JSON memory can support
   the next slice without SQL, ORM, or platform architecture.

5. Do not parse free-text notes into financial facts automatically.
   Historical `notes` can preserve context, but any cost, quantity, code, lockup,
   or liquidity field extracted from notes must be confirmed by the user.

6. Separate user memory from runtime snapshots.
   The memory file stores durable facts. Runtime context can contain calculated
   valuation snapshots with `as_of`, source, freshness, and data-quality flags.

## 3. What The Sample Data Says

### 3.1 Data That Deserves Structure

These facts directly change advice and cannot be recovered reliably from market
data:

| Fact | Why it matters | Proposed location |
|---|---|---|
| Account/platform | Determines funding source, currency, transfer friction, and execution venue | `accounts[]`, `position.account_id` |
| Currency | Required for CNY base reporting and FX risk | `position.currency` |
| Instrument code and market | Enables quote/history/news/event linkage | `position.instrument` |
| Quantity or shares | Required for market-value and PnL calculation | `holding.quantity`, `holding.unit` |
| Cost basis | Required for unrealized PnL and loss-source analysis | `holding.cost_basis` |
| Product type | Distinguishes ETF, stock, OTC fund, WMP, insurance, precious metal, cash | `classification.product_type` |
| Economic asset class | Drives portfolio buckets and drift checks | `classification.asset_class` |
| Exposure tags | Captures overlap across wrappers: gold, Nasdaq, AI, energy, defense, USD rates | `classification.exposure_tags` |
| Tradability and rebalance eligibility | Prevents suggesting actions on assets that cannot or should not move | `liquidity.tradable`, `liquidity.rebalance_eligible` |
| Liquidity tier and redemption rule | Separates T+0 cash, T+1 funds, holding-period products, insurance | `liquidity.*` |
| Manual valuation and valuation method | Needed for products without quote/NAV coverage | `valuation_input.*` |
| Broker-reported PnL, when not computable | Useful for OTC funds, WMP, bank gold, until cost/quantity data exists | `reported_performance.*` |
| Missing fields | Lets the agent explain why advice is blocked or lower-confidence | `data_completeness.missing_fields` |

### 3.2 Data That Should Be Derived

These should not be persisted as core holding facts unless they are a broker
snapshot for reconciliation:

| Derived data | Derivation |
|---|---|
| Current price | Quote provider, NAV provider, or configured manual valuation source |
| Current market value | `quantity * current_price`, or latest NAV/position snapshot |
| CNY value | source value × FX rate with explicit FX source |
| Unrealized PnL | current value minus cost amount |
| PnL ratio | PnL / cost amount |
| Account total | sum of positions and cash in the account |
| Total portfolio value | sum of all included CNY valuations |
| Account weight | position CNY value / account CNY total |
| Portfolio weight | position CNY value / portfolio CNY total |
| Exposure total | sum by normalized exposure tags |
| "Main profit/loss source" | rank by PnL or contribution |
| "Cash ratio high/low" | derived from classified liquid/rebalance-eligible assets |
| Gold total and Nasdaq total | sum by exposure tags, not manually maintained |

The sample contains many manually written values like "current market value
needs quote", "current price needs quote", and "PnL should be calculated". These
are instructions for the system, not persisted fields.

### 3.3 Data To Keep Only As Notes Or Profile

These are useful for human interpretation but should not drive deterministic
logic until promoted into explicit fields:

- "small base position"
- "major profit source"
- "major loss source"
- "A-share account is not a large allocation"
- "cash ratio is high"
- "gold exposure is high"
- "QDII NAV may be delayed"
- "correlation with gold is high"

Some of these can become derived labels. They should not be source facts.

### 3.4 Data To Omit From The Persistent Schema

These create duplication or drift:

- repeated account totals when holdings can sum to account total
- manually entered current prices for listed instruments
- manually entered current market values for listed instruments when quantity is known
- manually entered PnL for listed instruments when quantity and cost are known
- broad narrative summaries that duplicate structured classification
- repeated "needs quote" or "needs NAV" text per position

If the broker has a different value from the system's computed value, store it
as a dated reconciliation snapshot, not as the canonical holding fact.

## 4. Proposed Thin Persistent Model

Keep a single local JSON file for the first refactor slice:

```json
{
  "schema_version": 2,
  "base_currency": "CNY",
  "accounts": [],
  "positions": []
}
```

### 4.1 Account

```json
{
  "account_id": "cn_broker_a",
  "display_name": "A股证券账户",
  "institution_type": "brokerage",
  "market_scope": ["a"],
  "base_currency": "CNY",
  "default_liquidity_tier": "t0",
  "notes": null
}
```

Recommended fields:

| Field | Required | Notes |
|---|---:|---|
| `account_id` | yes | Stable local id, not an external account number |
| `display_name` | yes | Human-readable, can stay desensitized |
| `institution_type` | yes | `brokerage`, `fund_platform`, `bank`, `insurance`, `manual` |
| `market_scope` | no | `a`, `us`, `fund_cn`, `bank_cn`, etc. |
| `base_currency` | yes | Account reporting currency |
| `default_liquidity_tier` | no | Default only; position can override |
| `notes` | no | Non-driving text |

Do not store real account numbers unless a future connector explicitly needs
them and the user confirms the security boundary.

### 4.2 Position

```json
{
  "position_id": "cn_broker_a_510300",
  "account_id": "cn_broker_a",
  "display_name": "沪深300ETF",
  "currency": "CNY",
  "classification": {
    "asset_class": "equity",
    "product_type": "exchange_traded_fund",
    "subtype": "broad_index_etf",
    "exposure_tags": ["cn_equity", "csi300", "broad_index"]
  },
  "instrument": {
    "instrument_key": "a:510300",
    "market": "a",
    "code": "510300",
    "exchange": "sh",
    "name": "沪深300ETF",
    "quote_kind": "exchange_quote"
  },
  "holding": {
    "quantity": 2100,
    "unit": "share",
    "cost_basis": {
      "method": "average",
      "unit_cost": 4.796,
      "cost_amount": 10071.6,
      "currency": "CNY"
    }
  },
  "valuation_input": {
    "method": "market_quote"
  },
  "liquidity": {
    "tradable": true,
    "rebalance_eligible": true,
    "tier": "t0",
    "redemption_rule": null,
    "lockup_until": null,
    "maturity_date": null
  },
  "role": "core",
  "reported_performance": null,
  "data_completeness": {
    "missing_fields": []
  },
  "notes": null
}
```

The same shape can represent cash and manual assets by changing `instrument`,
`holding`, and `valuation_input`.

### 4.3 Classification Vocabulary

Use controlled fields, not one free-form `asset_type`.

`asset_class`:

- `cash`
- `cash_equivalent`
- `fixed_income`
- `equity`
- `commodity`
- `alternative`
- `insurance`
- `unknown`

`product_type`:

- `cash`
- `money_market_fund`
- `bank_wealth_management`
- `bond_fund`
- `mixed_fund`
- `qdii_fund`
- `exchange_traded_fund`
- `stock`
- `short_treasury_etf`
- `precious_metal_account`
- `gold_linked_fund`
- `insurance_policy`
- `manual_asset`

`exposure_tags` should be normalized but extensible. Initial tags from the
sample:

- geography/currency: `cn`, `us`, `usd`, `cny`
- broad assets: `cn_equity`, `us_equity`, `fixed_income`, `cash_buffer`
- sectors/themes: `energy`, `defense_aerospace`, `ai`, `semiconductor`,
  `tech_growth`, `information_technology`, `utilities_power`
- index exposures: `csi300`, `dividend_low_vol`, `star50`, `nasdaq100`
- gold-related: `gold`, `gold_miner`, `precious_metals`
- rates/cash-like: `short_treasury`, `money_market`, `bank_wmp`
- liquidity/risk overlays: `low_volatility`, `locked`, `qdii_delayed_nav`

The important point is that wrappers and exposures are independent. NEM is a
US stock and gold-miner exposure. A gold feeder fund is a CNY OTC fund and gold
exposure. Bank gold is a manual/bank asset and gold exposure.

### 4.4 Valuation Input

`valuation_input.method` tells the engine how to value a position:

| Method | Use case | Required user facts |
|---|---|---|
| `market_quote` | Listed stocks/ETFs | `instrument_key`, `quantity`, cost optional but recommended |
| `fund_nav` | OTC funds, QDII feeder funds | fund code, shares, cost NAV |
| `manual_amount` | Cash, bank products without NAV, incomplete fund data | manual value, as-of |
| `precious_metal_quote` | Bank gold if grams and cost are known | metal type, grams, quote source |
| `insurance_value` | Insurance/policy assets | cash value or surrender value, as-of |
| `broker_snapshot` | Temporary reconciliation when broker reports value/PnL | reported value, as-of, source |

Manual values must carry `as_of`; stale manual values should degrade data
quality and restrict advice.

### 4.5 Liquidity

Recommended controlled values:

`tier`:

- `cash`
- `t0`
- `t1`
- `t2_plus`
- `periodic_open`
- `locked`
- `unknown`

Fields:

| Field | Meaning |
|---|---|
| `tradable` | Can this be bought/sold through a market or platform? |
| `rebalance_eligible` | May the analyst suggest using this as source/target? |
| `tier` | Practical access speed |
| `redemption_rule` | Human-readable rule for funds/WMP/insurance |
| `lockup_until` | Date before which capital should not be assumed available |
| `maturity_date` | Fixed maturity or policy milestone |

Insurance should default to `rebalance_eligible=false` and `tier=locked` unless
the user explicitly provides a cash-value and withdrawal rule.

## 5. Mapping The Sanitized Sample

### 5.1 A-Share Brokerage Account

Positions:

- `a:510300`: exchange-traded fund, broad index, keep quantity and cost basis,
  derive price/value/PnL.
- `a:512890`: exchange-traded fund, dividend/low-vol exposure, keep quantity and
  cost basis, derive price/value/PnL.
- `a:561560`: exchange-traded fund, power/utilities exposure, keep quantity and
  cost basis, derive price/value/PnL.
- `a:588000`: exchange-traded fund, STAR50/technology exposure, keep quantity
  and cost basis, derive price/value/PnL.
- A-share available cash: cash position, manual amount, high liquidity, no
  instrument.

Keep:

- account id and account currency
- `instrument_key`, exchange, quantity, cost basis
- cash as its own position

Derive:

- current price
- market value
- PnL
- account total
- account cash ratio
- portfolio weight

Omit:

- "account not a large allocation" as a stored fact
- "red dividend/power dominate the account" as a stored fact; derive from
  position weights and tags

### 5.2 USD Brokerage Account

Positions:

- `us:ITA`: ETF, defense/aerospace exposure
- `us:NEM`: stock, gold-miner/gold-related exposure
- `us:NVDA`: stock, AI/semiconductor/tech exposure
- `us:SGOV`: short Treasury ETF, cash-equivalent/fixed-income exposure
- `us:XLE`: ETF, energy exposure
- USD cash: cash position, manual amount

Keep:

- USD currency
- exchange when known
- quantity and cost basis
- position-level exposure tags
- SGOV as `short_treasury_etf`, not cash

Derive:

- current price
- USD market value
- CNY value through FX
- PnL and PnL contribution
- "main profit/loss source"
- "USD cash ratio low"

Broker-reported PnL can be kept only as `reported_performance` if reconciliation
against computed PnL is useful. It should not replace computed PnL for listed
positions.

### 5.3 China OTC Fund Platform

Positions:

- money-market fund / Yu'ebao: manual amount, high liquidity, cash-equivalent.
- fixed-income-plus fund: manual amount until fund code, shares, cost NAV and
  NAV source are provided.
- Nasdaq QDII feeder funds: manual amount plus `nasdaq100`, `us_equity`,
  `qdii_delayed_nav`; upgrade to `fund_nav` when fund code and shares exist.
- gold ETF feeder fund: manual amount plus `gold`; upgrade to `fund_nav` when
  fund code and shares exist.
- active information-industry fund: manual amount plus `tech_growth` or
  `information_technology`; upgrade to `fund_nav` when fund code and shares exist.

Keep:

- display name
- platform account
- manual amount and as-of
- product type
- exposure tags
- reported profit only while cost/share data is missing
- known holding-period limitation if provided

Derive only after upgrade:

- NAV-based current value
- PnL from shares and cost NAV
- stale NAV warnings

Omit:

- duplicate "current only by amount" text; this is represented by
  `valuation_input.method=manual_amount`.

### 5.4 Bank Account

Positions:

- demand deposit: cash, manual amount, high liquidity.
- bank wealth-management product: bank WMP, manual amount, reported profit,
  missing redemption rule/risk level/open date.
- cash-management product / money-market fund: cash-equivalent, manual amount,
  reported profit.
- bank precious metals/gold: commodity/manual asset, gold exposure, manual
  value and reported loss until grams and cost price are provided.

Keep:

- product category
- manual value and value as-of
- reported profit/loss if not computable
- missing fields for redemption, risk level, NAV, grams, cost price

Derive:

- contribution to cash-like, fixed-income, and gold exposure buckets
- liquidity status only after explicit tier/rules exist

Do not classify bank WMP, money-market product, and demand deposit as identical
cash. They need different liquidity and valuation methods.

### 5.5 Insurance

The provided insurance fact is a USD policy-like asset with low liquidity. Its
reported amount is not necessarily investable cash.

Keep:

- account type: insurance
- currency: USD
- product type: insurance policy
- manual reported amount and as-of
- `rebalance_eligible=false`
- `tier=locked`
- missing fields: cash value, surrender value, lockup/withdrawal date, partial
  withdrawal rule

Do not treat policy amount as a rebalancing funding source. For net worth,
prefer cash value or surrender value when available. Until then, mark valuation
quality as limited.

### 5.6 Cross-Asset Exposure Groups

The sample includes useful aggregate groups:

- gold: bank gold, gold feeder fund, NEM
- overseas tech/Nasdaq: QDII Nasdaq funds, NVDA, information-industry fund
- energy: XLE
- defense/aerospace: ITA
- cash-like/low-volatility: cash, money-market funds, SGOV, bank WMP, fixed+,
  insurance only if clearly marked as low-liquidity

These groups should not be stored as manual totals. They should be derived from
`exposure_tags`, valuation snapshots, and liquidity filters.

## 6. Completeness Rules

Every position should carry a machine-readable completeness status. Suggested
rules:

### Listed market positions

Required:

- `instrument.instrument_key`
- `holding.quantity`
- `currency`
- `valuation_input.method=market_quote`

Strongly recommended:

- `holding.cost_basis`
- `liquidity.tradable`
- `liquidity.rebalance_eligible`

If quantity exists but no cost basis, the system can calculate value but not
PnL. If instrument exists but the instrument is not in the quote universe, the
system should add it to the runtime quote universe or emit a `data_quality`
warning.

### OTC funds

Minimum:

- display name
- currency
- manual amount
- product type
- exposure tags

Precise NAV tracking:

- fund code
- shares
- cost NAV or cost amount
- NAV source
- NAV as-of

Without fund code and shares, do not fabricate PnL. Use reported profit only as
an external snapshot.

### Bank WMP and money-market products

Minimum:

- product type
- manual amount
- currency
- liquidity tier

Better:

- product code
- risk level
- redemption/open rule
- maturity date
- reported profit

### Precious metals

Minimum:

- metal type
- manual value
- currency
- exposure tag `gold` or `precious_metals`

Precise monitoring:

- grams or units
- cost price
- quote source
- buy/sell spread rule

### Insurance

Minimum:

- policy display name or category
- currency
- reported amount
- `rebalance_eligible=false`
- liquidity tier

Better:

- current cash value
- surrender value
- lockup/withdrawal date
- partial withdrawal rule

## 7. Runtime Context Changes

The persistent memory should feed a richer runtime context:

```json
{
  "positions": [],
  "valuation_snapshots": [],
  "portfolio_exposures": [],
  "liquidity_summary": {},
  "data_quality": {}
}
```

Runtime-only fields:

- latest price
- price source
- price as-of
- NAV as-of
- FX rate and FX source
- value in native currency
- value in base currency
- unrealized PnL
- PnL ratio
- account weight
- portfolio weight
- exposure weight
- stale/manual/degraded flags

This prevents the memory file from accumulating stale market facts while still
giving the agent a complete analysis package.

## 8. Decision And Advice Guardrails

Advice generation should enforce the following:

1. `rebalance_eligible=false` positions cannot be used as funding sources.
2. `tradable=false` positions cannot receive `reduce` or `exit` actions unless
   the action is explicitly framed as non-market administrative action.
3. Positions with manual stale valuation can participate in net worth but should
   weaken or block precise rebalancing advice.
4. Listed positions without quote data should be marked `no_data`, not treated
   as unchanged.
5. OTC funds without fund code/shares can receive high-level allocation advice
   but not precise PnL/trigger advice.
6. Insurance should be excluded from "deployable cash" and "available funding"
   unless the user explicitly marks it withdrawable.
7. Exposure-level concentration should aggregate through tags across wrappers,
   especially gold, Nasdaq/US tech, energy, and defense.

## 9. Migration Plan

### Step 1: Add v2 fields without breaking v1

Extend `FinancialAsset` or introduce a parallel `Position` dataclass while
preserving old records:

- old `name` -> `display_name`
- old `platform` -> account display name or generated `account_id`
- old `amount` -> `valuation_input.manual_amount` for manual assets, or
  fallback value for listed assets
- old `asset_type` -> `classification.asset_class/product_type` through a
  deterministic mapping table
- old `currency` -> `currency`
- old `instrument_key` -> `instrument.instrument_key`
- old `quantity` -> `holding.quantity`
- old `tradable` -> `liquidity.tradable`
- old `notes` -> `notes`

No automatic parsing of notes into cost basis or codes.

### Step 2: Add completeness and quote-universe checks

Add deterministic warnings:

- mapped holding not in quote universe
- listed position missing quantity
- quantity exists but cost missing
- manual amount missing `as_of`
- rebalance-ineligible asset included in funding source
- unsupported FX currency

### Step 3: Add account and liquidity summaries

Context should output:

- account totals
- deployable cash
- cash-like but not deployable
- locked/low-liquidity assets
- FX exposure
- exposure concentrations by tag

### Step 4: Add valuation providers only where value is proven

Priority:

1. existing market quotes for listed assets
2. FX extension beyond USD/CNY if needed
3. China fund NAV provider for fund codes
4. manual snapshot freshness checks
5. precious metal quote provider if grams/cost are provided

Do not add providers for data that the user has not structured enough to use.

## 10. Suggested Thin JSON Example

```json
{
  "schema_version": 2,
  "base_currency": "CNY",
  "accounts": [
    {
      "account_id": "cn_broker_a",
      "display_name": "A股证券账户",
      "institution_type": "brokerage",
      "market_scope": ["a"],
      "base_currency": "CNY"
    },
    {
      "account_id": "us_broker",
      "display_name": "美元证券账户",
      "institution_type": "brokerage",
      "market_scope": ["us"],
      "base_currency": "USD"
    },
    {
      "account_id": "cn_fund_platform",
      "display_name": "中国基金销售账户",
      "institution_type": "fund_platform",
      "market_scope": ["fund_cn"],
      "base_currency": "CNY"
    }
  ],
  "positions": [
    {
      "position_id": "cn_broker_a_510300",
      "account_id": "cn_broker_a",
      "display_name": "沪深300ETF",
      "currency": "CNY",
      "classification": {
        "asset_class": "equity",
        "product_type": "exchange_traded_fund",
        "subtype": "broad_index_etf",
        "exposure_tags": ["cn_equity", "csi300", "broad_index"]
      },
      "instrument": {
        "instrument_key": "a:510300",
        "market": "a",
        "code": "510300",
        "exchange": "sh",
        "quote_kind": "exchange_quote"
      },
      "holding": {
        "quantity": 2100,
        "unit": "share",
        "cost_basis": {
          "method": "average",
          "unit_cost": 4.796,
          "cost_amount": 10071.6,
          "currency": "CNY"
        }
      },
      "valuation_input": {"method": "market_quote"},
      "liquidity": {
        "tradable": true,
        "rebalance_eligible": true,
        "tier": "t0"
      },
      "role": "core",
      "data_completeness": {"missing_fields": []}
    },
    {
      "position_id": "us_broker_sgov",
      "account_id": "us_broker",
      "display_name": "SGOV",
      "currency": "USD",
      "classification": {
        "asset_class": "cash_equivalent",
        "product_type": "short_treasury_etf",
        "subtype": "short_treasury",
        "exposure_tags": ["usd", "short_treasury", "cash_like", "low_volatility"]
      },
      "instrument": {
        "instrument_key": "us:SGOV",
        "market": "us",
        "code": "SGOV",
        "exchange": "NYSEARCA",
        "quote_kind": "exchange_quote"
      },
      "holding": {
        "quantity": 30,
        "unit": "share",
        "cost_basis": {
          "method": "average",
          "unit_cost": 100.5,
          "currency": "USD"
        }
      },
      "valuation_input": {"method": "market_quote"},
      "liquidity": {
        "tradable": true,
        "rebalance_eligible": true,
        "tier": "t1"
      },
      "role": "cash_buffer"
    },
    {
      "position_id": "insurance_usd_policy",
      "account_id": "insurance_account",
      "display_name": "USD insurance policy",
      "currency": "USD",
      "classification": {
        "asset_class": "insurance",
        "product_type": "insurance_policy",
        "exposure_tags": ["usd", "locked"]
      },
      "instrument": null,
      "holding": null,
      "valuation_input": {
        "method": "insurance_value",
        "reported_amount": null,
        "reported_amount_kind": "unknown_policy_amount"
      },
      "liquidity": {
        "tradable": false,
        "rebalance_eligible": false,
        "tier": "locked",
        "redemption_rule": null,
        "lockup_until": null,
        "maturity_date": null
      },
      "data_completeness": {
        "missing_fields": [
          "cash_value",
          "surrender_value",
          "withdrawal_date",
          "partial_withdrawal_rule"
        ]
      }
    }
  ]
}
```

## 11. Implementation Boundary For The Next Slice

The next practical slice should not try to solve everything. Recommended scope:

1. Add account id, classification, exposure tags, liquidity, valuation method,
   and cost basis fields.
2. Preserve v1 compatibility.
3. Add `data_quality` warnings for missing fields and mapped holdings outside
   the quote universe.
4. Derive current market value and unrealized PnL only for listed assets with
   quantity, cost basis, and quote data.
5. Output exposure and liquidity summaries in `raw_prompt_input`.

Explicitly out of scope for the next slice:

- transaction ledger
- tax lots
- broker API sync
- full accounting reconciliation
- automatic parsing of the user's historical notes
- database migration
- automatic trading

This keeps the refactor aligned with the product vision: a personal analyst
system that becomes more useful through validated slices, not a premature
portfolio accounting platform.
