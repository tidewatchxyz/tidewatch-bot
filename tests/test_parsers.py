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
                "polymarket_history_111": sample.history_raw(),
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


class RealDataRuleTests(unittest.TestCase):
    """Rules learned from the first live run (11 Oct 2026)."""

    def test_yield_bearing_excluded_from_peg_watch(self):
        raw = sample.stablecoins_raw()
        raw["peggedAssets"][1].update(symbol="USYC", price=1.1397, yieldBearing=True)
        raw["peggedAssets"][2].update(symbol="reUSD", price=1.1068)  # unflagged yield token
        coins = defillama.parse_stablecoins(raw)
        with tempfile.TemporaryDirectory() as d:
            post = charts.peg_chart(coins, Path(d), date(2026, 10, 11))
        self.assertNotIn("USYC", post.alt_text)
        self.assertNotIn("reUSD", post.alt_text)

    def test_duplicate_symbols_use_names(self):
        raw = sample.stablecoins_raw()
        raw["peggedAssets"][0].update(symbol="OUSD", name="Open USD")
        raw["peggedAssets"][11].update(symbol="OUSD", name="Origin Dollar",
                                       circulating={"peggedUSD": 1e6})
        coins = defillama.parse_stablecoins(raw)
        with tempfile.TemporaryDirectory() as d:
            post = charts.movers_chart(coins, Path(d), date(2026, 10, 11))
        self.assertIn("Open USD", post.alt_text)

    def test_market_picker_skips_sports_longshots_and_short_dated(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 11, tzinfo=timezone.utc)

        def mk(q, yes, vol, end="2026-12-31T00:00:00Z", sports=False):
            return polymarket.Market(q, q, ["Yes", "No"], [yes, 1 - yes], ["1", "2"], vol, end,
                                     volume_1wk=vol, is_sports=sports)

        ms = [mk("Chiefs vs. Bills", 0.5, 9e6, sports=True),
              mk("Will X win the 2028 nomination?", 0.002, 8e6),
              mk("Ceasefire through October 12?", 0.5, 7e6, end="2026-10-13T00:00:00Z"),
              mk("Will the Democrats win the House?", 0.6, 3e6),
              mk("Will Bitcoin hit $150k in 2026?", 0.3, 1e6)]
        self.assertEqual(polymarket.pick_market(ms, now).question, "Will Bitcoin hit $150k in 2026?")


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
