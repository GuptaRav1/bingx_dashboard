"""
Test app.py static routes and integration.
"""

import unittest
from app import app
import time_utils


class TestApp(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()

    def test_root_serves_index(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Trade Dashboard", r.data)
        self.assertIn(b"positions-table", r.data)
        self.assertIn(b"col-desktop", r.data)
        self.assertIn(b"metrics-grid", r.data)
        self.assertIn(b"metric-mdd", r.data)
        self.assertIn(b"metric-total-fees", r.data)
        self.assertIn(b"metric-avg-duration", r.data)
        self.assertIn(b"pnl-chart", r.data)
        self.assertIn(b"trade-pnl-chart", r.data)
        self.assertIn(b"chart.js", r.data.lower())
        self.assertIn(b"/static/app.js", r.data)

    def test_serves_app_js(self):
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"computeAll", r.data)
        self.assertIn(b"computeWinRate", r.data)
        self.assertIn(b"computeProfitFactor", r.data)
        self.assertIn(b"computeCumulativePoints", r.data)
        self.assertIn(b"computeMaxDrawdown", r.data)
        self.assertIn(b"computeFeeStats", r.data)
        self.assertIn(b"computeDurationStats", r.data)
        self.assertIn(b"renderChart", r.data)
        self.assertIn(b"renderTradeBarChart", r.data)
        self.assertIn(b"fetchPositions", r.data)
        self.assertIn(b"renderTable", r.data)
        self.assertIn(b"setupTableAccordion", r.data)
        self.assertIn(b"trade-detail-row", r.data)

    def test_serves_style_css(self):
        r = self.client.get("/static/style.css")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"metrics-grid", r.data)
        self.assertIn(b"chart-section", r.data)
        self.assertIn(b"table-wrapper", r.data)
        self.assertIn(b"badge-long", r.data)
        self.assertIn(b"col-desktop", r.data)
        self.assertIn(b"accordion-toggle", r.data)

    def test_positions_endpoint(self):
        today = time_utils.ist_today()
        r = self.client.get(f"/api/positions?from=2026-09-14&to={today}")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.get_json(), list)


if __name__ == "__main__":
    unittest.main()

