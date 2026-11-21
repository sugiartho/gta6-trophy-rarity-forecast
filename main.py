"""Forecast each GTA VI trophy's Exophase earn rate at a case's target.

The program reads every <id>.in in the input directory (the case's trophies and
the house's reads of Exophase's trophy page before the checkpoint) and writes one
<id>.out per case: five quantiles (5%, 25%, 50%, 75%, 95%) of each trophy's earn
rate at the target, in percent.

Model, in one paragraph: an earn rate is earners over players tracked, and both
move. Exophase refreshes its counts in jumps, not continuously, so between
refreshes the tracked count and the rates sit flat; when it refreshes, the
tracked count jumps (launch surge) and the rates fall, because the newly tracked
players have barely started the game, while the players already tracked keep
earning trophies. The forecast therefore tracks two things separately -- the
tracked-player count and each trophy's earner count -- and projects both to the
target. The tracked count is projected from its growth in the history (a power
law in time since release, fitted where the history shows growth, with a
launch-surge prior where the history is flat and the count is stale). Each
trophy's earners are projected from its own progression among the tracked
cohort plus the earners the new players bring, at a fraction of the current rate
estimated from past refreshes (small for grinds, near one for the opening
mission). The forecast is a two-branch mixture -- a refresh happens before the
target, or it does not -- with the refresh probability from the time since the
last refresh against the refresh interval; where no refresh has been seen, the
staleness is censored (the last refresh predates the first read), so the
probability uses the effective staleness, the measured staleness plus the
equilibrium mean age of a refresh cycle. Spreads widen with the horizon and
with how much the history moved. Everything is deterministic: no randomness, no
network, no model calls, standard library only.
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
# Standard normal quantiles at the scored levels.
Z = [-1.6448536269514722, -0.6744897501960817, 0.0, 0.6744897501960817, 1.6448536269514722]

# Priors used only where the history itself is silent (a flat launch night).
PRIOR_REFRESH_INTERVAL_H = 5.0      # Exophase refreshes a game's counts every few hours
PRIOR_GROWTH_PER_HOUR = 0.40       # tracked players grow ~40%/h through launch
PRIOR_GROWTH_LOG_SD = 0.5          # lognormal spread on that growth rate
PRIOR_NEW_PLAYER_FRACTION = 0.10   # a new player holds a trophy at ~10% of the current rate
BASE_WALK_PER_DAY = 0.5            # random-walk logit spread per sqrt(day), as the baseline uses


def clamp_rate(x):
    """A rate in percent, kept inside the range the logit can take."""
    return min(99.99, max(0.01, x))


def logit_of(pct):
    p = clamp_rate(pct) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit_of(lg):
    if lg >= 0:
        return 100.0 / (1.0 + math.exp(-lg))
    e = math.exp(lg)
    return 100.0 * e / (1.0 + e)


def round4(x):
    """Round to 4 decimals, halves away from zero, so 1.1 stays 1.1 in JSON."""
    scaled = x * 10000.0
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 10000.0
    return int(value) if value == int(value) else value


def parse_time(text):
    """An ISO 8601 UTC timestamp to epoch seconds."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def hours_since_release(case, when):
    """Hours from the game's release day (00:00 UTC) to a timestamp."""
    release = datetime.fromisoformat(case["game"]["release_date"] + "T00:00:00+00:00")
    return (when - release.timestamp()) / 3600.0


def least_squares(xs, ys):
    """Slope and intercept of the least-squares line through (xs, ys)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0, my
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return slope, my - slope * mx


def normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def mixture_quantile(branches, level):
    """The quantile of a mixture of normals in logit space.

    branches: a list of (weight, mean, sd). The CDF is inverted on a fixed
    grid, so the result is deterministic.
    """
    lo, hi, step = -9.3, 9.3, 0.02
    target = level
    prev_x, prev_cdf = lo, 0.0
    x = lo
    while x <= hi + 1e-9:
        cdf = 0.0
        for weight, mean, sd in branches:
            cdf += weight * normal_cdf((x - mean) / sd)
        if cdf >= target:
            if cdf <= prev_cdf + 1e-12:
                return x
            frac = (target - prev_cdf) / (cdf - prev_cdf)
            return prev_x + frac * (x - prev_x)
        prev_x, prev_cdf = x, cdf
        x += step
    return hi


class CaseView:
    """A case's history reduced to the series the model works with."""

    def __init__(self, case):
        self.case = case
        self.trophies = case["trophies"]
        self.history = case["history"]
        self.horizon_h = case["horizon_minutes"] / 60.0
        self.at = parse_time(case["at"])
        self.release_h = 0.0
        # Hours since release for each read, and the tracked count.
        self.times = []
        self.counts = []
        for read in self.history:
            t = parse_time(read["at"])
            self.times.append(hours_since_release(case, t))
            self.counts.append(float(read["players_tracked"]))
        # Refreshes: reads where the count or any rate moved.
        self.refresh_at = []
        prev = None
        for i, read in enumerate(self.history):
            if prev is not None and (
                read["players_tracked"] != prev["players_tracked"]
                or read["rates"] != prev["rates"]
            ):
                self.refresh_at.append(i)
            prev = read
        # The last refresh (or the first read, when nothing ever refreshed):
        # the moment the page's counts were last set.
        if self.refresh_at:
            last_refresh_read = self.history[self.refresh_at[-1]]
        else:
            last_refresh_read = self.history[0]
        self.staleness_h = (self.at - parse_time(last_refresh_read["at"])) / 3600.0
        self.last = self.history[-1]
        self.last_n = float(self.last["players_tracked"])
        self.last_rates = self.last["rates"]

    def earner_series(self, index):
        """Earners of one trophy over the reads (rate * tracked / 100)."""
        out = []
        for read in self.history:
            rate = read["rates"][index]
            if rate is None:
                out.append(None)
            else:
                out.append(rate * read["players_tracked"] / 100.0)
        return out


def project_players(view):
    """Project the tracked count to the target.

    Returns (growth_factor, log_sd, refresh_probability): the factor the
    tracked count multiplies by before the target, how uncertain its log is,
    and the chance at least one refresh lands before the target.
    """
    horizon = view.horizon_h
    distinct = sorted(set(view.counts))
    if len(distinct) >= 2 and len(view.times) >= 3:
        # Power-law growth in time since release: log N against log(1 + hours).
        xs = [math.log1p(max(0.0, t)) for t in view.times]
        ys = [math.log(max(1.0, n)) for n in view.counts]
        slope, intercept = least_squares(xs, ys)
        slope = max(0.0, slope)
        target_t = max(0.0, view.times[-1] + horizon)
        log_now = intercept + slope * math.log1p(view.times[-1])
        log_target = intercept + slope * math.log1p(target_t)
        growth = math.exp(max(0.0, log_target - log_now))
        # Residual spread around the fit, widened by the horizon.
        resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
        sd = math.sqrt(sum(r * r for r in resid) / max(1, len(resid) - 2))
        log_sd = math.sqrt(sd * sd + 0.04 * horizon / 24.0 + 0.09)
        growth = min(growth, 30.0)
        # Refreshes are a matter of time over a multi-day horizon.
        intervals = []
        for j in view.refresh_at:
            intervals.append(view.times[j] - view.times[j - 1])
        mean_interval = sum(intervals) / len(intervals) if intervals else PRIOR_REFRESH_INTERVAL_H
        mean_interval = max(1.0, mean_interval)
        refresh_prob = 1.0 - math.exp(-horizon / mean_interval)
        return growth, max(0.15, log_sd), min(1.0, max(0.0, refresh_prob))
    # Flat history: no refresh has been seen, so the count is stale. The
    # underlying player base grows through launch; the next refresh collects
    # the growth since the last one. The refresh is overdue by the staleness.
    elapsed = max(0.0, view.staleness_h)
    span = elapsed + horizon
    growth = math.exp(PRIOR_GROWTH_PER_HOUR * span)
    growth = min(growth, 30.0)
    log_sd = math.sqrt(PRIOR_GROWTH_LOG_SD ** 2 + 0.04 * horizon / 24.0)
    # The staleness is censored: the last refresh happened at or before the
    # first read, so the page is at least as stale as measured and the
    # refresh is at least as overdue. Judge how soon the next refresh lands
    # from the effective staleness -- the measured staleness plus the
    # equilibrium mean age of a refresh cycle (half the prior interval) --
    # while the growth still counts only the growth since the first read,
    # which is what the history shows.
    effective = elapsed + 0.5 * PRIOR_REFRESH_INTERVAL_H
    remaining = max(1.0, PRIOR_REFRESH_INTERVAL_H - effective)
    refresh_prob = min(1.0, horizon / remaining)
    # A stale count on launch day is very likely to refresh soon.
    refresh_prob = max(refresh_prob, 1.0 - math.exp(-(effective + horizon) / PRIOR_REFRESH_INTERVAL_H))
    return growth, log_sd, min(1.0, max(0.0, refresh_prob))


def progression_rate(view, index):
    """Per-hour fractional growth of one trophy's earners among the cohort.

    Estimated from the stretches where the tracked count sat still (so the
    earners' movement is progression, not dilution), shrunk toward a small
    tier prior when the history is flat.
    """
    series = view.earner_series(index)
    slopes = []
    for j in range(1, len(series)):
        if series[j] is None or series[j - 1] is None:
            continue
        if view.counts[j] != view.counts[j - 1]:
            continue
        dt = view.times[j] - view.times[j - 1]
        if dt <= 0 or series[j - 1] <= 0:
            continue
        slopes.append(math.log(series[j] / series[j - 1]) / dt)
    tier = view.trophies[index]["tier"]
    prior = {"platinum": 0.0, "gold": 0.002, "silver": 0.004, "bronze": 0.008}.get(tier, 0.005)
    if not slopes:
        return prior
    observed = sum(slopes) / len(slopes)
    observed = max(0.0, min(observed, 0.05))
    # Shrink the noisy per-trophy slope toward the tier prior.
    weight = min(1.0, len(slopes) / 8.0)
    return weight * observed + (1.0 - weight) * prior


def new_player_fraction(view, index):
    """The fraction of newly tracked players who hold one trophy.

    Estimated from past refreshes: the earners the new players brought, over
    how many of them there were, relative to the trophy's current rate. Falls
    back to a prior where no refresh has been seen: new players are fresh, so
    they hold a trophy at a small fraction of the current rate, except the
    trophies nearly everyone holds, which they hold too.
    """
    rate = view.last_rates[index]
    if rate is None:
        rate = 0.0
    if rate >= 99.0:
        return 1.0
    estimates = []
    for j in view.refresh_at:
        before = view.history[j - 1]
        after = view.history[j]
        new_players = after["players_tracked"] - before["players_tracked"]
        if new_players <= 0:
            continue
        r_before = before["rates"][index]
        r_after = after["rates"][index]
        if r_before is None or r_after is None:
            continue
        brought = r_after * after["players_tracked"] / 100.0 - r_before * before["players_tracked"] / 100.0
        if brought < 0:
            brought = 0.0
        frac = (brought / new_players) / (r_before / 100.0) if r_before > 0 else 0.0
        estimates.append(min(1.0, max(0.0, frac)))
    if not estimates:
        # Prior: a fresh player's chance of holding it scales with how common
        # it is; the opening trophies they hold, the grinds they do not.
        return max(PRIOR_NEW_PLAYER_FRACTION, (rate / 100.0) ** 1.5)
    estimates.sort()
    return estimates[len(estimates) // 2]


def forecast_trophy(view, index, growth, growth_log_sd, refresh_prob):
    """Five quantiles of one trophy's earn rate at the target, in percent."""
    trophy = view.trophies[index]
    rate = view.last_rates[index]
    if rate is None:
        rate = 0.0
    if rate >= 99.99:
        # Everyone holds it, and every newly tracked player will too.
        return [99.99, 100, 100, 100, 100]
    days = view.horizon_h / 24.0
    base_spread = BASE_WALK_PER_DAY * math.sqrt(max(0.0, days))
    prog = progression_rate(view, index)
    prog_factor = math.exp(prog * view.horizon_h)
    frac = new_player_fraction(view, index)
    # Refresh branch: the count multiplies by growth, the cohort earns more,
    # and the new players bring their share.
    new_rate = rate * (prog_factor + frac * (growth - 1.0)) / growth if growth > 0 else rate
    mu_refresh = logit_of(new_rate)
    sd_refresh = math.sqrt(growth_log_sd ** 2 + 0.09 + base_spread ** 2)
    # No-refresh branch: the count sits still and the cohort earns more.
    mu_still = logit_of(rate * prog_factor)
    sd_still = math.sqrt(0.04 + base_spread ** 2)
    branches = [
        (refresh_prob, mu_refresh, max(0.12, sd_refresh)),
        (1.0 - refresh_prob, mu_still, max(0.12, sd_still)),
    ]
    # A trophy nobody holds yet can only appear; keep its floor honest.
    lo = math.log(0.01 / 99.99)
    out = []
    for level in LEVELS:
        lg = mixture_quantile(branches, level)
        lg = max(lo, min(-lo, lg))
        value = inv_logit_of(lg)
        out.append(round4(min(100.0, max(0.0, value))))
    # Quantiles must not decrease (rounding can tie them, never invert them).
    for k in range(1, len(out)):
        if out[k] < out[k - 1]:
            out[k] = out[k - 1]
    return out


def forecast_case(case):
    view = CaseView(case)
    if not view.history:
        raise ValueError("case has no history")
    growth, growth_log_sd, refresh_prob = project_players(view)
    rows = []
    for i, trophy in enumerate(view.trophies):
        rows.append({"id": trophy["id"], "q": forecast_trophy(view, i, growth, growth_log_sd, refresh_prob)})
    return {"trophies": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case)
        text = json.dumps(forecast, separators=(",", ":")) + "\n"
        (OUTPUT_DIR / (path.stem + ".out")).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
