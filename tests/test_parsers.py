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


class PegRangeTests(unittest.TestCase):
    @staticmethod
    def _rows(series: dict[str, list[float]]) -> list:
        n = max(len(v) for v in series.values())
        return [{"date": str(1_700_000_000 + i * 86_400),
                 "prices": {gid: vals[i] for gid, vals in series.items() if i < len(vals)}}
                for i in range(n)]

    def test_low_high_in_bps(self):
        rows = self._rows({"tether": [1.0, 0.9990, 1.0002, 0.9995]})
        r = defillama.parse_peg_ranges(rows, ["tether"])["tether"]
        self.assertAlmostEqual(r.low_bps, -10.0, places=6)
        self.assertAlmostEqual(r.high_bps, 2.0, places=6)
        self.assertAlmostEqual(r.width_bps, 12.0, places=6)
        self.assertAlmostEqual(r.worst_bps, -10.0, places=6)  # furthest from $1, signed
        self.assertEqual(r.days, 4)

    def test_only_the_trailing_window(self):
        rows = self._rows({"dai": [0.50] + [1.0] * 30})  # the 0.50 is older than 30 days
        r = defillama.parse_peg_ranges(rows, ["dai"], days=30)["dai"]
        self.assertEqual(r.days, 30)
        self.assertAlmostEqual(r.low_bps, 0.0, places=6)

    def test_unwanted_ids_and_bad_values_skipped(self):
        rows = [{"date": "1700000000",
                 "prices": {"tether": 1.0, "dai": None, "usd-coin": "x", "other": 1.0}}]
        out = defillama.parse_peg_ranges(rows, ["tether", "dai", "usd-coin"])
        self.assertEqual(list(out), ["tether"])

    def test_no_ids_or_junk_payload_gives_nothing(self):
        self.assertEqual(defillama.parse_peg_ranges(self._rows({"dai": [1.0]}), []), {})
        self.assertEqual(defillama.parse_peg_ranges({"oops": 1}, ["dai"]), {})

    def test_trim_keeps_trailing_rows_in_order(self):
        rows = list(reversed(self._rows({"dai": [1.0] * 10})))  # arrives newest-first
        kept = defillama.trim_price_history(rows, days=3)
        self.assertEqual(len(kept), 3)
        self.assertEqual([int(r["date"]) for r in kept], sorted(int(r["date"]) for r in kept))
        self.assertEqual(int(kept[-1]["date"]), 1_700_000_000 + 9 * 86_400)


class PegChartTests(unittest.TestCase):
    def _coins(self, prices=None):
        raw = sample.stablecoins_raw()
        for i, a in enumerate(raw["peggedAssets"]):
            a["price"] = 1.0 if prices is None else prices(i)
        return defillama.parse_stablecoins(raw)

    def _render(self, coins, ranges):
        with tempfile.TemporaryDirectory() as d:
            return charts.peg_chart(coins, Path(d), date(2026, 10, 11), ranges=ranges)

    def test_calm_month_says_so_and_shows_ranges(self):
        coins = self._coins()
        ranges = {c.gecko_id: defillama.PegRange(-8.0, 2.0, 30) for c in coins}
        post = self._render(coins, ranges)
        self.assertIn("held their $1 peg this month", post.headline)
        self.assertIn("30-day", post.alt_text)
        self.assertIn("-8 bps to +2 bps", post.caption)  # the widest swing, spelled out

    def test_intramonth_break_is_reported_even_when_calm_today(self):
        """The old chart only saw today, so a mid-month depeg went unmentioned."""
        coins = self._coins()
        ranges = {c.gecko_id: defillama.PegRange(-2.0, 2.0, 30) for c in coins}
        ranges[coins[3].gecko_id] = defillama.PegRange(-180.0, 1.0, 30)
        post = self._render(coins, ranges)
        self.assertIn(coins[3].symbol, post.headline)
        self.assertIn("back near $1 today", post.headline)

    def test_off_peg_today_still_leads(self):
        coins = self._coins(prices=lambda i: 0.9921 if i == 3 else 1.0)
        ranges = {c.gecko_id: defillama.PegRange(-79.0, 1.0, 30) for c in coins}
        post = self._render(coins, ranges)
        self.assertIn("off its $1 peg", post.headline)
        self.assertIn(coins[3].symbol, post.headline)

    def test_renders_without_any_history(self):
        with tempfile.TemporaryDirectory() as d:
            post = charts.peg_chart(self._coins(), Path(d), date(2026, 10, 11), ranges={})
            self.assertTrue(post.image.exists())
        self.assertIn("are holding their $1 peg", post.headline)

    def test_flat_coin_reads_as_flat_not_missing(self):
        coins = self._coins()
        ranges = {c.gecko_id: defillama.PegRange(0.0, 0.0, 30) for c in coins}
        self.assertIn("flat for all 30 days", self._render(coins, ranges).alt_text)

    def test_dollar_signs_survive_rendering(self):
        """Two $ in one string used to be eaten as mathtext ("off its $1 peg")."""
        from matplotlib import pyplot as plt
        from tidewatch import style
        style.fonts()
        self.assertFalse(plt.rcParams["text.parse_math"])


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
                charts.peg_chart(coins, out, today, sample=True, ranges=defillama.parse_peg_ranges(
                    sample.price_history_raw(), [c.gecko_id for c in coins])),
                charts.odds_chart(market, history, out, sample=True),
            ]
            for p in posts:
                self.assertTrue(p.image.exists())
                self.assertTrue(p.caption.startswith("[SAMPLE DATA"))
                self.assertLessEqual(len(p.caption), 280 + 27)  # 27 = sample prefix


if __name__ == "__main__":
    unittest.main()
