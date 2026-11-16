# ROOT CAUSE ANALYSIS: F&O INTELLIGENCE ENGINE V3 -> V4
**Document:** `reports/fno_v4/root_cause_analysis.md`  
**Date:** 2026-11-16  
**Auditor:** Quantitative Trading Systems Architect & Market Microstructure Specialist  
**Repository:** `https://github.com/Gautam0324/trade`  
**Branch:** `main`  
**Commit SHA:** `c0e17b3da34836d392d02ff039f86ca1799c7add`  
**Status:** COMPLETE (Root Causes Identified & Documented)

---

## 1. Executive Summary

This Root Cause Analysis (RCA) rigorously investigates the core engineering problem:
> **Why did the F&O trading strategy generate massive profits (~₹6.82 Lakhs, 59.6% win rate, Profit Factor 10.32) during the early November testing period, and then suddenly collapse to consecutive losing trades (23.8% win rate, 76.2% loss rate, negative daily expectancy) once live market pricing was connected?**

### The Definitive Finding:
The strategy was **never profitable** under true market microstructure conditions. 
1. **The Synthetic Mirage:** In the early testing phase (Nov 2 – Nov 11), missing broker token mappings caused `fno_trader.py` to fill simulated paper entries at static fallback prices (e.g., fixed **₹220.00** for BANKNIFTY options). Later, when evaluating position exits, the engine queried live broker market LTPs (which were hovering between **₹700.00 and ₹950.00**). Because ₹700+ was dramatically higher than ₹220 * 1.15 (+15% target = ₹253), the engine instantly booked massive artificial profits (+₹15,000 to +₹22,000 per trade).
2. **The Microstructure Reality Shock:** Once real bid/ask quotes were enforced on **both entry and exit** (Nov 12 – Nov 16), the underlying strategy's fatal structural defects were exposed:
   - Operating on a **single 1-minute timeframe** without higher-timeframe trend or regime filters.
   - Setting a rigid **-5% stop-loss** on an option premium. In high-beta index options (NIFTY/BANKNIFTY), normal 1-minute bid-ask bounce and market noise exceed 5% within 120 seconds.
   - Exiting on **1-minute Supertrend reversals**, creating a perpetual whip-saw churn that paid full bid-ask spreads and regulatory turnover costs on every rapid roundtrip.

---

## 2. Root Cause Breakdown by Category

---

### CATEGORY A: Market Data Problems

#### Issue A-1: Local Machine Timestamp Substituted for Missing Exchange Feed Timestamps
- **Severity:** HIGH
- **Affected File:** `trader/fno_trader.py`
- **Function:** `get_option_market_quote()` (Lines 278–286)
- **Exact Failure Mechanism:**
  ```python
  # trader/fno_trader.py lines 278-286
  ts_key = next((k for k in ("feed_time", "timestamp", "quote_timestamp", "exch_feed_time") if k in data and data[k]), None)
  quote_age = 0.0
  quote_ts_str = now_ist
  if ts_key:
      is_valid_ts, quote_age, formatted_ts = parse_and_validate_quote_timestamp(data[ts_key], max_age_seconds=60.0, reference_dt=now_dt)
      ...
  ```
  Angel One SmartAPI's `getLtpData` response schema does **not** return a `feed_time` key in the inner `data` dictionary (it only returns `open`, `high`, `low`, `close`, `ltp`). Consequently, `ts_key` evaluates to `None`.
  The code falls back to `quote_ts_str = now_ist` (local machine clock). This local timestamp is subsequently packaged into `price_provenance` as `"is_realtime": True`. If the market is closed, illiquid, or the broker sends a stale closing tick from the previous session, the engine falsely marks it as fresh live market data (<60s age).
- **Reproduction Test:**
  `tests/test_fno_price_validation.py::TestFnoPriceValidation::test_8_stale_quote_rejection`
- **Recommended Fix (Phase 2):**
  Never substitute local machine time as an exchange feed timestamp. If an API payload lacks an exchange feed timestamp, require Tier 2 (`getMarketData` with `exch_feed_time`) or explicitly flag the quote as unverified exchange time with `quote_age = UNVERIFIED` and enforce strict quote validity checks.

---

#### Issue A-2: Tier 3 Fallback to 1-Minute Candle Close with Zero Bid/Ask Masking Spread
- **Severity:** HIGH
- **Affected File:** `trader/fno_trader.py`
- **Function:** `get_option_market_quote()` (Lines 400–406) and `_execute_option_entry()` (Lines 954–957)
- **Exact Failure Mechanism:**
  When Tier 1 (LTP) and Tier 2 (Market Depth) fail due to rate limits or network latency, the engine falls back to Tier 3 (`getCandleData`). The candle query returns only OHLC values, so the function sets:
  ```python
  "ltp": bar_close,
  "bid": 0.0,
  "ask": 0.0,
  "quote_source": "ANGEL_CANDLE_CLOSE"
  ```
  In `_execute_option_entry`:
  ```python
  if ask > 0 and ask >= ltp:
      premium = ask
  else:
      slippage = round(ltp * 0.002, 2)
      premium = round(ltp + slippage, 2)
  ```
  Because `ask == 0.0`, the simulated fill price is calculated as `LTP + 0.2%`. For out-of-the-money or low-liquidity stock options (such as IDEA or BSE), the real bid-ask spread is frequently 3% to 10% wide. Filling at `LTP + 0.2%` is completely unrealistic and results in immediate mark-to-market losses the moment real depth is received.
- **Recommended Fix (Phase 5):**
  Options must **never** be entered using historical candle close data without depth. If `ask <= 0` or bid-ask depth is unavailable, entry must be rejected with reason code `REJECTED_NO_ORDER_BOOK_DEPTH`.

---

#### Issue A-3: Serial Polling Loop Latency Due to API Rate Limits
- **Severity:** MEDIUM
- **Affected File:** `trader/fno_trader.py`
- **Function:** `run()` (Lines 738–744)
- **Exact Failure Mechanism:**
  To comply with Angel One's 3 requests/second rate limit, a 4.0-second `time.sleep(4.0)` was introduced between each underlying instrument. With 5 underlyings (`NIFTY`, `BANKNIFTY`, `IDEA`, `HDFCBANK`, `BSE`), each iteration of the loop takes 20 to 28 seconds.
  By the time the 5th asset is evaluated, the completed 1-minute candle is already 30 to 50 seconds stale.
- **Recommended Fix (Phase 2):**
  Batch candle queries or decouple underlying scanning from quote evaluation using WebSocket tick ingestion or an asynchronous multi-token scheduler.

---

### CATEGORY B: Strategy Problems (The Primary Root Cause)

#### Issue B-1: The -5% Option Premium Stop-Loss Hair-Trigger
- **Severity:** CRITICAL
- **Affected File:** `trader/fno_trader.py`
- **Function:** `evaluate_option_exit()` (Lines 1139–1142) and constant `STOP_LOSS_PCT = 0.05`
- **Exact Failure Mechanism:**
  In equity options, delta ($\Delta$) determines the premium change relative to the underlying index movement:
  $$\Delta_{\text{ATM}} \approx 0.50$$
  For NIFTY trading at 25,200 with an ATM option premium of ₹180:
  - A **-5% stop loss on premium** is triggered when premium drops by:
    $$\Delta P = 180 \times 0.05 = \text{₹9.00}$$
  - Since $\Delta \approx 0.50$, an index movement of only **18 points** ($18 \times 0.50 = \text{₹9.00}$) triggers the stop-loss:
    $$\frac{18}{25,200} = 0.07\% \text{ index movement!}$$
  In the Indian derivative market, NIFTY 50 fluctuates 15 to 25 points within a normal 1-minute candle simply due to bid-ask bounce and index constituent recalculations.
  Setting a **-5% stop loss on an option premium** is an engineering design flaw: it guarantees that normal microstructural noise will stop out the trade within 2 to 5 minutes of entry.
- **Data Evidence from `trader/ledger_fno.json`:**
  Under live pricing on Nov 13 and Nov 16:
  - 13 out of 16 losing trades (81.25%) were closed by `🛑 Stop-Loss Hit (-5%)`.
  - Trade durations before stop-out:
    - Trade 4 (`NIFTY17NOV2625200CE`): Entered 10:09 -> Stopped out 10:11 (Duration: **2 minutes**)
    - Trade 11 (`NIFTY17NOV2625150CE`): Entered 13:19 -> Stopped out 13:21 (Duration: **2 minutes**)
    - Trade 21 (`BANKNIFTY23NOV2661600CE`): Entered 12:35 -> Stopped out 12:38 (Duration: **3 minutes**)
- **Recommended Fix (Phase 8):**
  Option stop-loss must be calculated based on **underlying market structure / ATR levels**, not an arbitrary fixed percentage of premium. If an option-level percentage is used, it must be calibrated to the asset's implied volatility and ATR (e.g., 1.5x 15m ATR mapped via delta, typically 15%–25% premium tolerance with strict R:R >= 1:2).

---

#### Issue B-2: 1-Minute Supertrend Reversal Whip-Sawing
- **Severity:** HIGH
- **Affected File:** `trader/fno_trader.py`
- **Function:** `evaluate_option_exit()` (Lines 1144–1146)
- **Exact Failure Mechanism:**
  ```python
  if (pos["option_type"] == "CE" and st_dir == -1) or (pos["option_type"] == "PE" and st_dir == 1):
      self.exit_position(opt_sym, f"🔄 Supertrend Reversal on Underlying Spot @ Rs {current_premium:.2f}", current_premium)
  ```
  Supertrend(10, 3.0) on a 1-minute chart changes direction frequently whenever price enters a narrow trading band. When price consolidates in a 20-point zone for 30 minutes, 1-minute Supertrend flips 4 to 6 times.
  Every flip triggers an exit at the Bid price, incurring full slippage and exchange fees. The bot then re-enters 3 minutes later on the next flip, producing consecutive churn losses.
- **Data Evidence:**
  Trades 2, 9, 13, and 14 in `ledger_fno.json` exited with `Supertrend Reversal on Underlying Spot`, generating net losses after regulatory fees even when gross P&L was flat.
- **Recommended Fix (Phase 2 & 3):**
  Supertrend direction must be determined on **15-minute and 5-minute candles**. The 1-minute chart must only be used for entry trigger execution, never for primary trend invalidation.

---

#### Issue B-3: Complete Absence of Multi-Timeframe Confirmation (MTF)
- **Severity:** HIGH
- **Affected File:** `trader/fno_trader.py`
- **Function:** `run()` (Lines 800–814)
- **Exact Failure Mechanism:**
  Entry is evaluated solely on 1-minute completed bars:
  `st_dir == 1 and ema5 > ema9 > ema33 and spot_c > ema9`
  There is zero awareness of higher timeframes:
  - If NIFTY is in a brutal 15-minute downtrend, a minor 2-minute green bounce causes 1-minute `ema5 > ema9 > ema33`.
  - The bot immediately buys a CE call option at the Ask.
  - One minute later, the dominant 15-minute downtrend resumes, instantly triggering the -5% stop loss.
- **Recommended Fix (Phase 3):**
  Implement strict Multi-Timeframe (MTF) gating:
  - 15m: Primary market trend (EMA 20/50 + Market Structure).
  - 5m: Setup confirmation (Pullback to VWAP / EMA Channel).
  - 1m: Execution trigger only.
  If 15m and 5m are not aligned, decision must be `WAIT`.

---

#### Issue B-4: Complete Absence of Market Regime & Sideways Filters
- **Severity:** HIGH
- **Affected File:** `trader/fno_trader.py`
- **Function:** `run()` (Lines 800–814)
- **Exact Failure Mechanism:**
  Moving average crossover strategies (`EMA 5/9/33`) are inherently trend-following systems. In Indian index markets, price consolidates in a sideways range 65% to 70% of the time.
  In a sideways market, moving average crossovers are guaranteed loss generators. `fno_trader.py` contains:
  - No ADX indicator (trend strength).
  - No ATR volatility ratio (expansion vs. contraction).
  - No consolidation zone detection.
  The bot continuously forces entries throughout choppy midday sessions (11:30 to 13:30 IST).
- **Recommended Fix (Phase 2):**
  Implement `trader/fno_v4/market_regime.py` classifying markets into `STRONG_TREND`, `WEAK_TREND`, `SIDEWAYS`, and `NO_TRADE`. Block all F&O entries during `SIDEWAYS` and `NO_TRADE` regimes.

---

#### Issue B-5: Phantom Indicators (VWAP Claimed But Not Present)
- **Severity:** MEDIUM
- **Affected File:** `trader/fno_trader.py`
- **Function:** `run()` (Line 711 startup message)
- **Exact Failure Mechanism:**
  The engine sends Telegram alerts and console banners stating:
  `🎯 Strategy: Intraday Confluence Momentum (Supertrend + EMA Channel + VWAP)`
  In reality, VWAP is **never calculated, imported, or checked** anywhere in `fno_trader.py`.
- **Recommended Fix (Phase 3 & 6):**
  Integrate mathematically verified session VWAP calculated with tick volume.

---

#### Issue B-6: Blind ATM Contract Selection Without Greeks or Liquidity Checks
- **Severity:** HIGH
- **Affected File:** `trader/fno_trader.py`
- **Function:** `resolve_atm_contract()` (Lines 95–152)
- **Exact Failure Mechanism:**
  The function computes `round(spot_price / step) * step` and picks the first matching option from the contract master.
  It does **not** evaluate:
  - Delta ($\Delta$): It assumes ATM delta is always appropriate.
  - Implied Volatility (IV): It buys options during IV crush or high IV expansion without adjustment.
  - Bid-Ask Spread: It does not check if the spread is >2% of premium.
  - Traded Volume & Open Interest: It enters illiquid stock option strikes where market makers quote wide 10% spreads.
- **Recommended Fix (Phase 5):**
  Implement `trader/fno_v4/options_intelligence.py` with multi-strike liquidity scoring, spread threshold filtering (<1.5%), and delta-targeted selection.

---

### CATEGORY C: Execution Problems

#### Issue C-1: Execution Drag from Full Spread Crossing on Rapid Exits
- **Severity:** HIGH
- **Affected File:** `trader/fno_trader.py`
- **Function:** `_execute_option_entry()` line 952 and `_execute_exit_with_retry()` line 1213
- **Exact Failure Mechanism:**
  - Entry Fill: `premium = ask` (taker buys at top of book)
  - Exit Fill: `confirmed_exit_price = bid` (taker sells at bottom of book)
  On a contract trading at Bid ₹100.00 / Ask ₹101.50:
  - Entry cost: ₹101.50
  - Immediate exit value: ₹100.00
  - Instant spread loss: **-1.48%**
  With a -5% stop-loss threshold, the position is already 30% of the way to its stop loss at the moment of fill! A tiny adverse tick of ₹3.50 triggers the remaining 3.52%, hitting the -5% stop loss.
- **Recommended Fix (Phase 9):**
  Factor bid-ask spread into the entry filter. If `(ask - bid) / ltp > 0.015` (spread > 1.5%), reject entry.

---

#### Issue C-2: Sizing Invariance (Fixed 1 Lot Sizing Decoupled from Capital & Risk)
- **Severity:** CRITICAL
- **Affected File:** `trader/fno_trader.py`
- **Function:** `enter_option_trade()` (Line 828)
- **Exact Failure Mechanism:**
  `qty = contract.get("lotsize", 0)`
  Position sizing does not calculate risk per trade.
  - On `IDEA` (lot size: 71,475), an option priced at ₹1.20 commits **₹85,770** of cash (42.8% of the entire ₹2,00,000 F&O account capital!).
  - On `BANKNIFTY` (lot size: 30), an option priced at ₹600 commits **₹18,000** (9.0% of capital).
  A single losing trade on a high-lot stock option can wipe out weeks of index option gains.
- **Recommended Fix (Phase 8):**
  Implement dynamic position sizing in `trader/fno_v4/adaptive_risk.py` based on maximum rupee risk per trade:
  $$\text{Shares} = \min\left(\text{Lotsize} \times \left\lfloor \frac{\text{Max Risk INR}}{\text{Stop Loss INR per share}} \div \text{Lotsize} \right\rfloor, \text{Max Deployed INR}\right)$$
  If 1 lot exceeds the maximum allowable rupee risk, the trade must be **rejected**.

---

### CATEGORY D: Historical Performance Problems (The Mirage Explained)

#### Issue D-1: Synthetic Fallback Entry vs. Real Market Exit Divergence
- **Severity:** CRITICAL
- **Affected File:** `trader/ledger_fno.json` (Historical Trades Nov 2 – Nov 11)
- **Exact Failure Mechanism:**
  During the early implementation phase, when broker option quotes were unavailable or unmapped, the engine used synthetic fixed prices (`220.0` for BankNifty options, `85.0` for Nifty options) for entry simulation.
  However, position tracking queried actual broker LTPs for exit evaluation.
  
  **Real Evidence from `trader/ledger_fno.json`:**
  - Trade ID: `OPT-1793769763161-BANKNIFTY23NOV2655700CE` (Date: 2026-11-04)
    - Entry Price: **₹220.00** (Synthetic fallback)
    - Exit Price: **₹711.65** (Actual market quote)
    - Recorded P&L: **+₹14,664.27**
    - Recorded Reason: `🎯 Target Hit (+15% R:R) @ Rs 711.65`
  - Trade ID: `OPT-1793774640102-BANKNIFTY23NOV2654800CE` (Date: 2026-11-02)
    - Entry Price: **₹220.00** (Synthetic fallback)
    - Exit Price: **₹955.00** (Actual market quote)
    - Recorded P&L: **+₹21,953.16**
  
  The engine bought at an imaginary price of ₹220, and then upon the very next market poll, saw the real price was ₹700–₹950, which was > ₹253 (+15% target). It immediately logged an artificial +₹14,000 to +₹22,000 win!
  This defect fabricated hundreds of thousands of rupees in synthetic paper gains.
  The moment real market prices were enforced on both entry and exit, this synthetic arbitrage disappeared, exposing the 23.8% win rate of the underlying 1-minute strategy.
- **Recommended Action:**
  Quarantine all pre-Nov 12 trades in `reports/fno_v4/data_quality_report.md` as synthetic/unverified baseline data. Never cite them as evidence of strategy viability.

---

## 3. Summary of Failure Mechanisms

```
+---------------------------------------------------------------------------------------------------------+
|                                    WHY THE STRATEGY COLLAPSED IN LIVE PRICING                            |
+---------------------------------------------------------------------------------------------------------+
| 1. HISTORICAL ILLUSION : Early "profits" were an artifact of synthetic ₹220 entries exiting at ₹700+.  |
| 2. TIME-FRAME NOISE    : Strategy traded 1-minute EMA crossovers without higher-timeframe trend context. |
| 3. HAIR-TRIGGER SL     : -5% option premium stop-loss was smaller than normal 1-minute bid-ask noise.   |
| 4. CHURN & WHIP-SAW    : 1-minute Supertrend reversals forced exits at the Bid every 5-10 minutes.      |
| 5. SPREAD & FEE DRAG   : Roundtrip Bid/Ask spread + ₹55-₹85 regulatory fees drained capital.            |
| 6. ZERO REGIME AWARENESS: Traded trending rules during sideways range-bound market sessions.            |
| 7. ZERO GREEK / SPREAD : Blindly bought ATM strikes without checking Delta, IV, or order book liquidity.|
| 8. INVARIANT SIZING    : Fixed 1-lot sizing committed >40% of capital on stock options (IDEA/BSE).      |
+---------------------------------------------------------------------------------------------------------+
```

---

## 4. Phase 1 Verification Evidence Table

| Root Cause ID | Severity | File | Function / Lines | Tested & Confirmed? | Evidence / Impact |
| :--- | :---: | :--- | :--- | :---: | :--- |
| **RCA-01** | CRITICAL | `ledger_fno.json` | Historical entries | **CONFIRMED** | Entry ₹220.00 vs Exit ₹711.65–₹955.00 generated fake +₹6.82L profit. |
| **RCA-02** | CRITICAL | `fno_trader.py` | Line 980, Line 1139 | **CONFIRMED** | -5% SL triggered in 2–5 minutes on 81.2% of live losing trades. |
| **RCA-03** | HIGH | `fno_trader.py` | Lines 1144–1146 | **CONFIRMED** | 1m Supertrend reversal caused repeated chop exits at Bid. |
| **RCA-04** | HIGH | `fno_trader.py` | Lines 748–775 | **CONFIRMED** | Only 1-minute candles used; 15m and 5m MTF completely absent. |
| **RCA-05** | HIGH | `fno_trader.py` | Lines 278–286 | **CONFIRMED** | Missing `feed_time` substituted with local clock `now_ist`. |
| **RCA-06** | HIGH | `fno_trader.py` | Lines 400–406 | **CONFIRMED** | Tier 3 sets bid/ask to 0.0; simulated fill ignores spread. |
| **RCA-07** | HIGH | `fno_trader.py` | Line 828 | **CONFIRMED** | 1 lot fixed sizing (e.g., IDEA 71,475 qty) violates risk limits. |
| **RCA-08** | MEDIUM | `fno_trader.py` | Line 711 | **CONFIRMED** | VWAP advertised in logs/alerts but never calculated. |
| **RCA-09** | HIGH | `fno_trader.py` | Lines 800–814 | **CONFIRMED** | No regime filter; trades moving average cross in sideways chop. |

---

## 5. Architectural Blueprint for Phase 2 – 14 (V4 Engine)

Based on these confirmed root causes, the V4 F&O Intelligence Engine will implement the following decoupled modules in `trader/fno_v4/`:

1. **`market_regime.py` (Phase 2):** Quantitatively classify market state (`STRONG_TREND`, `SIDEWAYS`, `HIGH_VOLATILITY`, `NO_TRADE`) using ADX, ATR ratio, and EMA slope. Explicitly support `NO_TRADE`.
2. **`multi_timeframe.py` (Phase 3):** 15-minute primary trend + 5-minute setup confirmation. 1-minute candles used **only** for entry triggers.
3. **`market_structure.py` (Phase 4):** Deterministic Higher High/Higher Low, Break of Structure (BOS), and swing level detection without look-ahead bias.
4. **`options_intelligence.py` (Phase 5):** Multi-strike liquidity scoring, spread threshold filter (<1.5%), Delta targeting (0.45–0.60), and Black-Scholes Greeks derivation.
5. **`confluence_engine.py` (Phase 6):** Weighted multi-factor evidence scoring with missing-data fallbacks.
6. **`decision_engine.py` (Phase 7):** Deterministic decision generation (`BUY_CE`, `BUY_PE`, `WAIT`, `NO_TRADE`) with machine-readable reason codes.
7. **`adaptive_risk.py` (Phase 8):** Risk-normalized position sizing based on maximum rupee risk; stop-loss aligned with underlying ATR/structure (15%–25% premium buffer, R:R >= 1:2).
8. **`execution_engine.py` (Phase 9):** Formal 11-state order lifecycle with idempotent retries, partial fill handling, and hard `PAPER_ONLY = True` guard.
9. **`trade_audit.py` (Phase 10):** End-to-end explainability and daily audit telemetry.

