# DECISION LOG: F&O ALGO TRADER V4 REDESIGN

This document tracks every major architectural, algorithmic, risk management, and quantitative decision made during the development of Algo Trader V4.

---

## Decision D-001: Phase 0 Repository Freeze & Empirical Baseline Audit
- **Date:** 2026-11-26
- **Status:** APPROVED
- **Context:** Transition from V3 to V4 required verifying 14 pre-identified concerns regarding synthetic fallbacks, quote timestamps, accounting mismatches, and execution integrity before modifying code.
- **Decision:**
  1. Complete Phase 0 without destructive code changes.
  2. Maintain V3 baseline files (`trader/fno_trader_v3_baseline.py` and `config/v3_config.json`) strictly intact as unchangeable benchmarks.
  3. Reconstruct historical trade ledgers to differentiate synthetic fallback fills from live quote executions.
- **Empirical Evidence:**
  - 141 trades (Nov 2 - Nov 11) used static ₹220/₹85 fallback prices.
  - 22 live trades (Nov 12 - Nov 16) with verified quotes suffered a 77.3% loss rate under V3's rigid -5% stop loss on 1-minute bars.
- **Expected Impact:** Establishes an uncompromised scientific benchmark against which V4 can be judged.

---

## Decision D-005: Master Repair — Fail-Closed Execution, Quote Validation & Accounting Conservation
- **Date:** 2026-11-27
- **Status:** APPROVED & IMPLEMENTED
- **Context:** Resolving the ₹528,540 deployed cash drift in slave ledgers, eliminating historical candle fills for executable options, and replacing automatic overnight mark closures with fail-closed reconciliation.
- **Decision:**
  - Hardened `QuoteValidator` to strictly reject historical candle data for executable order decisions.
  - Reconstructed deployed cash and free cash balances from first principles across all 4 ledgers ($Free + Deployed == Initial + Realized$).
  - Implemented `RECONCILIATION_PENDING` state for overnight positions when morning quotes are unavailable, strictly prohibiting artificial P&L.
  - Maintained live trading disabled and paper mode strictly enforced.
- **Empirical Validation:** 62 automated unit and regression tests passing.

---

## Decision D-004: Strict Option Premium P&L Authority (CE & PE Buying/Selling)
- **Date:** 2026-11-27
- **Status:** APPROVED & IMPLEMENTED
- **Context:** Ensuring CE and PE buy/sell operations strictly derive gross and net P&L from the traded option contract's actual premium, avoiding false P&L calculations based on underlying index movements.
- **Decision:**
  - Codify standard formulas:
    - BUY: $(P_{\text{exit}} - P_{\text{entry}}) \times \text{Qty}$
    - SELL: $(P_{\text{entry}} - P_{\text{exit}}) \times \text{Qty}$
  - Validate that underlying spot direction dictates trade entry qualification, but never overrides option premium P&L.
  - Implement full Indian statutory schedule (STT on sell, GST, stamp duty, turnover, brokerage).
- **Empirical Validation:** Verified with 15 automated test cases in `tests/test_fno_directional_pnl.py`.

---

## Decision D-003: Centralized Quote Validator & Dynamic BFO Exchange Propagation
- **Date:** 2026-11-26
- **Status:** APPROVED & IMPLEMENTED
- **Context:** Hardcoded "NFO" exchange routing in exit retries broke execution for BSE contracts (e.g. `BSE26DEC3950CE`). Missing exchange feed timestamps risked accepting stale quotes as live.
- **Decision:**
  - Implement `QuoteValidator` in `trader/fno/quote_validator.py` with fail-closed architecture.
  - Dynamically route contracts based on underlying and symbol prefix (`BFO` for BSE contracts, `NFO` for NSE).
  - Enforce `verified_market_data = True` only when genuine exchange timestamps pass age checks.
- **Empirical Validation:** 47 automated tests passed across all integrity suites.

---

## Decision D-002: Preservation of V3 Benchmark & Independent V4 Namespace
- **Date:** 2026-11-26
- **Status:** APPROVED
- **Context:** Need to ensure V3 and V4 performance can be compared side-by-side on identical market data, identical timeframes, and identical transaction cost models.
- **Decision:**
  - V4 will live in `trader/fno_v4/` with separate modular components.
  - V3 configuration and execution remain isolated.
  - Backtesting and forward paper validation will run identical data splits on both engines.
- **Expected Impact:** Guarantees zero regression on baseline and prevents cherry-picked comparison.
