# 📈 Advanced Multi-Strategy Day Trader System (NSE & BSE)

> **Autonomous Algo-Trading & Quantitative Execution Engine for Indian Equities (NSE/BSE)**  
> Developed with strict risk management, multiple quantitative strategies (EMA Channel, VWAP Pullback, ORB 15-min, Trend + Momentum, Confluence Scoring), ATR-based trailing exits, +1R partial profit booking, live Web Dashboard, and Telegram Bot alerts.

---

## 👨‍💻 Author & Maintainer
- **Lead Developer**: **Gautam** ([@Gautam0324](https://github.com/Gautam0324))
- **Repository**: [https://github.com/Gautam0324/trade.git](https://github.com/Gautam0324/trade.git)
- **Currency**: `₹ INR`
- **Target Exchanges**: National Stock Exchange (`NSE`) & Bombay Stock Exchange (`BSE`)
- **Default Operating Mode**: `PAPER TRADING` (Active safety constraint)

---

## 🚀 Supported Strategies

The trading engine supports independent or concurrent strategy execution selectable via `trader/config.json`:

### 1. Strategy A: EMA Channel Breakout
- **Indicators**: EMA 5 (Close), EMA 9 (Close), EMA 33 (High), EMA 33 (Low)
- **Long Entry**: Candle closes above EMA 33 High with EMA 5 > EMA 9.
- **Initial Stop**: Derived from structural level / ATR, bounded by max 1% budget risk.
- **Exit**: Trailing profit-lock, EMA 5 cross-down, or baseline EMA 9 breakdown.

### 2. Strategy B: Intraday VWAP Pullback
- **Session-anchored VWAP**: Calculated strictly on intraday session volume and typical price.
- **Long Entry**: Price in primary uptrend pulls back within 0.8% of VWAP and forms a completed bullish confirmation candle.
- **Exit**: Stop below VWAP or 1.5R target / trailing stop.

### 3. Strategy C: Opening Range Breakout (ORB 15-min)
- **Session Window**: First 15 minutes of trading (09:15 - 09:30 IST). No entries before 09:30 IST.
- **Long Entry**: First completed 5-min bar closing strictly above the 15-minute Opening Range High.
- **Stop**: Opening Range Midpoint or breakout bar low.

### 4. Strategy D: Trend + Momentum Confirmation
- **Indicators**: EMA 9, EMA 33, RSI 14, ADX 14 (+DI / -DI), ATR 14.
- **Long Entry**: EMA 9 > EMA 33, RSI between 48 and 78, ADX showing directional trend strength (+DI > -DI).
- **Stop**: ATR-derived volatility stop (Entry - 1.5 * ATR).

### 5. Confluence Scoring Mode (`CONFLUENCE`)
- Combines independent strategy signals with weighted scoring. Trades are only triggered when cumulative confidence exceeds the configurable threshold (`min_confluence_score: 65.0`).

---

## 🛡️ Centralized Risk Management Engine

All trade proposals are vetted by `RiskEngine` (`trader/risk_engine.py`):
- **Capital**: `₹100,000.00`
- **Max Planned Risk Per Trade**: `1%` of budget (`₹1,000.00`)
- **Max Position Allocation**: `75%` of available free cash
- **Max Open Positions**: `3`
- **Max Trades Per Day**: `3`
- **Daily Loss Kill-Switch**: `₹1,000.00` (blocks all new entries once reached)
- **Short Selling / Leveraged Derivatives**: Disabled by default
- **Strict Hours**: No entries after 15:20 IST; mandatory squareoff by 15:20 IST; daily CSV audit at 15:25 IST.

---

## 🎯 Centralized Exit & Position Management Engine

All positions are managed bar-by-bar by `PositionManager` (`trader/exit_engine.py`):
1. **Initial Stop-Loss**: Enforced without widening.
2. **Partial Profit Booking**: At `+1.0R`, automatically closes `50%` of the position and ratchets the protective stop to **Breakeven (Entry Price)**.
3. **ATR Trailing Stop**: Ratchets upward only (`Highest Price - 1.5 * ATR`).
4. **Dynamic Profit Lock / Peak Reversal**: Locks profit if price drops 0.2% from peak after entering profit.
5. **Mandatory EOD Cutoff**: All positions squared off at 15:20 IST before market close.

---

## 📊 Backtest Comparative Performance Matrix

Deterministic event-driven backtest over realistic intraday price series with 0.05% transaction fees:

| Strategy | Total Trades | Win Rate | Net P&L (₹) | Profit Factor | Max Drawdown | Average R |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **VWAP_PULLBACK** | 2 | **50.0%** | **+₹144.67** | **2.10** | **0.13%** | **+0.17R** |
| **EMA_CHANNEL** | 3 | 33.3% | -₹48.36 | 0.82 | 0.26% | +0.07R |
| **TREND_MOMENTUM** | 3 | 33.3% | -₹361.56 | 0.39 | 0.59% | -0.07R |
| **CONFLUENCE** | 3 | 33.3% | -₹361.56 | 0.39 | 0.59% | -0.07R |
| **ORB** | 3 | 33.3% | -₹520.12 | 0.31 | 0.75% | -0.33R |

*To run the comparative backtest suite locally:*
```bash
python trader/compare_strategies.py
```

---

## 📱 Telegram Bot & Live Web Dashboard

- **Telegram Bot**: Sends real-time alerts to [@Tradesbaba_bot](https://t.me/Tradesbaba_bot) showing strategy name, timestamp, entry price, stop-loss, target (1.5R), quantity, deployed cash, remaining available balance, and realized P&L.
- **Web Dashboard**: Local dark-mode responsive monitor showing Total Portfolio Value, Active Holding Amount, Day's Realized P&L, and Executed Trade Count.
  ```bash
  python dashboard/server.py 8080
  ```

---

## 🧪 Running Tests

To run the complete automated test suite (indicators, strategy setups, risk engine, partial exits, and backtesting):
```bash
python -m unittest tests/test_advanced_engine.py -v
```

---

## ⚙️ Configuration (`trader/config.json`)

```json
{
  "budget": 100000.0,
  "max_position_pct": 75,
  "max_loss_per_trade_usd": 1000.0,
  "max_daily_loss_usd": 1000.0,
  "max_trades_per_day": 3,
  "max_open_positions": 3,
  "strategy_mode": "EMA_CHANNEL",
  "enabled_strategies": ["EMA_CHANNEL", "VWAP_PULLBACK", "ORB", "TREND_MOMENTUM"],
  "min_confluence_score": 65.0,
  "enable_partial_profit": true,
  "partial_profit_ratio": 0.50,
  "atr_period": 14,
  "atr_trailing_multiplier": 1.5,
  "peak_reversal_threshold": 0.002,
  "flat_by": "15:20",
  "paper": true,
  "allow_short": false,
  "currency": "INR",
  "market": "NSE"
}
```

---

## 📄 License
MIT License. Developed for quantitative day trading on Indian Equities (NSE/BSE).
