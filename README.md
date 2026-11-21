# gta6-trophy-rarity-forecast

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/sugiartho/gta6-trophy-rarity-forecast/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/sugiartho/gta6-trophy-rarity-forecast/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

Forecasts each Grand Theft Auto VI trophy's Exophase earn rate a week after launch, as five quantiles per trophy. It models earners and players tracked separately: Exophase refreshes its counts in jumps, so a refresh dilutes the rates (new players have barely started) while the players already tracked keep earning. The program projects the tracked count and each trophy's earners to the target, mixes a refresh-happens branch with a no-refresh branch, and reads the quantiles off that mixture.

## How it works

- **Tracked players.** The count is projected to the target from its growth in the history (a power law in time since release, fitted where the history shows growth). Where the history is flat and the count is stale, a launch-surge prior takes over: the next refresh collects hours of uncounted growth.
- **Earners.** Each trophy's earners grow two ways: the tracked cohort earns more (a per-trophy progression rate, read off the stretches where the count sat still), and newly tracked players bring their share (a fraction of the current rate, estimated from past refreshes; near one for the opening mission, small for grinds).
- **Uncertainty.** The forecast is a two-branch mixture in logit space -- a refresh lands before the target, or it does not -- with the refresh probability from the time since the last refresh against the refresh interval. Spreads widen with the horizon and with how much the history moved, so the 90% band stays honest.
- **Output.** One `<id>.out` per case: `{"trophies": [{"id": n, "q": [q05, q25, q50, q75, q95]}]}`, deterministic, standard library only, no network.

On the practice cases (launch night, both straddling Exophase's first refresh) this scores a pinball loss of about 0.17 against the persistence baseline's 1.51, with a typical miss of about 0.03 points against its 0.68.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=c31gnmy5&utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
