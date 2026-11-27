# STRATEGY IMPLEMENTATION AUDIT: CONFIGURATION VS. EXECUTION

**Document:** `reports/fno_v4/strategy_implementation_audit.md`  
**Date:** 2026-11-27  
**Auditor:** Quantitative Strategy Auditor & Systems Architect  
**Status:** ✅ COMPLETE AUDIT WITH ARCHITECTURAL DISCREPANCIES IDENTIFIED  

---

## 1. Executive Summary

This audit compares the trading strategies defined in configuration files (`config/v3_config.json`, `trader/config.json`) and advertised in user dashboards against the code actually running in the active F&O trading loop (`trader/fno_trader.py`).

---

## 2. Feature-by-Feature Discrepancy Matrix

| Strategy Dimension | Configured / Advertised Specification | Actual Implementation in `trader/fno_trader.py` | Audit Finding & Recommendation |
| :--- | :--- | :--- | :--- |
| **Strategy Mode** | 8-Strategy Confluence (`BREAKOUT_RETEST`, `EMA_CHANNEL`, `VWAP_PULLBACK`, etc.) in `trader/config.json` | Hardcoded 1-minute Supertrend + EMA 5/9/33 crossover | **Major Discrepancy:** F&O trader runs a monolithic inline script ignoring `trader/strategy_engine.py`. |
| **Indicators Used** | "Supertrend + EMA Channel + VWAP" advertised in startup messages | EMA 5, EMA 9, EMA 33, Supertrend (10, 3.0). **VWAP is completely missing** in `fno_trader.py`. | **Ghost Indicator:** VWAP was never computed in `fno_trader.py`. Wired in V4 Market Regime engine. |
| **Timeframe** | 5m entry + 15m confirmation (`config/v3_config.json`) | Single 1-minute completed bars (`interval="ONE_MINUTE"`) | **Single Timeframe Risk:** 1-minute noise causes excessive stop-outs. V4 multi-timeframe regime engine required. |
| **Daily Trade Quota** | Max 5 trades per day (`v3_config.json`), Max 30 (`config.json`) | Max 30 trades checked in `_execute_option_entry` | Configured to 30. Standardize to 3–5 trades per day for V4. |
| **Cooldown Period** | 15 minutes per underlying index | 900 seconds (15 minutes) enforced in `fno_trader.py` | ✅ Working as configured. |
| **Trading Window** | 09:30–15:20 IST | 09:30–15:10 IST entry window, flat by 15:20 IST | ✅ Working as configured. |
| **StratLab Shields** | OI Wall Guard + IV Rank Guard declared in header | OI Call/Put Wall Guard is active. **IV Rank Guard is never called.** | **Dormant Feature:** Wire IV Rank Guard into contract selection before trade entry. |
| **Vertical Spreads** | `ENABLE_VERTICAL_SPREADS = False` | Resolver implemented in `stratlab_options_engine.py` but bypassed | Working as intended (disabled until single-leg execution is hardened). |

---

## 3. Redesign Recommendations for V4
1. Standardize timeframe to **15-minute market regime** and **5-minute setup confirmation**.
2. Replace single 1-minute Supertrend crossovers with **Model A (EMA Channel Breakout)** and **Model B (Trend Pullback)**.
3. Wire StratLab IV Rank Guard into the entry gate to prevent buying options at peak implied volatility.
