# CHANGELOG: F&O ALGO TRADER V4 REDESIGN & PROFITABILITY OPTIMIZATION

All notable changes to the F&O Trading Engine, Architecture, and Risk Management will be documented in this file.
This changelog strictly adheres to the Phase-by-Phase engineering protocol.

---

## [Phase 1.2 — Master Repair] — 2026-11-27

### Added
- `trader/fno/quote_validator.py`: Hardened `QuoteValidator` to strictly prohibit historical candle data from generating executable fills (`HISTORICAL_CANDLE_CANNOT_BE_USED_FOR_EXECUTION`), added regex cleaning for timezone suffixes (`IST`, `UTC`, `GMT`).
- `reports/fno_v4/repair_execution_report.md`: Master repair execution report detailing root causes and fixes across all subsystems.
- `reports/fno_v4/ledger_reconciliation_report.md`: Complete accounting reconciliation report investigating the ₹528,540 deployed cash mismatch.
- `reports/fno_v4/market_data_validation_report.md`: Quote integrity rules and validation workflow.
- `reports/fno_v4/strategy_implementation_audit.md`: Detailed configuration vs. execution discrepancy matrix.
- `reports/fno_v4/test_results.md`: Complete automated test execution log.

### Fixed
- **Order Execution Hardening:** Updated `_execute_option_entry` in `trader/fno_trader.py` to enforce `QuoteValidator` depth checks and prohibit fills on candle closes.
- **Fail-Closed Overnight Reconciliation:** Updated `load_ledger` in `trader/fno_trader.py` to query live morning auction quotes; if unavailable, positions are preserved open in state `RECONCILIATION_PENDING` without artificial fills or fabricated P&L.
- **Ledger Invariant Conservation:** Reconstructed balances across all 4 account ledgers (`ledger.json`, `ledger_fno.json`, `ledger_himanshu.json`, `ledger_prateek.json`) with zero residual drift.
- **Test Fixture Date Drift:** Updated `test_fno_price_validation.py` to dynamically compute future test expiries, preventing test failures across dates.

### Verified
- Executed 62 automated tests across all 5 test suites: 62 passed, 0 failed in 11.50s.

---

## [Phase 1.1] — 2026-11-27

### Added
- `trader/fno/pnl_engine.py`: Created centralized `OptionsPnLEngine` and `OptionPnLBreakdown` for CE/PE Buy/Sell PnL, partial exit aggregation, statutory charges, and directional signal validation.
- `tests/test_fno_directional_pnl.py`: Implemented 15 deterministic unit tests covering CE/PE Buy/Sell profit/loss logic, lot sizes, partial exits, IV crush divergence, and quote rejection.
- `reports/fno_v4/directional_pnl_execution_audit.md`: Comprehensive audit report on directional P&L and execution logic.

### Verified
- Executed 62 automated tests across all 5 test suites: 62 passed, 0 failed.

---

## [Phase 1] — 2026-11-26

### Added
- `trader/fno/quote_validator.py`: Built centralized `QuoteValidator` and `ValidationResult` enforcing timestamp presence, staleness rejection (<60s for ticks, <180s for candles), positive finite LTP, bid <= ask, and BFO/NFO exchange routing validation.
- `trader/fno/__init__.py`: Package initialization exporting `QuoteValidator`.
- `tests/test_fno_v4_phase1_integrity.py`: 8 comprehensive tests covering timestamp parsing, staleness rejection, crossed order books, BFO validation, and ledger cash conservation.
- `reports/fno_v4/phase_1_data_integrity_report.md`: Formal Phase 1 sign-off report.

### Fixed
- **BFO/NFO Routing in Exits & Market Depth:** Fixed hardcoded `exchange="NFO"` in `_execute_exit_with_retry`, `get_underlying_chain_structure`, and live order dispatch in `trader/fno_trader.py`.
- **Multi-Account Ledger Accounting Drift:** Reconciled deployed cash and free cash balances in `trader/ledger_himanshu.json` and `trader/ledger_prateek.json` to strictly preserve the fundamental accounting invariant.
- **Overnight Stale Reconciliation:** Updated `fno_trader.py` startup reconciliation to fetch live morning quotes with dynamic exchange routing.

### Verified
- Executed 4 test suites: 47 tests passed, 0 failed.

---

## [Phase 0] — 2026-11-26

### Added
- Created `reports/fno_v4/initial_strategy_audit.md`: Comprehensive engineering baseline audit inspecting the entire codebase, identifying why V3 underperformed under live market pricing, tracing execution paths, and verifying 14 key data and accounting defects.
- Initialized `V4_CHANGELOG.md` to track every modification, bug fix, test verification, and architectural evolution.
- Initialized `V4_DECISION_LOG.md` to record all quantitative and architectural decisions with hypotheses, empirical evidence, and outcome validations.

### Audited & Verified Findings
- **Synthetic Entry Discrepancy:** Proved from `trader/ledger_fno.json` that 141 early November trades were entered at static fallback prices (e.g. ₹220 for BANKNIFTY) and exited against live broker quotes (₹700–₹950), fabricating an artificial 59.6% win rate and ₹6.82L gross P&L.
- **Microstructure Reality Shock:** Proved that live trading win rate collapsed to 22.7% (with 14/17 losses caused by rigid 5% stop loss) due to index bid-ask bounce and 1-minute whipsaws.
- **Phantom Indicators in F&O Daemon:** Confirmed that `VWAP`, `RSI`, `ADX`, and multi-strategy confluence advertised in logs and configs are absent from `trader/fno_trader.py`.
- **Dormant StratLab Modules:** Identified that while `ENABLE_IV_RANK_GUARD = True` is declared, the guard is never invoked in the evaluation loop; `ENABLE_VERTICAL_SPREADS = False` is hardcoded; Greeks and IV calculations are not wired into contract selection.
- **Hardcoded Exchange Routing:** Identified that `_execute_exit_with_retry` and multiple fallback paths hardcode `exchange="NFO"`, causing quote failures and order rejections on BSE/BFO contracts.
- **Overnight Stale Reconciliation:** Proved that stale overnight positions are closed using `current_price` (stored from previous day) rather than verified morning auction market quotes.
- **Accounting Drift:** Uncovered a deployed cash mismatch in `trader/ledger_himanshu.json` (stored ₹851,312.25 vs calculated ₹322,772.25) causing test failure in `test_11_ledger_cash_conservation_and_reconciliation`.
