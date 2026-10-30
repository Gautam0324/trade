"""
SmartWebSocket Manager for Day Trader Pro V2.
Manages persistent SmartWebSocketV2 connection, subscriptions, tick routing, and auto-reconnection.
"""

from typing import Dict, Any, List, Optional, Callable
import time
import threading
import logging
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from .connection_monitor import ConnectionMonitor, ConnectionState
from .tick_parser import TickParser
from .tick_validator import TickValidator
from .price_store import PriceStore
from .candle_builder import CandleBuilder

logger = logging.getLogger("WebSocketManager")

class SmartWebSocketManager:
    def __init__(
        self,
        angel_client,
        instrument_manager,
        price_store: PriceStore,
        candle_builder: CandleBuilder,
        connection_monitor: ConnectionMonitor,
        on_valid_tick: Optional[Callable[[Dict[str, Any]], None]] = None,
        subscription_mode: int = 2  # 2 = QUOTE (includes cumulative volume, ohlc)
    ):
        self.angel_client = angel_client
        self.instrument_manager = instrument_manager
        self.price_store = price_store
        self.candle_builder = candle_builder
        self.connection_monitor = connection_monitor
        self.on_valid_tick = on_valid_tick
        self.subscription_mode = subscription_mode

        self.tick_parser = TickParser(instrument_manager)
        self.tick_validator = TickValidator(instrument_manager)

        self.ws: Optional[SmartWebSocketV2] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_requested = False
        self._is_running = False

    def start(self):
        if self._is_running:
            return

        self._stop_requested = False
        self._thread = threading.Thread(target=self._run_loop, name="SmartWebSocketThread", daemon=True)
        self._thread.start()
        self._is_running = True

    def stop(self):
        self._stop_requested = True
        self._is_running = False
        if self.ws:
            try:
                self.ws.close_connection()
            except Exception:
                pass
        self.connection_monitor.set_state(ConnectionState.DISCONNECTED, "Manual Stop")

    def _run_loop(self):
        backoff = 2
        max_backoff = 60

        while not self._stop_requested:
            try:
                # 1. Authenticate / Refresh session if needed
                sess_info = self.angel_client.get_session_info()
                if not sess_info["authenticated"]:
                    self.connection_monitor.set_state(ConnectionState.CONNECTING, "Authenticating with Angel One")
                    auth_success = self.angel_client.authenticate()
                    if not auth_success:
                        self.connection_monitor.set_state(ConnectionState.ERROR, "Authentication failed")
                        time.sleep(backoff)
                        backoff = min(backoff * 2, max_backoff)
                        continue
                    sess_info = self.angel_client.get_session_info()

                jwt_token = sess_info["jwt_token"]
                feed_token = sess_info["feed_token"]
                api_key = sess_info["api_key"]
                client_code = sess_info["client_code"]

                # 2. Instantiate SmartWebSocketV2
                self.connection_monitor.set_state(ConnectionState.CONNECTING, "Connecting WebSocket")
                self.ws = SmartWebSocketV2(
                    auth_token=jwt_token,
                    api_key=api_key,
                    client_code=client_code,
                    feed_token=feed_token,
                    max_retry_attempt=5,
                    retry_strategy=1,
                    retry_delay=5,
                    retry_multiplier=2,
                    retry_duration=60
                )

                # Wire callbacks
                self.ws.on_open = self._on_ws_open
                self.ws.on_data = self._on_ws_data
                self.ws.on_error = self._on_ws_error
                self.ws.on_close = self._on_ws_close

                # Connect (blocking until disconnect)
                self.ws.connect()

                # If connection dropped cleanly or errored
                if not self._stop_requested:
                    self.connection_monitor.set_state(ConnectionState.RECONNECTING, "Socket closed; reconnecting")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)

            except Exception as e:
                err_str = str(e)
                self.connection_monitor.set_state(ConnectionState.ERROR, err_str)
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    def _on_ws_open(self, wsapp):
        self.connection_monitor.set_state(ConnectionState.CONNECTED)
        tokens = self.instrument_manager.get_all_tokens()
        if tokens:
            token_list = [{"exchangeType": 1, "tokens": tokens}]  # 1 = NSE_CM
            try:
                self.ws.subscribe("sub_token_correlation_id", self.subscription_mode, token_list)
            except Exception as e:
                print(f"[WS SUBSCRIBE ERROR] {e}")

    def _on_ws_data(self, wsapp, parsed_dict):
        # 1. Parse raw tick
        tick = self.tick_parser.parse_tick(parsed_dict)
        if not tick:
            self.connection_monitor.record_tick(is_valid=False, error_reason="Tick parser returned None")
            return

        # 2. Validate tick
        is_valid, reason = self.tick_validator.validate_tick(tick)
        if not is_valid:
            is_dup = "Duplicate" in reason
            self.connection_monitor.record_tick(is_valid=False, is_duplicate=is_dup, error_reason=reason)
            return

        # Record valid tick
        self.connection_monitor.record_tick(is_valid=True)

        # 3. Update in-memory PriceStore
        self.price_store.update_tick(tick)

        # 4. Feed into CandleBuilder for 1m and 5m candle generation
        self.candle_builder.process_tick(tick)

        # 5. Invoke custom real-time callbacks (e.g. tick-level risk monitor)
        if self.on_valid_tick:
            try:
                self.on_valid_tick(tick)
            except Exception as e:
                print(f"[TICK HANDLER ERROR] {e}")

    def _on_ws_error(self, wsapp, error):
        self.connection_monitor.set_state(ConnectionState.ERROR, str(error))

    def _on_ws_close(self, wsapp):
        self.connection_monitor.set_state(ConnectionState.DISCONNECTED, "WebSocket connection closed")

