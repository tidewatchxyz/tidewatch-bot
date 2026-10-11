"""Parser and chart smoke tests. Run with: python -m unittest"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from tidewatch import charts, defillama, fmt, polymarket, sample


class ParserTests(unittest.TestCase):
    def test_stablecoins_sorted_and_changes(self):
        coins = defillama.parse_stablecoins(sample.stablecoins_raw())
        self.assertEqual(len(coins), 12)
        self.assertGreaterEqual(coins[0].circulating, coins[1].circulating)
        self.assertIsNotNone(coins[0].change_7d)

    def test_stablecoins_skip_bad_rows(self):
        raw = {"peggedAssets": [{"name": "x", "pegType": "peggedUSD", "circulating": {}},
                                {"name": "y", "symbol": "Y", "pegType": "peggedUSD",
                                 "circulating": {"peggedUSD": 5.0}, "price": None}]}
        coins = defillama.parse_stablecoins(raw)
        self.assertEqual([c.symbol for c in coins], ["Y"])
        self.assertIsNone(coins[0].price)
        self.assertIsNone(coins[0].change_7d)

    def test_supply_chart_sums_pegs(self):
        pts = defillama.parse_supply_chart([
            {"date": "1700000000", "totalCirculatingUSD": {"peggedUSD": 10, "peggedEUR": 2}},
            {"date": "bad"},
        ])
        self.assertEqual(len(pts), 1)
        self.assertEqual(pts[0].total_usd, 12)

    def test_market_json_string_fields(self):
        m = polymarket.parse_market(sample.markets_raw()[0])
        self.assertTrue(m.is_binary_yes_no)
        self.assertAlmostEqual(m.yes_price, 0.63)
        self.assertEqual(m.token_ids[0], "111")

    def test_history(self):
        pts = polymarket.parse_history({"history": [{"t": 2, "p": 0.5}, {"t": 1, "p": "0.4"}]})
        self.assertEqual([p.price for p in pts], [0.4, 0.5])

    def test_fmt(self):
        self.assertEqual(fmt.usd(315.6e9), "$315.6B")
        self.assertEqual(fmt.usd(-3.04e9, sign=True), "-$3.0B")
        self.assertEqual(fmt.usd(116e6, sign=True), "+$116M")
        self.assertEqual(fmt.bps(-79.2), "-79 bps")


class ReplayTests(unittest.TestCase):
    def test_from_raw_rerenders_without_network(self):
        import json
        from tidewatch.__main__ import main
        with tempfile.TemporaryDirectory() as d:
            raw = Path(d) / "2026-10-11" / "raw"
            raw.mkdir(parents=True)
            files = {
                "defillama_stablecoins": sample.stablecoins_raw(),
                "defillama_supply_chart": sample.supply_chart_raw(),
                "polymarket_top_markets": sample.markets_raw(),
                "polymarket_history": sample.history_raw(),
            }
            for name, data in files.items():
                (raw / f"{name}.json").write_text(json.dumps(data))
            self.assertEqual(main(["daily", "--from-raw", str(raw.parent)]), 0)
            out = raw.parent / "rerender"
            for name in ("supply", "movers", "pegs", "odds"):
                self.assertTrue((out / f"{name}.png").exists(), name)
            self.assertIn("## supply:", (out / "captions.md").read_text())


class HeadlineTests(unittest.TestCase):
    def test_peg_headline_all_on_peg(self):
        raw = sample.stablecoins_raw()
        for a in raw["peggedAssets"]:
            a["price"] = 1.0001
        coins = defillama.parse_stablecoins(raw)
        with tempfile.TemporaryDirectory() as d:
            post = charts.peg_chart(coins, Path(d), date(2026, 10, 11))
        self.assertEqual(post.headline, "All 10 of the biggest stablecoins are holding their $1 peg")

    def test_movers_headline_names_leader(self):
        coins = defillama.parse_stablecoins(sample.stablecoins_raw())
        leader = max(coins, key=lambda c: abs(c.change_7d))
        with tempfile.TemporaryDirectory() as d:
            post = charts.movers_chart(coins, Path(d), date(2026, 10, 11))
        self.assertIn(leader.symbol, post.headline)


class ChartSmokeTests(unittest.TestCase):
    def test_all_charts_render(self):
        coins = defillama.parse_stablecoins(sample.stablecoins_raw())
        points = defillama.parse_supply_chart(sample.supply_chart_raw())
        market = polymarket.parse_market(sample.markets_raw()[0])
        history = polymarket.parse_history(sample.history_raw())
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            today = points[-1].day
            posts = [
                charts.supply_chart(points, out, sample=True),
                charts.movers_chart(coins, out, today, sample=True),
                charts.peg_chart(coins, out, today, sample=True),
                charts.odds_chart(market, history, out, sample=True),
            ]
            for p in posts:
                self.assertTrue(p.image.exists())
                self.assertTrue(p.caption.startswith("[SAMPLE DATA"))
                self.assertLessEqual(len(p.caption), 280 + 27)  # 27 = sample prefix


if __name__ == "__main__":
    unittest.main()
