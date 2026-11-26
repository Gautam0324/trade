# CHANGELOG: F&O ALGO TRADER V4 REDESIGN & PROFITABILITY OPTIMIZATION

All notable changes to the F&O Trading Engine, Architecture, and Risk Management will be documented in this file.
This changelog strictly adheres to the Phase-by-Phase engineering protocol.

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
