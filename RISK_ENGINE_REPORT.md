# DAY TRADER PRO -- V3 RISK ENGINE & POSITION SIZING REPORT (PHASE 4)

**Date:** 2026-11-02  
**Branch:** `feature/trading-v3`  
**Status:** COMPLETE & TESTED  

---

## 1. REPAIRS IMPLEMENTED

1. **Independent Account-Isolated Risk Architecture (`trader/v3_risk_engine.py`):**
   * Eliminates the flaw of arbitrary hardcoded lot sizing (e.g. `quantity: 100` for all stocks).
   * Implements strict **0.5% risk per trade** calibrated directly to individual account equity:
     $$\text{Quantity} = \left\lfloor \frac{\text{Account Equity} \times 0.005}{|\text{Entry Price} - \text{Stop Loss Price}|} \right\rfloor$$
   * High-priced and low-priced equities now have **exact identical rupee risk** (e.g. ₹5,000 max risk per trade on ₹10L equity).

2. **Standard V3 Protective Guardrails:**
   * **Max Daily Loss Limit:** 1.5% of account equity (₹15,000 on ₹10L). Exceeding this halts all new entries for the trading session.
   * **Consecutive Losses Kill-Switch:** 2 consecutive losses trigger an automatic risk pause.
   * **Max Open Positions:** Strictly capped at 3 concurrent open positions.
   * **Max Daily Trades:** Capped at 5 trades per day to eliminate fee erosion.
   * **Max Concurrent Open Risk:** Aggregate open risk across all positions cannot exceed 1.0% of equity.

3. **Multi-Account Isolation:**
   * Risk states and trade counters are completely decoupled per account (`gautam`, `himanshu`, `prateek`, `fno`).
   * A breach in one account (e.g. Himanshu reaching 2 consecutive losses) halts only that account; Gautam and Prateek continue operating without interference.

4. **Automated Unit Testing:**
   * Created unit test suite `tests/test_v3_risk_engine.py` with 6 test cases verifying 0.5% sizing, price parity, daily loss limits, consecutive loss triggers, position caps, and multi-account isolation.
   * All 6 tests passed (100% OK).
