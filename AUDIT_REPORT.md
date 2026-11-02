# DAY TRADER PRO -- COMPREHENSIVE ENGINE & LEDGER AUDIT REPORT (PHASE 1)

**Date:** 2026-11-02  
**Branch:** `feature/trading-v3`  
**Checkpoint Commit:** `4553cf3`  
**Auditor:** Quantitative Architect & Financial Execution Auditor  
**Trading Mode:** STRICT PAPER TRADING ONLY  

---

## 1. EXECUTIVE SUMMARY & REPOSITORY ARCHITECTURE

An exhaustive code and ledger audit of the **Day Trader Pro** repository was conducted. The system is designed to provide real-time automated day trading across Indian cash equities (NSE) and index derivatives (NIFTY & BANKNIFTY weekly options) via Angel One SmartAPI.

### Current High-Level Architecture
```
Angel One SmartAPI (WebSocket / REST)
       │
       ▼
Tick Normalizer / Memory CandleStore (multi_live_trader.py / fno_trader.py)
       │
       ▼
Strategy Engine (strategy_engine.py / indicators.py)
       │  [CONFLUENCE, EMA_CHANNEL, TREND_MOMENTUM, VWAP_PULLBACK]
       ▼
Risk Engine (risk_engine.py / daily_risk_state.py)
       │
       ▼
Execution & Position Layer (multi_account_manager.py / order_manager.py)
       │
       ▼
Cost Model (cost_model.py)
       │
       ▼
Multi-Account Ledgers (ledger.json, ledger_himanshu.json, ledger_prateek.json, ledger_fno.json)
       │
       ▼
Dashboard Server & Web UI (dashboard/server.py, dashboard/service.py, dashboard/app.js)
```

---

## 2. ROOT CAUSE AUDIT OF EXISTING LEDGER & EXECUTION DEFECTS

### Issue A: Repeated EOD Square-Off Entries (Oct 26 Loop)
* **Observed Incident in `trader/ledger.json`:**
  On 2026-10-26, between 15:20:05 IST and 15:25:00 IST, exactly **18 duplicate RELIANCE positions** (`POS-56` through `POS-73`) of `quantity: 341` were created and squared off every 16 seconds with identical entry and exit prices (`₹1201.60`).
* **Root Cause Verified in Code:**
  1. In `trader/multi_live_trader.py`, while `flat_by = "15:20"` was checked, the main evaluation loop continued to evaluate Stage-1 and Stage-2 entry signals between 15:20 and 15:25 because `no_entries_after` was not enforced as a hard circuit breaker inside the symbol scanner.
  2. Each iteration generated an entry, immediately followed by the EOD square-off check (`current_time_str >= flat_by and open_positions`), which immediately closed the position and wrote it to `ledger.json`.
  3. **Lack of an Idempotent EOD State Machine:** The engine lacked an atomic state (`EOD_LOCKED` / `EOD_COMPLETE`) that blocks all new entries and ensures square-off executes exactly once.
* **Proposed Solution:**
  Implement an explicit, persistent EOD State Machine (`READY`, `OPEN`, `EXIT_PENDING`, `CLOSED`, `EOD_LOCKED`, `EOD_COMPLETE`) in `order_manager.py` and `multi_live_trader.py`. Once `15:20 IST` is reached, state transitions to `EOD_LOCKED`; after all open positions close, state transitions to `EOD_COMPLETE`. Any signal generated after `EOD_LOCKED` is rejected immediately.

---

### Issue B: Incomplete P&L and Missing Fees in Historical Ledger
* **Observed Incident in `trader/ledger.json`:**
  Out of 112 historical trade records in `ledger.json`:
  * **81 records** (primarily Oct 26 legacy trades) have `gross_pnl: null` and `fees: null`.
  * Only **31 records** contain populated cost fields (`gross_pnl`, `fees`).
  * In the 31 records with cost fields, gross P&L was `₹+5,824.42` while fees were `₹7,638.40`, resulting in a cost-adjusted net P&L of `-₹1,813.98`, whereas top-level ledger reported realized P&L as `₹+4,614.21` (calculated purely on unadjusted gross differences without taxes).
* **Root Cause Verified in Code:**
  1. Early versions of `live_trader_bot.py` and `multi_live_trader.py` recorded trades directly into `trade_history` by calculating `pnl = (exit_p - entry_p) * qty` without routing through `CostModel`.
  2. Later, `CostModel` was added, but old records were left with missing fields (`null`), causing top-level summary metrics to diverge from actual net P&L.
* **Proposed Solution:**
  1. Create a centralized `accounting_engine.py` that enforces complete trade records.
  2. For historical records missing cost data, mark status as `COST_DATA_INCOMPLETE`. Provide a backward-compatible migration script `migrate_ledger_accounting.py` that recalculates standard Indian statutory charges (Brokerage, STT, Exchange Txn, GST, Stamp Duty) for historical trades without overwriting raw audit files.

---

### Issue C: Daily P&L Reconciliation & Symbol Discrepancies
* **Observed Incident in `trader/ledger.json`:**
  * For date `2026-10-28`, trade history showed 7 completed trades with net P&L `+₹3,896.44`, but daily summary mappings reported `₹0.00`.
  * Inconsistent symbol naming: `RELIANCE.NS`, `ICICIBANK.NS` coexist with `RELIANCE` and `ICICIBANK`.
* **Root Cause Verified in Code:**
  1. `service.py` and `dashboard/server.py` grouped trades by `t.get("date")`, but earlier trades stored dates under `t.get("exit_date")` or ISO strings like `2026-10-28T15:20:00+05:30`.
  2. Symbol names from Yahoo Finance / external scrapers appended `.NS` and `.BO`, whereas Angel One SmartAPI uses canonical NSE symbols (`RELIANCE`, `ICICIBANK`).
* **Proposed Solution:**
  1. Implement symbol canonicalization (`sym.upper().replace(".NS", "").replace(".BO", "")`) across all ingest, storage, and ledger layers.
  2. Standardize trade records with ISO-8601 dates (`YYYY-MM-DD`) and canonical timestamps.

---

### Issue D: Backtesting Engine Ambiguities & Data Handling
* **Observed Incident in `trader/run_4day_backtest.py`:**
  1. In `BacktestEngine.run()`, `daily_trades_count` was initialized to `len(trade_log)` (cumulative trades across all days) instead of resetting on date change (`cur_date != prev_date`), causing subsequent backtest days to be blocked when `max_trades_per_day` was reached.
  2. Slippage and statutory fees were aggregated into a single `fees` bucket rather than being reported distinctly.
  3. Lack of 15-minute confirmation candles in the legacy backtest engine.
* **Proposed Solution:**
  1. Rebuild the backtest engine to strictly reset daily risk counters at the start of each new trading date (`09:15 IST`).
  2. Model separate fields for `gross_pnl`, `brokerage`, `stt`, `exchange_txn`, `gst`, `stamp_duty`, `slippage`, and `net_pnl`.
  3. Implement dual-timeframe candle processing: 15-minute higher-timeframe trend confirmation + 5-minute primary execution candles.

---

## 3. INVENTORY OF EXISTING STRATEGIES (MUST REMAIN PRESERVED)

The existing strategy implementations in `trader/strategy_engine.py` will remain 100% intact and independently testable:
1. `EMA_CHANNEL`: 5/9/33 EMA channel breakout momentum.
2. `TREND_MOMENTUM`: EMA 9/33 + RSI + ADX + ATR trend follower.
3. `VWAP_PULLBACK`: Intraday VWAP pullback bounce.
4. `CONFLUENCE`: Multi-factor weighted confirmation (Supertrend + EMA channel + VWAP).
5. `ORB`: 15-minute Opening Range Breakout.
6. `BREAKOUT_RETEST`: Range breakout with retest confirmation.
7. `TREND_PULLBACK_RECLAIM`: Trend pullback towards EMA33/VWAP and reclaim.
8. `FAILED_BREAKDOWN`: False breakdown reclaim / false breakout rejection.
9. `RELATIVE_STRENGTH_BREAKOUT`: Outperformance vs benchmark.

The new strategy:
* `V3_TREND_PULLBACK`: Independent 5-minute primary + 15-minute trend confirmation engine with 0.5% risk per trade and volatility-based stops.

---

## 4. DETAILED FILE-LEVEL CHANGE PLAN

| File Path | Current Status | Planned Modification | Regression Risk |
| :--- | :--- | :--- | :--- |
| `trader/accounting_engine.py` | New file | Authoritative accounting module calculating gross P&L, fees, slippage, net P&L with strict schema validation. | Zero (New module) |
| `trader/eod_state_machine.py` | New file | Explicit EOD state machine (`READY`, `OPEN`, `EXIT_PENDING`, `CLOSED`, `EOD_LOCKED`, `EOD_COMPLETE`). | Zero (New module) |
| `trader/strategies/v3_trend_pullback.py` | New file | Independent V3 Strategy Engine (5m entry, 15m trend confirmation, ADX/ATR, 0.5% risk). | Zero (New module) |
| `trader/backtesting/v3_backtest_engine.py` | New file | Dual-timeframe backtester supporting 5m/15m candles, chronological walk-forward, realistic friction. | Zero (New module) |
| `trader/order_manager.py` | Existing | Integrate EOD state machine; prevent duplicate entries after cutoff. | Low (Tested via unit tests) |
| `trader/cost_model.py` | Existing | Preserve existing methods; add distinct `slippage` vs `statutory_fees` breakdown. | Low (Backward compatible) |
| `trader/risk_engine.py` | Existing | Add account-specific 0.5% equity risk sizing and multi-account isolation guards. | Low (Backward compatible) |
| `trader/ledger_utils.py` | Existing | Add reconciliation check for `COST_DATA_INCOMPLETE` flags and daily aggregation. | Zero (Audit utility) |
| `config/v3_config.json` | New file | Configuration file for V3 (disabled by default, paper mode). | Zero (New file) |

---

## 5. RECOVERY & ROLLBACK PLAN

* **Stable Git Checkpoint:** Commit `4553cf3` on branch `main`.
* **V3 Development Branch:** `feature/trading-v3`.
* **Rollback Procedure:**
  If any unintended behavior occurs at any time during development:
  ```bash
  git checkout main
  ```
  This immediately restores the exact pre-development state with all original ledgers, configs, and strategies intact.
* **Paper Trading Guarantee:**
  `LIVE_TRADING` is strictly locked to `false` and broker adapter order placement methods execute solely in simulation mode. No live exchange orders can be routed.

---

## 6. PHASE EXECUTION ROADMAP

1. **PHASE 1:** Repository Audit (Complete -- this document).
2. **PHASE 2:** Accounting Engine & Ledger Reconciliation Repair.
3. **PHASE 3:** Centralized Execution & EOD State Machine Repair.
4. **PHASE 4:** Account-Specific Risk Management & 0.5% Sizing Engine.
5. **PHASE 5:** Dual-Timeframe Historical Data Pipeline & Backtesting Engine.
6. **PHASE 6:** V3 Trend Pullback Strategy Implementation (Cash & Options).
7. **PHASE 7:** Multi-Account Isolation & Dashboard Integration.
8. **PHASE 8:** Backtesting, Strategy Comparison & Walk-Forward Validation.
9. **PHASE 9:** Final Automated Regression Testing & Verification Report.
