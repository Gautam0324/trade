# 📈 Day Trader Pro V2 — Event-Driven Quantitative Trading System (NSE)

> **High-Frequency WebSocket Streaming, Multi-Timeframe Candle Builder, Multi-Strategy Confluence Engine, Tick-Level Risk Monitor & Reliable Paper Trading**  
> Built for Indian Equities (NSE) with Angel One SmartAPI WebSocket (`SmartWebSocketV2`), atomic order state machine, statutory regulatory cost accounting (STT, GST, NSE turnover fees, stamp duty, slippage), and real-time dashboard diagnostics.

---

## 👨‍💻 Author & Repository Details
* **Author / Developer**: **Gautam** ([@Gautam0324](https://github.com/Gautam0324))
* **Repository**: [https://github.com/Gautam0324/trade](https://github.com/Gautam0324/trade)
* **Base Currency**: `₹ INR`
* **Target Exchange**: National Stock Exchange (`NSE`) *(BSE strictly blocked)*
* **Safety Isolation**: `PAPER_TRADING=true`, `LIVE_TRADING=false` (Guarded by `PaperTradingIsolationError`)
* **Test Suite**: **417 Automated Unit Tests Passing (100% Green, 0 Failures)**

---

## 🏛️ V2 Architecture Pipeline

```
Angel One SmartAPI WebSocket (SmartWebSocketV2)
        |
        v
Live Tick Data Receiver
        |
        v
TickParser & TickValidator (Normalization, bounds & duplicate checks)
        |
        v
Live PriceStore (Thread-safe in-memory cache)
        |
        +-----------------------------------+
        |                                   |
        v                                   v
CandleBuilder (1m & 5m OHLCV)       TickRiskMonitor (Sub-millisecond)
        |                                   |
        v                                   v
IndicatorEngine (EMA, ST, MACD, VWAP)  Paper Stop-Loss / Target Exit
        |                                   |
        v                                   +--> PaperExecutionEngine
StrategyManager (Confluence Engine)                 |
        |                                           v
Signal Validation & Order Admission         TradeLedger / SQLite DB
        |                                           |
        v                                           v
PaperExecutionEngine (0.02% slip + costs)   DashboardEventManager
```

---

## 🚀 Key Features & Architectural Upgrades in V2

### 1. 📡 Event-Driven SmartAPI WebSocket Stream (`trader/market_data/`)
* **Zero REST Rate Limits**: Replaces legacy polling loops that triggered Angel One's HTTP 429 (`Access denied because of exceeding access rate`).
* **Persistent Connection**: Uses official `SmartWebSocketV2` binary packet unpacker (`TickParser`) to stream ticks in paise, converting them to rupees with accurate volume and timestamps.
* **Integrity Validation**: `TickValidator` rejects malformed ticks, detects duplicate sequence numbers, monitors feed staleness, and drops out-of-order packets.
* **Auto-Reconnection**: Reconnects with exponential backoff and restores subscriptions without losing state.

### 2. 🕯️ Multi-Timeframe Live Candle Builder (`trader/market_data/candle_builder.py`)
* Constructs **1-Minute** and **5-Minute** completed OHLCV candles aligned to Asia/Kolkata exchange boundaries.
* Aggregates cumulative traded volume delta accurately.
* Emits completed candles into `CandleStore` with idempotent SQLite persistence (`market_candles`).

### 3. 🛡️ Sub-Millisecond Tick-Level Risk Monitor (`trader/risk/tick_risk_monitor.py`)
* Evaluates open paper positions on **every valid incoming tick** without running the entire strategy engine.
* Checks stop-loss and profit target instantly:
  * **Long**: `ltp <= stop_loss` or `ltp >= target`
  * **Short**: `ltp >= stop_loss` or `ltp <= target`
* Resolves gap opening ambiguities conservatively by prioritizing the protective stop-loss.
* Prevents duplicate exit events for the same position.

### 4. 🧠 Quantitative Indicator & Strategy Confluence (`trader/indicators/` & `trader/strategies/`)
* **`IndicatorEngine`**: Computes EMA 5, 9, 33, Highest High 33, Lowest Low 33, Supertrend (10, 3.0), MACD (12, 26, 9), Session VWAP, RSI 14, and ATR 14 strictly on completed candles with zero lookahead bias.
* **`StrategyManager`**: Standardized strategy interface (`evaluate`) with atomic signal IDs (`SIG-{symbol}-{direction}-{timestamp}`) preventing duplicate signals on the same candle.
* **Confluence Filter**: Requires explicit directional agreement (either all LONG or all SHORT) with score $\ge 65.0\text{ pts}$ and favorable risk-to-reward $\ge 1.5R$.

### 5. ⚖️ Atomic Execution & Statutory Indian Cost Model (`trader/execution/`)
* **Lifecycle State Machine**: Strict states (`SIGNAL_GENERATED` $\to$ `SIGNAL_VALIDATED` $\to$ `PAPER_ORDER_CREATED` $\to$ `POSITION_OPEN` $\to$ `EXIT_TRIGGERED` $\to$ `POSITION_CLOSED`).
* **Realistic Cost Accounting**: `CostModel` calculates exact Indian charges:
  * Brokerage: Flat ₹20 per executed leg
  * STT (Securities Transaction Tax): 0.025% on sell leg
  * Exchange Transaction Charges: 0.00345%
  * GST: 18% on brokerage and transaction fees
  * SEBI Turnover Charges: 0.0001%
  * Stamp Duty: 0.003% on buy leg
  * Execution Slippage: Conservative 0.02%
* **Daily Risk State**: Persisted to `trader/daily_risk_state.json` so daily loss limits and trade counters survive process restarts.

### 6. 📊 Real-Time Web Dashboard & Diagnostics (`dashboard/`)
* **Fast In-Memory Serving**: Responsive dark-mode dashboard on `http://localhost:8080`.
* **Stable Live Streaming**: Eliminates unwanted screen blinking and full-page refreshes.
* **Subsystem Endpoints**:
  * `GET /health`: Overall system health and trading safety mode.
  * `GET /api/system/status`: Daemon status, feed latency, and open position counts.

---

## 📂 Project Directory Structure

```text
day-trader-skill/
├── dashboard/                     # Web Dashboard UI & Server
│   ├── app.js                     # Frontend interactive controller
│   ├── event_manager.py           # Real-time WebSocket event broadcaster
│   ├── index.html                 # Dark-mode responsive terminal interface
│   ├── server.py                  # HTTP API server & diagnostic endpoints
│   ├── service.py                 # Dashboard data aggregation service
│   └── styles.css                 # Styling & animations
│
├── trader/                        # Core Algorithmic Trading Engine
│   ├── market_data/               # Live Tick, WebSocket & Candle Builder
│   │   ├── angel_client.py        # Secure TOTP SmartAPI authentication
│   │   ├── candle_builder.py      # Real-time 1m & 5m OHLCV generator
│   │   ├── candle_recovery.py     # Reconnection historical backfiller
│   │   ├── candle_store.py        # In-memory cache & SQLite persistence
│   │   ├── connection_monitor.py  # Health & throughput metrics
│   │   ├── instrument_manager.py  # Token, symbol & exchange registry
│   │   ├── price_store.py         # Concurrency price store & staleness check
│   │   ├── tick_parser.py         # SmartWebSocketV2 binary packet parser
│   │   ├── tick_validator.py      # Data integrity & bounds validator
│   │   └── websocket_manager.py   # Persistent streaming manager
│   │
│   ├── indicators/                # Quantitative Indicator Engine
│   │   ├── ema.py                 # SMA & EMA calculation
│   │   ├── highest_lowest.py      # Rolling HH 33 & LL 33
│   │   ├── indicator_engine.py    # Memoized multi-indicator engine
│   │   ├── macd.py                # Standard MACD (12, 26, 9)
│   │   ├── rsi.py                 # Wilder's 14-period RSI
│   │   ├── supertrend.py          # Supertrend direction & ATR bands
│   │   └── vwap.py                # Session-anchored VWAP
│   │
│   ├── strategies/                # Orthogonal Trading Strategies
│   │   ├── base_strategy.py       # Standard strategy interface
│   │   ├── confluence.py          # Multi-strategy confluence aggregator
│   │   ├── ema_channel.py         # EMA 5/9/33 channel breakouts & bounces
│   │   ├── strategy_manager.py    # Deduplication & signal orchestrator
│   │   ├── trend_momentum.py      # Supertrend direction + EMA momentum
│   │   └── vwap_pullback.py       # Intraday VWAP test & reclaim
│   │
│   ├── risk/                      # Risk Controls & Sizing
│   │   ├── daily_risk_state.py    # Persistent daily loss accumulator
│   │   ├── position_sizer.py      # Risk-fraction capital allocator
│   │   ├── risk_engine.py         # Central gate evaluation & cooldowns
│   │   └── tick_risk_monitor.py   # Sub-millisecond tick SL/Target watcher
│   │
│   ├── execution/                 # Paper Trading Simulation
│   │   ├── paper_engine.py        # Slippage simulation & isolation guard
│   │   ├── paper_order_manager.py # Atomic order state machine
│   │   ├── position_manager.py    # Open positions & mark-to-market registry
│   │   └── trade_ledger.py        # Single-source-of-truth ledger.json
│   │
│   ├── backtesting/               # Robust Historical Simulation
│   │   ├── backtest_engine.py     # Zero lookahead backtester with fee model
│   │   ├── data_splitter.py       # Chronological train/val/test splitter
│   │   ├── performance_metrics.py # Profit factor, win rate, max DD
│   │   └── walk_forward.py        # Rolling-window out-of-sample analyzer
│   │
│   ├── monitoring/                # System Health & Metrics
│   │   ├── health.py              # Health check service
│   │   ├── logger.py              # Structured audit logger with secret masking
│   │   └── metrics.py             # Latency & throughput tracker
│   │
│   ├── config.json                # Master system configuration
│   ├── cost_model.py              # Regulatory Indian tax & brokerage model
│   ├── ledger.json                # Persisted trades and position records
│   ├── multi_live_trader.py       # Production multi-symbol live trader
│   └── signals_audit.csv          # Real-time CSV audit trace
│
├── tests/                         # Complete Automated Test Suite (417 Tests)
│   ├── test_backtesting.py        # Backtest engine & metrics tests
│   ├── test_candle_builder.py     # Real-time OHLCV aggregation tests
│   ├── test_indicators.py         # Indicator accuracy & edge case tests
│   ├── test_paper_execution.py    # Slippage, cost model & order state tests
│   ├── test_risk_engine_v2.py     # Tick risk monitor & persistence tests
│   ├── test_strategies.py         # Strategy evaluation & deduplication tests
│   ├── test_tick_processing.py    # Tick parser, validator & price store tests
│   └── test_websocket.py          # WebSocket manager & reconnect tests
│
├── run.py                         # Master process supervisor
├── SYSTEM_V2_REPORT.md            # Detailed quantitative audit & changelog
└── README.md                      # Complete system documentation
```

---

## 📈 Historical Backtesting Datasets Available

| Timeframe | Duration | Date Range | Total Data Points | Covered Instruments |
| :--- | :---: | :---: | :---: | :--- |
| **1-Day (Daily)** | **248 Trading Days (~1 Year)** | 27 Oct 2025 to 27 Oct 2026 | ~248 bars/symbol | HDFCBANK, INFY, RELIANCE, TCS, ICICIBANK, SBIN, **NIFTY 50** |
| **5-Minute (5m)** | **~60 Trading Days (~3 Months)** | 31 Jul 2026 to 27 Oct 2026 | ~4,379 bars/symbol | HDFCBANK, INFY, RELIANCE, TCS, ICICIBANK, SBIN, **NIFTY 50** |
| **1-Minute (1m) Unified** | **7 Trading Days** | 16 Oct 2026 to 27 Oct 2026 | 17,614 bars | All watchlist stocks with precalculated indicators |
| **Live Captured (SQLite)** | **Recent Sessions** | 28 Oct 2026 to Present | ~2,500+ bars | Live session database (`trader/ticks_market_data.db`) |
| **10-Year Archive** | **10 Years (2,500+ Days)** | 2016 to 2026 | 500,000+ bars | Multi-year historical archive in `historical_data/` |

---

## ⚡ Quick Start & Commands

### 1. Setup Environment Credentials
Add your Angel One credentials to environment variables or `trader/secrets.json`:
```bash
ANGEL_API_KEY="your_api_key"
ANGEL_CLIENT_CODE="your_client_code"
ANGEL_PASSWORD="your_pin"
ANGEL_TOTP_SECRET="your_totp_secret"
PAPER_TRADING=true
LIVE_TRADING=false
```

### 2. Run the System (Master Supervisor)
Starts both the Dashboard Server (`http://localhost:8080`) and the Multi-Symbol Live Trader Engine:
```bash
python run.py
```

### 3. Run the Automated Test Suite
Executes the full test suite (all 417 unit and regression tests):
```bash
python -m unittest discover tests
```

To run only the 8 core V2 test modules:
```bash
python -m unittest tests/test_tick_processing.py tests/test_candle_builder.py tests/test_websocket.py tests/test_indicators.py tests/test_strategies.py tests/test_risk_engine_v2.py tests/test_paper_execution.py tests/test_backtesting.py
```

### 4. Verify System Health & Diagnostics
```bash
curl http://localhost:8080/health
curl http://localhost:8080/api/system/status
```

---

## 🛡️ Critical Safety Constraints
1. **Paper Trading Enforced**: `paper: true` is permanently required. Live broker execution is locked via `PaperTradingIsolationError`.
2. **Capital Protection**: Maximum daily loss kill-switch immediately disables new entries upon threshold breach.
3. **NSE Only**: BSE execution and derivative leverage are blocked by default.
4. **Mandatory EOD Square-Off**: All open positions are exited before `15:20 IST`.

---

## 📄 License
This project is licensed under the MIT License. See [LICENSE](file:///d:/Trade-agent/day-trader-skill/LICENSE) for details. Developed strictly for algorithmic trading research and paper trading on Indian Equities.
