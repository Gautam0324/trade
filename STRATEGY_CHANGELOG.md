# Strategy & Architecture Changelog: Day Trader Pro V2

All notable changes to the trading bot architecture, execution pipeline, risk engine, and strategy models are documented in this file.

---

## [v2.2.0] - 2026-10-29

### Added
* **Reproducible Multi-Strategy Comparator (`trader/compare_strategies.py`)**:
  * Ingests genuine historical 1-minute OHLCV CSV data (`historical_data/`).
  * Implements chronological forward splits: Training (60%), Validation (20%), and Out-Of-Sample Test (20%).
  * Generates institutional comparative metrics: Gross P&L, Regulatory Fees & Taxes, Slippage, Net P&L, Profit Factor, Max Drawdown %, and Average R.
  * Completely removes legacy synthetic Gaussian noise generator (`random.gauss`).
* **Kernel-Level Single Instance Locking (`trader/multi_live_trader.py`)**:
  * Utilizes OS kernel-level non-blocking file locking (`msvcrt.locking` on Windows, `fcntl.flock` on Unix).
  * Automatically releases locks on process termination, eliminating false-positive stale PID conflicts.
* **Symbol Cooldown Enforcement (`trader/config.json`, `trader/multi_live_trader.py`)**:
  * Enforces mandatory 15-minute cooldown (`cooldown_minutes: 15`) per symbol following a closed position.
  * Restores cooldown state from `ledger.json` on restart.
* **Adverse Regime Filters (`trader/strategy_engine.py`)**:
  * Automatically blocks trend breakout admissions during `HIGH_VOLATILITY` and `LOW_LIQUIDITY` regimes.

### Changed
* **Supertrend Exit Smoothing (`trader/multi_live_trader.py`)**:
  * Added `min_hold_bars = 4` threshold before discretionary indicators (Supertrend reversal) can exit a position.
  * Prevents 1-minute noise exits in 60-120 seconds while keeping Hard Stop-Loss and Target fully active from bar 0.
* **Confluence Regime Bonus Alignment (`trader/strategy_engine.py`)**:
  * Removed erroneous directional bonus awarded to `RANGE_BOUND` conditions.
  * Directional bonuses require explicit `TRENDING_UP` (for Long) and `TRENDING_DOWN` (for Short).
* **Historical CSV Chronological Sorting (`trader/backtest.py`)**:
  * Added datetime parsing and ascending sort (`sort_values('dt', ascending=True)`) in `load_historical_csv()`.
  * Guarantees forward chronological bar progression across multi-year data chunks.
* **Exit Price Guard (`trader/order_manager.py`)**:
  * Rejects exit recording if market quote is missing or non-positive; preserves position as `OPEN`.
* **Cash Shorting Guard (`trader/config.json`)**:
  * Set `allow_short: false` for cash equities to prevent counter-trend breakdown traps during bull market sessions.

---

## [v2.1.0] - 2026-10-28

### Added
* **Order Lifecycle Manager (`trader/order_manager.py`)**:
  * SQLite WAL transactional database (`trader/orders_lifecycle.db`).
  * Explicit state machine: `CREATED` $\rightarrow$ `VALIDATED` $\rightarrow$ `SUBMITTED` $\rightarrow$ `FILLED` $\rightarrow$ `CLOSED`.
  * Atomic order reservation and signal idempotency via `UNIQUE(signal_id)`.
* **Institutional Statutory Cost Model (`trader/cost_model.py`)**:
  * Intraday equity fees: Brokerage (0.03% capped at ₹20), STT (0.025% on sell), NSE turnover (0.00345%), GST (18%), SEBI (₹10/crore), Stamp Duty (0.003% on buy), Slippage (2 bps).
* **Ledger Invariant Reconciliation (`trader/ledger_utils.py`)**:
  * Mathematical verification: $\text{Free Cash} + \text{Deployed Cash} = \text{Starting Capital} + \text{Realized P\&L}$.

### Fixed
* **Infinite Telegram Alert Loop on EOD Square-Off**:
  * Moved position removal (`open_positions.pop(sym, None)`) to start of exit block, preventing Unicode console crashes from aborting position deletion.
* **Dashboard Metric Discrepancies (`dashboard/service.py`, `dashboard/app.js`)**:
  * Separated `today_realized_pnl` from cumulative all-time P&L and `today_trades_count` from total historical trades.
* **Fake Breakeven Exits**:
  * Added `fetch_exit_quote(...)` at `flat_by` cutoff, eliminating fabricated ₹0.00 P&L exits.
