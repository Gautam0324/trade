"""
Unit tests for WebSocket Manager, Authentication, and Stale Feed Handling.
"""

import unittest
from unittest.mock import MagicMock, patch
from trader.market_data.instrument_manager import InstrumentManager
from trader.market_data.price_store import PriceStore
from trader.market_data.candle_builder import CandleBuilder
from trader.market_data.connection_monitor import ConnectionMonitor, ConnectionState
from trader.market_data.websocket_manager import SmartWebSocketManager

class TestWebSocketManager(unittest.TestCase):
    def setUp(self):
        self.im = InstrumentManager([
            {"symbol": "INFY", "token": "1594", "exchange": "NSE"}
        ])
        self.price_store = PriceStore(stale_threshold_secs=0.5)
        self.candle_builder = CandleBuilder()
        self.monitor = ConnectionMonitor()
        self.mock_client = MagicMock()
        self.mock_client.get_session_info.return_value = {
            "authenticated": True,
            "client_code": "TEST1234",
            "api_key": "TESTKEY",
            "jwt_token": "JWT_MOCK",
            "feed_token": "FEED_MOCK"
        }

        self.ws_manager = SmartWebSocketManager(
            angel_client=self.mock_client,
            instrument_manager=self.im,
            price_store=self.price_store,
            candle_builder=self.candle_builder,
            connection_monitor=self.monitor
        )

    def test_on_open_subscribes_tokens(self):
        mock_ws = MagicMock()
        self.ws_manager.ws = mock_ws
        self.ws_manager._on_ws_open(mock_ws)

        self.assertEqual(self.monitor.state, ConnectionState.CONNECTED)
        mock_ws.subscribe.assert_called_once()
        args = mock_ws.subscribe.call_args[0]
        # args[2] is token_list: [{'exchangeType': 1, 'tokens': ['1594']}]
        self.assertEqual(args[2][0]["tokens"], ["1594"])

    def test_on_data_processes_valid_tick(self):
        valid_packet = {
            "token": "1594",
            "last_traded_price": 117000,
            "subscription_mode": 2,
            "sequence_number": 1
        }
        mock_ws = MagicMock()
        self.ws_manager._on_ws_data(mock_ws, valid_packet)

        self.assertEqual(self.price_store.get_latest_price("INFY"), 1170.0)
        self.assertEqual(self.monitor.valid_ticks_processed, 1)

    def test_on_data_drops_invalid_tick(self):
        invalid_packet = {
            "token": "1594",
            "last_traded_price": -100,  # Negative LTP
            "sequence_number": 2
        }
        mock_ws = MagicMock()
        self.ws_manager._on_ws_data(mock_ws, invalid_packet)

        self.assertEqual(self.monitor.invalid_ticks_rejected, 1)

    def test_feed_staleness_detection(self):
        import time
        # Valid tick
        mock_ws = MagicMock()
        self.ws_manager._on_ws_data(mock_ws, {
            "token": "1594", "last_traded_price": 117000, "subscription_mode": 2, "sequence_number": 10
        })
        self.assertFalse(self.price_store.is_feed_stale("INFY"))

        time.sleep(0.6)  # Threshold is 0.5s
        self.assertTrue(self.price_store.is_feed_stale("INFY"))

if __name__ == "__main__":
    unittest.main()

