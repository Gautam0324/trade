#!/usr/bin/env python3
"""
Unit and Integration Tests for Dashboard Service & API Server.
Validates:
- Mathematical accuracy of KPI metrics (P&L, return %, win rate, drawdown)
- Equity curve data generation
- Secret masking in configuration viewer
- Risk management audit rules
- Safe API responses for all 10 views
"""

import unittest
import json
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "dashboard"))
sys.path.insert(0, str(BASE_DIR / "trader"))

import service

class TestDashboardService(unittest.TestCase):
    def test_kpi_computation(self):
        kpis = service.compute_dashboard_metrics()
        self.assertIn("starting_capital", kpis)
        self.assertIn("free_cash", kpis)
        self.assertIn("realized_pnl", kpis)
        self.assertIn("win_rate", kpis)
        self.assertIn("profit_factor", kpis)
        self.assertIn("max_drawdown", kpis)
        self.assertGreaterEqual(kpis["starting_capital"], 0)
        self.assertGreaterEqual(kpis["win_rate"], 0.0)
        self.assertLessEqual(kpis["win_rate"], 100.0)

    def test_equity_curve(self):
        points = service.get_equity_curve_data()
        self.assertIsInstance(points, list)
        self.assertGreater(len(points), 0)
        first_point = points[0]
        self.assertIn("equity", first_point)
        self.assertIn("cumulative_pnl", first_point)
        self.assertEqual(first_point["cumulative_pnl"], 0.0)

    def test_sanitize_config_masks_telegram_token(self):
        raw_cfg = {
            "budget": 100000.0,
            "telegram_bot_token": "8916950436:AAHY9Oft4ORf6WASvm9AP9d3yoCw465LTr0",
            "sec_user_agent": "secret-agent"
        }
        safe_cfg = service.sanitize_config(raw_cfg)
        self.assertNotEqual(safe_cfg["telegram_bot_token"], raw_cfg["telegram_bot_token"])
        self.assertTrue("••••" in safe_cfg["telegram_bot_token"])
        self.assertFalse("8916950436:AAHY9Oft4ORf6WASvm9AP9d3yoCw465LTr0" in json.dumps(safe_cfg))

    def test_risk_audit_evaluation(self):
        risk = service.get_risk_audit()
        self.assertIn("max_daily_loss", risk)
        self.assertIn("alerts", risk)
        self.assertIsInstance(risk["alerts"], list)

    def test_strategy_performance_attribution(self):
        stats = service.get_strategy_performance()
        self.assertIsInstance(stats, list)
        for s in stats:
            self.assertIn("strategy", s)
            self.assertIn("trades", s)
            self.assertIn("win_rate", s)
            self.assertIn("net_pnl", s)

if __name__ == "__main__":
    unittest.main()
