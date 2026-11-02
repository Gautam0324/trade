# DAY TRADER PRO -- V3 STRATEGY REPORT (PHASE 6)

**Date:** 2026-11-02  
**Branch:** `feature/trading-v3`  
**Strategy ID:** `V3_TREND_PULLBACK`  
**Status:** IMPLEMENTED, TESTED & DISABLED BY DEFAULT  

---

## 1. STRATEGY SPECIFICATION

The **V3 Trend Pullback** engine is an independent, institutional-grade quantitative strategy designed to trade pullbacks within high-probability established trends while strictly eliminating over-trading and premature wick stop-outs.

* **Primary Timeframe:** 5-minute completed bars.
* **Higher-Timeframe (HTF) Confirmation:** 15-minute completed bars.
* **Execution Mode:** STRICT PAPER TRADING ONLY.
* **Default State:** `enabled: false` (Safely disabled by default in `config/v3_config.json`).
* **Existing Strategies Preserved:** All 4 legacy strategies (`EMA_CHANNEL`, `TREND_MOMENTUM`, `VWAP_PULLBACK`, `CONFLUENCE`) remain 100% active and unedited.

---

## 2. QUANTITATIVE RULES

### A. 15-Minute Higher-Timeframe Trend Confirmation
* **Bullish Trend:**
  * EMA 9 > EMA 21
  * 15m Close > EMA 50
  * ADX(14) > 20.0 (Confirms directional trend strength; eliminates sideways chop)
* **Bearish Trend:**
  * EMA 9 < EMA 21
  * 15m Close < EMA 50
  * ADX(14) > 20.0

### B. 5-Minute Primary Pullback Entry
* **Bullish Pullback (LONG):**
  1. 15m Trend confirmed **BULLISH**.
  2. 5m Candle tests into EMA 9 or EMA 21 pullback zone.
  3. 5m Candle respects support and closes as a bullish reversal candle.
  4. 5m Close > VWAP.
  5. RSI(14) between 35 and 75 (Not overextended).
  6. Volatility-Based Stop-Loss: $\text{Recent Swing Low} - (1.5 \times \text{ATR})$.
  7. Targets: **1R** (Partial) and **2R** (Full).
* **Bearish Pullback (SHORT):**
  1. 15m Trend confirmed **BEARISH**.
  2. 5m Candle tests into EMA 9 or EMA 21 resistance zone.
  3. 5m Candle confirms bearish reversal close.
  4. 5m Close < VWAP.
  5. RSI(14) between 25 and 65.
  6. Volatility-Based Stop-Loss: $\text{Recent Swing High} + (1.5 \times \text{ATR})$.
  7. Targets: **1R** and **2R**.

---

## 3. AUTOMATED TEST SUITE

Created comprehensive unit test suite in `tests/test_v3_strategy.py`:
1. `test_candle_aggregation`: Validates chronological 1m to 5m/15m aggregation without lookahead bias.
2. `test_sideways_market_rejection_low_adx`: Rejects trades when ADX < 20.
3. `test_15m_bullish_trend_detection`: Verifies bullish trend alignment on 15m timeframe.
4. `test_15m_bearish_trend_detection`: Verifies bearish trend alignment on 15m timeframe.
5. `test_full_bullish_pullback_signal`: Verifies full end-to-end signal generation, stop loss, and 1:2 R:R targets.

**Result:** All 5 tests passed (100% OK).
