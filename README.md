# Tidewatch bot

Daily charts on stablecoins and prediction markets, styled for [@tidewatchxyz](https://x.com/tidewatchxyz).

Every run makes four 1600x900 PNGs, each with a draft caption and alt text:

| Chart | What it shows | Source |
|---|---|---|
| `supply.png` | Total stablecoin supply, last 180 days, with the 30-day change | DefiLlama |
| `movers.png` | Biggest 7-day mints and redemptions, each as a share of that coin's own float, plus the net | DefiLlama |
| `pegs.png` | Price today plus the 30-day range vs $1 for the 10 largest USD stablecoins, flagging anything more than 50 bps off | DefiLlama |
| `odds.png` | 30-day Yes odds for one Polymarket market, with its resolution date | Polymarket |

Both APIs are free and need no key.

Every chart leads with a headline worked out from the data ("$3.7B of USDX was redeemed this week", "Stablecoin supply hit a new all-time high"), one hero number, and at most one annotation (the peak, or the biggest one-day swing). The caption opens with the same line, so the image and the post say the same thing.

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
out/2026-10-11/
  supply.png  movers.png  pegs.png  odds.png
  captions.md      caption + alt text for each chart
  raw/*.json       the exact API responses the charts were built from
```

Keep `raw/`. If someone questions a number, you can show exactly what the data said that day.

`defillama_stablecoin_prices.json` is the one trimmed file: the endpoint returns about
6 MB of history going back to 2020, so only the trailing 30 days the peg chart reads are
kept. Rows are saved whole, so every price behind the chart is still there.

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
