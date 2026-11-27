"""
Dual-Direction Options Strategy Engine (F&O Version 4.0).

Supports all 4 Core Strategies:
1. BUY CE (Bullish):
   - Strong uptrend (Supertrend Bullish, EMA 5>9>33, Spot > DayOpen, Spot > EMA9).
   - High momentum, breaking resistance or expanding volatility.
2. BUY PE (Bearish):
   - Strong downtrend (Supertrend Bearish, EMA 5<9<33, Spot < DayOpen, Spot < EMA9).
   - High downside momentum, breaking support.
3. SELL CE (Bearish / High IV Mean Reversion / Call Wall Rejection):
   - Market Bearish or Rangebound below DayOpen / Call Wall resistance.
   - IV Rank Elevated or Momentum Exhaustion -> Short OTM/ATM CE to harvest theta & delta decay.
4. SELL PE (Bullish / High IV Mean Reversion / Put Wall Bounce):
   - Market Bullish or Rangebound above DayOpen / Put Wall support.
   - IV Rank Elevated or Momentum Exhaustion -> Short OTM/ATM PE to harvest theta & delta decay.

Configuration Modes:
- 'BUY_ONLY': Traditional options buying only.
- 'BUY_AND_SELL': Intelligently selects between buying and selling based on regime, trend strength, and IV.
- 'SELL_ONLY': Exclusively sells options for premium decay.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class OptionSignal:
    should_trade: bool
    action: str              # 'BUY' or 'SELL'
    option_type: str         # 'CE' or 'PE'
    strategy_name: str       # 'BUY_CE', 'BUY_PE', 'SELL_CE', 'SELL_PE'
    market_bias: str         # 'BULLISH', 'BEARISH', 'NEUTRAL'
    confidence: float        # Confluence score 0.0 to 1.0
    reason: str
    suggested_strike_type: str  # 'ATM', 'OTM_1', 'ITM_1'
    risk_reward_ratio: float


class DualDirectionStrategyEngine:
    """
    Evaluates multi-timeframe confluence and market regime to generate
    high-probability directional option signals across all 4 strategy types.
    """

    @classmethod
    def evaluate_market_condition(
        cls,
        spot_price: float,
        day_open: float,
        st_dir: int,
        ema5: float,
        ema9: float,
        ema33: float,
        trading_mode: str = "BUY_AND_SELL",
        iv_rank: Optional[float] = None,
        call_wall: Optional[float] = None,
        put_wall: Optional[float] = None,
        oi_pcr: Optional[float] = None
    ) -> OptionSignal:
        """
        Evaluates underlying price action and derivatives structure to determine optimal strategy.
        """
        trading_mode = (trading_mode or "BUY_AND_SELL").upper()

        is_bull_trend = (spot_price >= day_open) and (spot_price >= ema33)
        is_bear_trend = (spot_price < day_open) and (spot_price < ema33)
        is_strong_bull_momentum = (st_dir == 1) and (ema5 > ema9 > ema33) and (spot_price > ema9)
        is_strong_bear_momentum = (st_dir == -1) and (ema5 < ema9 < ema33) and (spot_price < ema9)

        # Volatility condition: IV Rank > 50 or Chop favors option selling
        high_iv = (iv_rank is not None and iv_rank >= 50.0)

        # -------------------------------------------------------------
        # 1. BULLISH SCENARIOS
        # -------------------------------------------------------------
        if is_bull_trend and is_strong_bull_momentum:
            # If trading mode allows buying, Strong Momentum favors BUY CE
            if trading_mode in ("BUY_ONLY", "BUY_AND_SELL") and not high_iv:
                return OptionSignal(
                    should_trade=True,
                    action="BUY",
                    option_type="CE",
                    strategy_name="BUY_CE",
                    market_bias="BULLISH",
                    confidence=0.85,
                    reason="Strong Bullish Momentum (Supertrend + EMA Channel 5>9>33 + Bull Regime Alignment)",
                    suggested_strike_type="ATM",
                    risk_reward_ratio=3.0
                )
            # If High IV or SELL_ONLY mode: SELL PE (Bullish theta decay)
            elif trading_mode in ("SELL_ONLY", "BUY_AND_SELL"):
                return OptionSignal(
                    should_trade=True,
                    action="SELL",
                    option_type="PE",
                    strategy_name="SELL_PE",
                    market_bias="BULLISH",
                    confidence=0.80,
                    reason="Bullish Trend with High IV / Premium Harvest (Short PE at Put Wall Support)",
                    suggested_strike_type="OTM_1" if put_wall else "ATM",
                    risk_reward_ratio=2.0
                )

        # Moderate Bullish Bounce / Put Wall Support holding
        if is_bull_trend and put_wall and spot_price >= put_wall and trading_mode in ("SELL_ONLY", "BUY_AND_SELL"):
            if st_dir == 1 or spot_price > ema9:
                return OptionSignal(
                    should_trade=True,
                    action="SELL",
                    option_type="PE",
                    strategy_name="SELL_PE",
                    market_bias="BULLISH",
                    confidence=0.75,
                    reason="Put Wall Support Defense (Short PE below Put Wall for Theta Decay)",
                    suggested_strike_type="OTM_1",
                    risk_reward_ratio=2.0
                )

        # -------------------------------------------------------------
        # 2. BEARISH SCENARIOS
        # -------------------------------------------------------------
        if is_bear_trend and is_strong_bear_momentum:
            # If trading mode allows buying, Strong Momentum favors BUY PE
            if trading_mode in ("BUY_ONLY", "BUY_AND_SELL") and not high_iv:
                return OptionSignal(
                    should_trade=True,
                    action="BUY",
                    option_type="PE",
                    strategy_name="BUY_PE",
                    market_bias="BEARISH",
                    confidence=0.85,
                    reason="Strong Bearish Momentum (Supertrend + EMA Channel 5<9<33 + Bear Regime Alignment)",
                    suggested_strike_type="ATM",
                    risk_reward_ratio=3.0
                )
            # If High IV or SELL_ONLY mode: SELL CE (Bearish theta decay)
            elif trading_mode in ("SELL_ONLY", "BUY_AND_SELL"):
                return OptionSignal(
                    should_trade=True,
                    action="SELL",
                    option_type="CE",
                    strategy_name="SELL_CE",
                    market_bias="BEARISH",
                    confidence=0.80,
                    reason="Bearish Trend with High IV / Premium Harvest (Short CE at Call Wall Resistance)",
                    suggested_strike_type="OTM_1" if call_wall else "ATM",
                    risk_reward_ratio=2.0
                )

        # Moderate Bearish Rejection / Call Wall Resistance holding
        if is_bear_trend and call_wall and spot_price <= call_wall and trading_mode in ("SELL_ONLY", "BUY_AND_SELL"):
            if st_dir == -1 or spot_price < ema9:
                return OptionSignal(
                    should_trade=True,
                    action="SELL",
                    option_type="CE",
                    strategy_name="SELL_CE",
                    market_bias="BEARISH",
                    confidence=0.75,
                    reason="Call Wall Resistance Rejection (Short CE above Call Wall for Theta Decay)",
                    suggested_strike_type="OTM_1",
                    risk_reward_ratio=2.0
                )

        # -------------------------------------------------------------
        # 3. NO TRADE / REGIME MISALIGNMENT
        # -------------------------------------------------------------
        no_trade_reason = "No clear directional confluence."
        if st_dir == -1 and is_bull_trend:
            no_trade_reason = "Supertrend bearish but Market is in Bull Regime (Spot >= DayOpen). Counter-trend trade blocked."
        elif st_dir == 1 and is_bear_trend:
            no_trade_reason = "Supertrend bullish but Market is in Bear Regime (Spot < DayOpen). Counter-trend trade blocked."

        return OptionSignal(
            should_trade=False,
            action="NONE",
            option_type="NONE",
            strategy_name="NO_TRADE",
            market_bias="NEUTRAL",
            confidence=0.0,
            reason=no_trade_reason,
            suggested_strike_type="NONE",
            risk_reward_ratio=0.0
        )
