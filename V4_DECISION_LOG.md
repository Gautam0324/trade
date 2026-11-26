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

## Decision D-002: Preservation of V3 Benchmark & Independent V4 Namespace
- **Date:** 2026-11-26
- **Status:** APPROVED
- **Context:** Need to ensure V3 and V4 performance can be compared side-by-side on identical market data, identical timeframes, and identical transaction cost models.
- **Decision:**
  - V4 will live in `trader/fno_v4/` with separate modular components.
  - V3 configuration and execution remain isolated.
  - Backtesting and forward paper validation will run identical data splits on both engines.
- **Expected Impact:** Guarantees zero regression on baseline and prevents cherry-picked comparison.
