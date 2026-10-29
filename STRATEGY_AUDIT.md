# Strategy & Architecture Audit Report: Day Trader Pro V2

**Audit Date**: 2026-10-29  
**Repository**: [github.com/Gautam0324/trade](https://github.com/Gautam0324/trade)  
**Safety Requirement**: Strict Paper Trading (`paper: true`), live execution calls physically blocked at the adapter boundary.

---

## 1. Executive Summary

This comprehensive audit inspects the complete quantitative trading pipeline of Day Trader Pro, covering:
1. **Entry & Exit Rule Execution Path**: From live/historical completed bars to indicator calculation, regime detection, confluence scoring, risk evaluation, atomic order admission, simulated execution, position monitoring, and ledger reconciliation.
2. **Execution State Management & Safety**: Idempotency, order reservation, broker isolation, and crash recovery.
3. **Institutional Cost Model**: Indian statutory charges (Brokerage cap ₹20, STT 0.025% sell-only, NSE turnover, GST 18%, SEBI, Stamp Duty 0.003% buy-only, slippage 2 bps).
4. **Historical Backtesting & Regime Verification**: Elimination of look-ahead bias, chronological time-series sorting, and replacement of synthetic random data with genuine 1-minute historical datasets (`historical_data/`).

---

## 2. Severity Classification Matrix

| Finding ID | Severity | Module | Description | Resolution Status |
| :--- | :---: | :--- | :--- | :---: |
| **DEF-01** | **CRITICAL** | `trader/compare_strategies.py` | Comparative strategy script generated synthetic Gaussian noise (`random.gauss`) rather than using real historical data. | **FIXED**: Rewritten to ingest genuine 1-min CSV data with chronological Train/Val/Test splits. |
| **DEF-02** | **CRITICAL** | `trader/backtest.py` | Multi-chunk CSV data stored in reverse chronological order was evaluated backward in time without ascending sort. | **FIXED**: Enforced chronological ascending sort on datetime before sequential bar evaluation. |
| **DEF-03** | **CRITICAL** | `trader/multi_live_trader.py` | Premature indicator exit on 1-min Supertrend flip (held 60s), causing repetitive transaction fee churn (-₹2,108 in fees). | **FIXED**: Enforced 4-bar minimum holding period for indicator exits; hard stop and target remain active from bar 0. |
| **DEF-04** | **CRITICAL** | `trader/strategy_engine.py` | Confluence engine awarded a +20.0 directional bonus to both Long and Short setups during `RANGE_BOUND` regimes. | **FIXED**: Bonus restricted strictly to `TRENDING_UP` (Long) and `TRENDING_DOWN` (Short). Adverse regimes blocked. |
| **DEF-05** | **HIGH** | `trader/order_manager.py` | Missing or non-positive exit quote could record corrupted exit price. | **FIXED**: Position preserved as `OPEN` if exit quote is missing or invalid; zero-P&L fabrication blocked. |
| **DEF-06** | **HIGH** | `trader/multi_live_trader.py` | Unbounded symbol re-entry caused 4 trades on `HDFCBANK` in 1 hour, incurring ₹992 in fees. | **FIXED**: Enforced 15-minute symbol cooldown (`cooldown_minutes: 15`). |
| **DEF-07** | **HIGH** | `trader/config.json` | Cash equity shorting permitted (`allow_short: true`) caused counter-trend loss on `TCS_BSE`. | **FIXED**: Set `allow_short: false` for cash equities. |
| **DEF-08** | **MEDIUM** | `trader/multi_live_trader.py` | Redundant benchmark NIFTY 50 API calls on every symbol loop triggered Angel One rate limit (3 req/sec). | **FIXED**: Cached benchmark data for 60 seconds with rate-limit exponential backoff. |
| **DEF-09** | **MEDIUM** | `dashboard/service.py` & `app.js` | "Today's Realized P&L" KPI was bound to all-time cumulative P&L rather than session-specific P&L. | **FIXED**: Separated `today_realized_pnl` and `today_trades_count` from cumulative metrics. |
| **DEF-10** | **LOW** | `trader/multi_live_trader.py` | Windows PID lock check relied on recycled OS PIDs causing false stale lock detection. | **FIXED**: Implemented OS kernel-level non-blocking file locking (`msvcrt.locking`). |

---

## 3. Deep-Dive Findings

### Finding 1: Synthetic Data in Strategy Comparison (CRITICAL)
* **Root Cause**: `trader/compare_strategies.py` generated prices using `random.gauss(0.6, 2.5)` across 250 bars. This gave misleading impressions of strategy viability and violated the rule against fabricated data.
* **Fix**: Rewrote `trader/compare_strategies.py` to ingest real historical CSVs from `historical_data/` (`3MINDIA_1min.csv`, `360ONE_1min.csv`, `20MICRONS_1min.csv`). Added chronological forward partitioning: Training (60%), Validation (20%), and Untouched Out-Of-Sample Test (20%).

### Finding 2: Reverse-Chronological CSV Slicing in Backtest (CRITICAL)
* **Root Cause**: Historical 1-minute CSVs downloaded via SmartAPI appended newer dates before older dates (e.g. 2026 at row 0, 2021 at row 390,000). Slicing without timestamp sorting caused the backtest to evaluate bars backward in time.
* **Fix**: Enforced `df['dt'] = pd.to_datetime(df['timestamp']); df = df.sort_values('dt', ascending=True)` in `BacktestEngine.load_historical_csv()`.

### Finding 3: Whipsaw Exit on 1-Minute Supertrend (CRITICAL)
* **Root Cause**: On 1-minute charts, minor market noise flipped Supertrend direction within 60-120 seconds of entry. The bot closed positions immediately on `st_dir == -1`, paying ~₹250 in statutory round-trip charges every time before the price had time to reach the +1.5R target.
* **Fix**: Added a minimum holding threshold (`bars_held >= 4`) for discretionary indicator exits in `evaluate_position_exit()`. Protective Hard Stop-Loss and Target remain active from bar 0.

### Finding 4: Inappropriate Confluence Bonus in Range-Bound Regimes (CRITICAL)
* **Root Cause**: `StrategyEngine.evaluate()` awarded a +20.0 bonus for `regime in ("TRENDING_UP", "RANGE_BOUND")` for Longs and `regime in ("TRENDING_DOWN", "RANGE_BOUND")` for Shorts. This falsely boosted breakout scores in sideways consolidation.
* **Fix**: Required strict directional alignment (`regime == "TRENDING_UP"` for Long, `regime == "TRENDING_DOWN"` for Short). Disallowed breakout entries during `HIGH_VOLATILITY` and `LOW_LIQUIDITY` regimes.

---

## 4. Execution State & Risk Limits Verification

1. **Broker Isolation**: `BrokerAdapter` strictly raises `PaperModeIsolationError` if any live order method is invoked while `paper=True`.
2. **Order Lifecycle FSM**: SQLite WAL state machine explicitly transitions:
   $$\text{CREATED} \longrightarrow \text{VALIDATED} \longrightarrow \text{SUBMITTED} \longrightarrow \text{FILLED} \longrightarrow \text{CLOSED}$$
3. **Ledger Accounting Invariant**: Mathematical verification enforced on every ledger read/write:
   $$\text{Free Cash} + \text{Deployed Cash} = \text{Starting Capital} + \text{Realized P\&L}$$
4. **Centralized Risk Limits**:
   * Cash Buffer: 10% unallocated reserve (₹100,000 minimum).
   * Portfolio Exposure Cap: 80% maximum capital deployment.
   * Daily Order Limit: 20 orders/day maximum.
   * Daily Trade Limit: 10 round-trip trades/day maximum.
   * Daily Loss Kill Switch: ₹20,000 loss threshold halts all trading.
