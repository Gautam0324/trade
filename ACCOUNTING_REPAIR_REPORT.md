# DAY TRADER PRO -- ACCOUNTING REPAIR & RECONCILIATION REPORT (PHASE 2)

**Date:** 2026-11-02  
**Branch:** `feature/trading-v3`  
**Status:** COMPLETE & TESTED  

---

## 1. REPAIRS IMPLEMENTED

1. **Centralized Authoritative Accounting Engine (`trader/accounting_engine.py`):**
   * Implemented full regulatory cost calculation according to Indian statutory rules:
     * **Brokerage:** Capped at ₹20 per executed order leg.
     * **STT:** 0.025% on Cash Sell turnover, 0.0625% on Options Sell premium turnover.
     * **Exchange Charges:** 0.00345% (NSE Cash) / 0.053% (NSE Options).
     * **GST:** 18% on (Brokerage + Exchange Charges).
     * **SEBI Charges:** ₹10 per crore (0.0001%).
     * **Stamp Duty:** 0.003% on Buy legs only.
     * **Slippage:** Segregated from statutory taxes into a dedicated `slippage` field.
   * Enforced canonical symbol normalization: strips suffixes such as `.NS`, `.BO`, `-EQ`.

2. **Cost-Completeness Audit (`COST_DATA_INCOMPLETE`):**
   * 81 legacy records from 2026-10-26 in `trader/ledger.json` that lacked itemized gross P&L and fees are explicitly tracked without mutating or deleting raw history.
   * All subsequent records (Himanshu, Prateek, F&O Desk, and modern Gautam trades) feature complete granular fee breakdowns.

3. **Multi-Account Reconciliation Verification:**
   * **Gautam (Cash):** Reported P&L `₹+4,614.21` == Calculated Net P&L `₹+4,614.21` (Discrepancy: `₹0.00`)
   * **Himanshu (Cash):** Reported P&L `₹-929.09` == Calculated Net P&L `₹-929.09` (Discrepancy: `₹0.00`)
   * **Prateek (Cash):** Reported P&L `₹+839.53` == Calculated Net P&L `₹+839.53` (Discrepancy: `₹0.00`)
   * **F&O Desk:** Reported P&L `₹+27,093.19` == Calculated Net P&L `₹+27,093.19` (Discrepancy: `₹0.00`)

4. **Automated Testing:**
   * Created unit test suite `tests/test_accounting_engine.py` covering symbol canonicalization, cash buy/sell costs, options costs, incomplete fee handling, and account reconciliation.
   * All 7 test cases passed (100% OK).
