# 📈 Automated Day Trader System (NSE & BSE)

> **Autonomous Algo-Trading Engine for Indian Equities (NSE/BSE)**  
> Built with strict risk management, EMA Channel Breakout Strategy, Dynamic Trailing Profit Locking, Live Web Dashboard, and Telegram Bot integration.

---

## 👨‍💻 Author & Maintainer
- **Lead Developer**: **Gautam** ([@Gautam0324](https://github.com/Gautam0324))
- **Repository**: [https://github.com/Gautam0324/trade.git](https://github.com/Gautam0324/trade.git)
- **Currency**: `₹ INR`
- **Exchanges**: National Stock Exchange (`NSE`) & Bombay Stock Exchange (`BSE`)

---

## 🚀 Key Highlights & Strategy Architecture

### 1. 📐 Indicator Strategy: EMA 5 / 9 / 33 Channel
- **EMA 5 (Close)**: Ultra-fast momentum trigger line.
- **EMA 9 (Close)**: Baseline trend confirmation & dynamic support.
- **EMA 33 (High)**: Upper channel envelope — buy breakout confirmation.
- **EMA 33 (Low)**: Lower channel envelope — dynamic trailing floor.

### 2. 🎯 Trailing Profit-Lock Exit (The "Toffee Rule")
- **Dynamic Profit Protection**: Jaise hi position profit me aati hai, profit locking mechanism activate ho jata hai.
- **0.2% Peak Reversal Exit**: Price apne peak se 0.2% bhi girta hai ya EMA 5 ke niche aata hai, system turant exit karke profit lock kar leta hai.
- **Stop-Loss Protection**: Agar price EMA 9 baseline ke niche slip karta hai, trade turant risk management ke saath exit ho jata hai.

### 3. 🛡️ Capital & Risk Discipline
- **Starting Budget**: `₹1,00,000` (Strictly bounded).
- **Max Risk per Trade**: 1% of budget (`₹1,000.00`).
- **Position Sizing**: Dynamically calculated based on distance from EMA 33 Low stop-loss, capped at 75% of available cash.
- **Hard Trade Hours Cutoff**: 
  - **15:20 IST**: All open intraday positions are automatically squared off. No new entries allowed after 15:20.
  - **15:25 IST**: Audit-ready CSV daily trade journal generated & sent to Telegram.

---

## 📱 Telegram Bot Integration
Real-time alerts sent to user's Telegram bot ([@Tradesbaba_bot](https://t.me/Tradesbaba_bot)) containing:
- Entry / Exit Date & Time (IST)
- Exact Trade Amount & Quantity
- Entry Price, Stop-Loss & Target
- Remaining Available Free Cash & Realized P&L

---

## 📊 Live Web Dashboard
A modern dark-mode responsive dashboard displaying 4 primary KPIs:
1. **Total Portfolio Value**
2. **Active Holding Amount**
3. **Day's Realized P&L**
4. **Today's Executed Trade Count**

To start the local dashboard:
```bash
python dashboard/server.py 8080
```
Visit `http://localhost:8080` in your browser.

---

## 🛠️ Project Structure

```
trade/
├── dashboard/                 # Web Dashboard
│   ├── index.html            # UI Structure
│   ├── styles.css            # Dark Mode Design System
│   ├── app.js                # Live Polling & Rendering
│   └── server.py             # Lightweight HTTP Server
├── trader/                    # Core Trading Engine & Accounting
│   ├── config.json           # Currency, Budget, & Telegram Credentials
│   ├── ledger.py             # Fund Accounting & Dynamic Balance Tracker
│   ├── ledger.json           # Live Ledger Storage
│   └── export_csv.py         # Daily CSV Journal & EOD Telegram Summary
├── trader-scanner/            # Market Scanner & Daemon
│   ├── daemon.py             # Continuous 15s Scanner Loop (15:20 cutoff)
│   ├── ema_strategy.py       # EMA 5, 9, 33 High/Low Strategy Math
│   └── scan.py               # Live Quote Fetcher
├── trader-alerts/             # Notification Dispatcher
│   └── telegram_bot.py       # Telegram Bot Alert Handlers
└── tests/                     # Test Suite (315 Unit Tests)
```

---

## ⚡ Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt # standard library + urllib
```

### 2. Run Test Suite
```bash
python -m unittest discover -s tests -v
```

### 3. Launch Scanner Daemon
```bash
python trader-scanner/daemon.py
```

### 4. Generate Daily Audit CSV
```bash
python trader/export_csv.py
```

---

## 📄 License
MIT License. Built for autonomous algorithmic trading on Indian Markets.
