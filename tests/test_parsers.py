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


class ShareLabelTests(unittest.TestCase):
    @staticmethod
    def _coin(circ, prev_week):
        return defillama.Stablecoin(name="x", symbol="X", peg_type="peggedUSD", mechanism="",
                                    circulating=circ, prev_day=None, prev_week=prev_week,
                                    prev_month=None, price=1.0)

    def test_percent_for_ordinary_moves(self):
        self.assertEqual(charts._share_label(self._coin(1.14e9, 1e9)), "+14%")

    def test_extra_decimal_when_tiny(self):
        """USDT's +$170M is +0.1%, and rounding it to +0% throws the number away."""
        self.assertEqual(charts._share_label(self._coin(184.09e9, 183.92e9)), "+0.1%")

    def test_launch_reads_as_new(self):
        self.assertEqual(charts._share_label(self._coin(683e6, 0.0)), "new")

    def test_near_launch_shows_a_multiple_not_a_huge_percent(self):
        """USPD went from $360K to $99M: '+27560%' is arithmetic, not information."""
        self.assertEqual(charts._share_label(self._coin(99.36e6, 0.36e6)), "\u00d7276")

    def test_no_label_without_history(self):
        self.assertIsNone(charts._share_label(self._coin(1e9, None)))


class FrozenCoinTests(unittest.TestCase):
    @staticmethod
    def _coin(circ, prev_day, price):
        return defillama.Stablecoin(name="x", symbol="X", peg_type="peggedUSD", mechanism="",
                                    circulating=circ, prev_day=prev_day, prev_week=0.358e6,
                                    prev_month=None, price=price)

    def test_unpriced_overnight_200x_is_frozen(self):
        """USPD: $0.48M "a day ago", $98.9M now, no price, unchanged for a week."""
        self.assertTrue(charts._looks_frozen(self._coin(98.9e6, 0.48e6, None)))

    def test_priced_launch_is_kept(self):
        self.assertFalse(charts._looks_frozen(self._coin(98.9e6, 0.48e6, 1.0)))

    def test_unpriced_but_steady_is_kept(self):
        """M, USDB, SUSD have no price on DefiLlama but move normally."""
        self.assertFalse(charts._looks_frozen(self._coin(207.8e6, 208.4e6, None)))

    def test_frozen_coin_left_out_of_movers_and_net(self):
        raw = sample.stablecoins_raw()
        raw["peggedAssets"].append({
            "name": "Frozen Dollar", "symbol": "FRZ", "pegType": "peggedUSD",
            "circulating": {"peggedUSD": 5e9}, "circulatingPrevDay": {"peggedUSD": 1e6},
            "circulatingPrevWeek": {"peggedUSD": 1e6}})
        coins = defillama.parse_stablecoins(raw)
        with tempfile.TemporaryDirectory() as d:
            post = charts.movers_chart(coins, Path(d), date(2026, 10, 19))
        self.assertNotIn("FRZ", post.alt_text)
        self.assertNotIn("Frozen", post.headline)


class SwingAndFedTests(unittest.TestCase):
    @staticmethod
    def _m(q, p, wk, vol=500e3, event="", group="", sports=False, end="2099-01-01T00:00:00Z"):
        return polymarket.Market(q, q, ["Yes", "No"], [p, 1 - p], ["1", "2"], vol / 5, end,
                                 volume_1wk=vol, is_sports=sports, one_week_change=wk,
                                 event_id=event, event_title="", group_title=group)

    def test_swings_keep_liquid_crypto_and_macro_only(self):
        ms = [self._m("Will Bitcoin reach $150k in 2026?", 0.24, -0.27),
              self._m("Will Bitcoin dip to $60k in 2026?", 0.30, 0.05),
              self._m("Will Bitcoin hit $1M?", 0.01, -0.02),                   # nearly settled
              self._m("Will Ethereum reach $5k?", 0.40, 0.30, vol=10e3),       # too thin
              self._m("Chiefs vs. Bills: will the Chiefs win?", 0.5, 0.4),     # a game
              self._m("Will Taylor Swift tour Asia?", 0.5, 0.4),               # off topic
              self._m("Bitcoin Up or Down on October 20?", 0.5, 0.45)]          # daily coin flip
        got = [m.question for m in polymarket.swing_candidates(ms)]
        self.assertEqual(got, ["Will Bitcoin reach $150k in 2026?", "Will Bitcoin dip to $60k in 2026?"])

    def test_swings_one_market_per_event(self):
        ms = [self._m("Will Bitcoin reach $100k in October?", 0.3, -0.05, event="btc-oct"),
              self._m("Will Bitcoin dip to $70k in October?", 0.2, -0.15, event="btc-oct")]
        got = polymarket.swing_candidates(ms)
        self.assertEqual([m.question for m in got], ["Will Bitcoin dip to $70k in October?"])

    def test_fed_buckets_and_week_ago(self):
        q = "Will {} after the October 2026 meeting?"
        meeting = [self._m(q.format("the Fed increase interest rates by 25 bps"), 0.12, -0.07, event="f",
                           group="25 bps increase"),
                   self._m(q.format("there be no change in Fed interest rates"), 0.88, 0.07, event="f",
                           group="No change"),
                   self._m(q.format("the Fed decrease interest rates by 25 bps"), 0.004, None, event="f",
                           group="25 bps decrease")]
        now, before = charts.fed_odds(meeting)
        self.assertAlmostEqual(sum(now.values()), 1.0)
        self.assertGreater(now["hold"], before["hold"])
        self.assertAlmostEqual(sum(before.values()), 1.0)
        self.assertAlmostEqual(before["hike"], 0.19 / 1.004, places=4)  # 0.12 + 0.07, renormalised
        self.assertEqual(polymarket.fed_month(meeting), "October")

    def test_fed_meeting_skips_resolved_and_picks_nearest(self):
        q = "Will there be no change in Fed interest rates after the {} 2026 meeting?"
        past = self._m(q.format("September"), 0.5, 0.0, event="sep", end="2020-01-01T00:00:00Z")
        oct_ = [self._m(q.format("October"), 0.8, 0.0, event="oct", end="2099-10-29T00:00:00Z"),
                self._m(q.replace("there be no change in Fed", "the Fed increase").format("October"),
                        0.2, 0.0, event="oct", end="2099-10-29T00:00:00Z")]
        dec = [self._m(q.format("December"), 0.6, 0.0, event="dec", end="2099-12-10T00:00:00Z"),
               self._m(q.format("December"), 0.4, 0.0, event="dec", end="2099-12-10T00:00:00Z")]
        got = polymarket.fed_meeting([past, *dec, *oct_])
        self.assertEqual({m.event_id for m in got}, {"oct"})

    def test_short_row_labels(self):
        self.assertEqual(charts._label_question(
            "Will there be no change in Fed interest rates after the October 2026 meeting?"),
            "No change in Fed rates after the October 2026 meeting")
        self.assertEqual(charts._label_question("Will Bitcoin dip to $77,500 in October?"),
                         "Bitcoin dip to $77,500 in October")


class ChainAndShareTests(unittest.TestCase):
    def test_chain_change_over_window(self):
        hist = [(date(2026, 9, d), 100e9) for d in range(1, 30)] + [(date(2026, 10, 21), 103e9)]
        cs = defillama.chain_supply("Tron", hist, days=30)
        self.assertAlmostEqual(cs.change, 3e9)
        self.assertAlmostEqual(cs.change_pct, 0.03)

    def test_pegged_history_reads_only_dollar_pegs(self):
        rows = [{"date": "1700000000", "totalCirculatingUSD": {"peggedUSD": 5.0, "peggedEUR": 99.0}},
                {"date": "1700086400", "totalCirculatingUSD": {"peggedEUR": 1.0}}]
        self.assertEqual([v for _, v in defillama.parse_pegged_history(rows)], [5.0])

    def test_supply_points_keep_the_dollar_part(self):
        p = defillama.parse_supply_chart(sample.supply_chart_raw(days=40))[-1]
        self.assertAlmostEqual(p.pegged_usd / p.total_usd, 0.985, places=6)


class RankingTests(unittest.TestCase):
    def test_score_scales_to_each_charts_notable_bar(self):
        self.assertEqual(charts.story_score(2e9, charts.NOTABLE["movers"]), 50.0)
        self.assertEqual(charts.story_score(-4e9, charts.NOTABLE["movers"]), 100.0)
        self.assertEqual(charts.story_score(99, 1), 100.0)  # capped

    def test_depeg_beats_everything_and_ties_follow_priority(self):
        from tidewatch.__main__ import rank
        P = lambda n, s: charts.Post(n, Path(n), n, "", "", s, "")
        ranked = rank([P("movers", 100), P("pegs", 100), P("share", 40), P("fed", 40)])
        self.assertEqual([p.name for p in ranked], ["pegs", "movers", "fed", "share"])


class ChartSmokeTests(unittest.TestCase):
    def test_all_charts_render(self):
        coins = defillama.parse_stablecoins(sample.stablecoins_raw())
        points = defillama.parse_supply_chart(sample.supply_chart_raw())
        market = polymarket.parse_market(sample.markets_raw()[0])
        history = polymarket.parse_history(sample.history_raw())
        markets = polymarket.parse_events(sample.topic_events_raw())
        chains = [defillama.chain_supply(n, defillama.parse_pegged_history(sample.chain_history_raw(n)))
                  for n, _ in defillama.parse_chain_list(sample.chain_list_raw(), 8)]
        usdt = defillama.parse_pegged_history(sample.coin_history_raw(defillama.USDT_ID))
        usdc = defillama.parse_pegged_history(sample.coin_history_raw(defillama.USDC_ID))
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            today = points[-1].day
            posts = [
                charts.supply_chart(points, out, sample=True),
                charts.movers_chart(coins, out, today, sample=True),
                charts.peg_chart(coins, out, today, sample=True, ranges=defillama.parse_peg_ranges(
                    sample.price_history_raw(), [c.gecko_id for c in coins])),
                charts.odds_chart(market, history, out, sample=True),
                charts.chains_chart(chains, out, today, sample=True),
                charts.share_chart(points, usdt, usdc, out, sample=True),
                charts.swings_chart(polymarket.swing_candidates(markets), out, today, sample=True),
                charts.fed_chart(polymarket.fed_meeting(markets), out, today, sample=True),
            ]
            for p in posts:
                self.assertTrue(p.image.exists(), p.name)
                self.assertTrue(p.caption.startswith("[SAMPLE DATA"), p.name)
                self.assertLessEqual(len(p.caption), 280 + 27, p.name)  # 27 = sample prefix
                self.assertTrue(0 <= p.score <= 100, p.name)
                self.assertTrue(p.why, p.name)

    def test_quiet_week_is_a_skip_not_a_failure(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(charts.NoStory):
                charts.swings_chart([], Path(d), date(2026, 10, 21))
            with self.assertRaises(charts.NoStory):
                charts.fed_chart([], Path(d), date(2026, 10, 21))


if __name__ == "__main__":
    unittest.main()
