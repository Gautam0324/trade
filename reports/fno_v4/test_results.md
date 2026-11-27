# TEST RESULTS REPORT: F&O REPAIR REGRESSION SUITE

**Document:** `reports/fno_v4/test_results.md`  
**Date:** 2026-11-27  
**Test Suite Path:** `tests/`  
**Framework:** Pytest 9.1.1 on Python 3.12  
**Status:** ✅ ALL 62 TESTS PASSED (100% PASS RATE)  

---

## 1. Test Suite Summary

```powershell
pytest tests\test_fno_directional_pnl.py tests\test_fno_v4_phase1_integrity.py tests\test_fno_price_validation.py tests\test_fno_v4_audit_verification.py tests\test_fno_v4_market_regime.py
```

```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\Trade-agent\day-trader-skill
plugins: anyio-4.16.0
collected 62 items

tests\test_fno_directional_pnl.py ...............                        [ 24%]
tests\test_fno_v4_phase1_integrity.py ........                           [ 37%]
tests\test_fno_price_validation.py .................                     [ 64%]
tests\test_fno_v4_audit_verification.py ............                     [ 83%]
tests\test_fno_v4_market_regime.py ..........                            [100%]

============================= 62 passed in 11.50s =============================
```

---

## 2. Detailed Test Category Breakdown

| Test Suite File | Test Count | Key Invariants Tested | Pass Rate | Status |
| :--- | :---: | :--- | :---: | :---: |
| `tests/test_fno_directional_pnl.py` | 15 | CE/PE Buy/Sell profit/loss, lot sizes, partial exit aggregation, statutory charges, IV crush divergence | 15 / 15 | ✅ 100% |
| `tests/test_fno_v4_phase1_integrity.py` | 8 | QuoteValidator, timestamp presence, staleness, crossed books, BFO routing, ledger cash conservation | 8 / 8 | ✅ 100% |
| `tests/test_fno_price_validation.py` | 17 | OrderManager fills, depth slippage, duplicate order blocking, failed exit preservation | 17 / 17 | ✅ 100% |
| `tests/test_fno_v4_audit_verification.py` | 12 | Paper mode guard, rigid risk parameters, 15m HTF confirmation, session VWAP, cash conservation | 12 / 12 | ✅ 100% |
| `tests/test_fno_v4_market_regime.py` | 10 | Strong/weak trends, consolidation, volatility shocks, missing/stale data rejection, zero lookahead bias | 10 / 10 | ✅ 100% |
| **Total** | **62** | **Full financial, market data, and execution safety invariants** | **62 / 62** | ✅ **100%** |
