# Tidewatch bot

Daily charts on stablecoins and prediction markets, styled for [@tidewatchxyz](https://x.com/tidewatchxyz).

Every run makes up to eight 1600x900 PNGs, each with a draft caption and alt text, and ranks them by how strong the day's story is:

| Chart | What it shows | Source |
|---|---|---|
| `supply.png` | Total stablecoin supply, last 180 days, with the 30-day change | DefiLlama |
| `movers.png` | Biggest 7-day mints and redemptions, each as a share of that coin's own float, plus the net | DefiLlama |
| `pegs.png` | Price today plus the 30-day range vs $1 for the 10 largest USD stablecoins, flagging anything more than 50 bps off | DefiLlama |
| `chains.png` | Where the dollars live: stablecoin supply on the 8 biggest chains, with each chain's 30-day change | DefiLlama |
| `share.png` | USDT, USDC and everyone else: change in share of all USD stablecoins over 180 days | DefiLlama |
| `odds.png` | 30-day Yes odds for one Polymarket market, with its resolution date | Polymarket |
| `fed.png` | The next Fed decision on Polymarket: cut / hold / hike, today against a week ago | Polymarket |
| `swings.png` | The crypto and macro markets whose odds moved most this week, a week ago against now | Polymarket |

Both APIs are free and need no key.

Every chart leads with a headline worked out from the data ("$3.7B of USDX was redeemed this week", "Tron added $1.3B of stablecoins in 30 days while Ethereum lost $1.6B"), one hero number, and at most one annotation. The caption opens with the same line, so the image and the post say the same thing.

### Which chart to post

`captions.md` opens with a ranking. Each chart scores its own news on a 0 to 100 scale: 50 means its number hit what counts as notable for that chart, 100 means twice that. The bars, in `charts.NOTABLE`:

| Chart | Notable at |
|---|---|
| movers | $2B net weekly flow |
| chains | one chain gaining or losing $2B in 30 days |
| share | a 1-point shift in market share over 30 days |
| fed | a 10-point weekly shift (x1.25 within 10 days of the meeting) |
| swings | a 20-point weekly swing |
| odds | a 15-point weekly move |
| supply | 2% in 30 days (a new all-time high scores 100) |
| pegs | any top-10 coin more than 50 bps off peg scores 100 |

Ties go to pegs, fed, movers, swings, chains, odds, share, supply, in that order. A chart without enough data for a story that day (fewer than 3 liquid markets moved, no Fed meeting listed) prints `skip`, not `FAIL`.

The swings chart only counts markets that match crypto or macro keywords, traded $100K+ in the week, are between 4% and 96%, and are not sports or daily up/down bets, one market per event. That is a small set, often 3 to 6 markets, which is the point.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Use

```bash
python -m tidewatch daily                   # today's charts -> out/YYYY-MM-DD/
python -m tidewatch markets                 # most traded Polymarket markets, with slugs
python -m tidewatch daily --market SLUG     # odds chart for a market you choose
python -m tidewatch daily --sample          # offline run on fake data (stamped, never post)
python -m tidewatch daily --from-raw out/2026-10-11   # re-render a past day, no network
python -m unittest                          # tests
```

API calls run in parallel, so a full run usually takes a few seconds. `--from-raw` re-renders any past day from its saved responses, which is useful after a style change or for debugging a chart.

Each run writes:

```
out/2026-10-21/
  supply.png  movers.png  pegs.png  chains.png
  share.png   odds.png    fed.png   swings.png
  captions.md      today's ranking, then caption + alt text for each chart
  raw/*.json       the API responses the charts were built from
```

Keep `raw/`. If someone questions a number, you can show exactly what the data said that day.

Some responses are trimmed before they are saved, because they are mostly data no chart reads.
Rows and market objects are always saved whole, so every number behind a chart is still there:

- `defillama_stablecoin_prices.json`: trailing 30 days (the endpoint returns about 6 MB back to 2020)
- `defillama_chain_*.json`, `defillama_coin_*.json`: trailing 40 and 190 days
- `polymarket_events_*.json`: only markets that traded $20K+ in the week (each feed is 2 to 4 MB)

## Run it daily on GitHub (free)

`.github/workflows/daily.yml` runs every day at 05:15 UTC (08:15 in Addis). To use it:

1. Push this folder to a GitHub repo.
2. Open the repo's **Actions** tab and enable workflows.
3. Each morning, open the latest run and download the `tidewatch-...` artifact.

You can also trigger a run by hand from the Actions tab (**Run workflow**) and enter a market slug.

## Posting

The bot drafts posts and you post them by hand. That's on purpose: you read the number before it goes out, and you can add your own take, which is what makes a post worth following. Always paste the alt text into X's image description.

## Style

Dark surface `#0D1117`, brand mint `#3DDC97` for single lines, Archivo Black titles, IBM Plex Mono for everything else. Minted vs redeemed uses mint `#1FA874` and orange `#D95926`, a pair checked for color-blind safety on the dark background. Off-peg dots use amber and always carry a text label.

The peg chart scales to the day's data rather than a fixed ±100 bps, with a floor of ±15 bps
so ordinary noise never gets magnified into news. The ±50 bps band is drawn only when it fits
on that scale.

Charts carry their values as direct labels and drop the axis that would repeat them, so the
movers chart has no x-axis at all. A move is shown in dollars and as a share of that coin's
own supply, because $1.2B out of USDC is 1.6% of it while a $683M launch is all of it. The
mint/orange pair is checked with a CVD validator against the dark surface (worst adjacent
deutan ΔE 8.8, above the 8 threshold).

Fonts are bundled under the SIL Open Font License (see `tidewatch/fonts/`).

## Ideas for next versions

- Stablecoin supply by chain (Ethereum, Tron, Solana, Base)
- Weekly thread: the week's supply moves plus the biggest odds swings
- Alerts when a large stablecoin moves more than 50 bps off peg
- Trim the `USDX Money US.` style of truncated name in labels and captions
