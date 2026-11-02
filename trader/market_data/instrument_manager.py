"""
Instrument Manager for Day Trader Pro V2.
Maintains authoritative mapping between exchange tokens, symbols, and instrument metadata.
"""

from typing import Dict, Optional, List

class InstrumentManager:
    DEFAULT_WATCHLIST = [
        {"symbol": "JPL", "token": "766830", "exchange": "NSE", "name": "JPL (Newly Listed)"},
        {"symbol": "TCS", "token": "11536", "exchange": "NSE", "name": "Tata Consultancy Services"},
        {"symbol": "RELIANCE", "token": "2885", "exchange": "NSE", "name": "Reliance Industries"},
        {"symbol": "INFY", "token": "1594", "exchange": "NSE", "name": "Infosys"},
        {"symbol": "HDFCBANK", "token": "1333", "exchange": "NSE", "name": "HDFC Bank"},
        {"symbol": "SBIN", "token": "3045", "exchange": "NSE", "name": "State Bank of India"},
        {"symbol": "ICICIBANK", "token": "4963", "exchange": "NSE", "name": "ICICI Bank"},
        {"symbol": "LT", "token": "11483", "exchange": "NSE", "name": "Larsen & Toubro"},
        {"symbol": "TATASTEEL", "token": "3499", "exchange": "NSE", "name": "Tata Steel"},
        {"symbol": "BAJFINANCE", "token": "317", "exchange": "NSE", "name": "Bajaj Finance"},
        {"symbol": "ITC", "token": "1660", "exchange": "NSE", "name": "ITC Limited"},
        {"symbol": "AXISBANK", "token": "5900", "exchange": "NSE", "name": "Axis Bank"},
        {"symbol": "WIPRO", "token": "3787", "exchange": "NSE", "name": "Wipro Limited"}
    ]

    def __init__(self, initial_instruments: Optional[List[Dict]] = None):
        self._by_token: Dict[str, Dict] = {}
        self._by_symbol: Dict[str, Dict] = {}
        
        instruments = initial_instruments or self.DEFAULT_WATCHLIST
        for inst in instruments:
            self.register_instrument(inst)

    def register_instrument(self, inst: Dict):
        token = str(inst.get("token", "")).strip()
        symbol = str(inst.get("symbol", "")).strip().upper()
        exchange = str(inst.get("exchange", "NSE")).strip().upper()
        record = {
            "token": token,
            "symbol": symbol,
            "exchange": exchange,
            "name": inst.get("name", symbol),
            "lot_size": int(inst.get("lot_size", 1)),
            "tick_size": float(inst.get("tick_size", 0.05))
        }
        if token:
            self._by_token[token] = record
        if symbol:
            self._by_symbol[symbol] = record

    def get_by_token(self, token: str) -> Optional[Dict]:
        return self._by_token.get(str(token).strip())

    def get_by_symbol(self, symbol: str) -> Optional[Dict]:
        return self._by_symbol.get(str(symbol).strip().upper())

    def get_symbol_for_token(self, token: str) -> Optional[str]:
        inst = self.get_by_token(token)
        return inst["symbol"] if inst else None

    def get_token_for_symbol(self, symbol: str) -> Optional[str]:
        inst = self.get_by_symbol(symbol)
        return inst["token"] if inst else None

    def get_all_symbols(self) -> List[str]:
        return list(self._by_symbol.keys())

    def get_all_tokens(self) -> List[str]:
        return list(self._by_token.keys())

    def get_token_list_by_exchange(self, exchange: str = "NSE") -> List[str]:
        return [inst["token"] for inst in self._by_token.values() if inst["exchange"] == exchange.upper()]

