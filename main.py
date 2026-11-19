"""Forecast Exophase trophy rates through refreshes and early audience growth."""

import json
import math
import os
from pathlib import Path

LEVEL_Z = (-1.645, -0.674, 0.0, 0.674, 1.645)
INPUT = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))
BASE_TIME = "2026-11-19T00:00:00Z"


def logit(rate):
    p = min(99.99, max(0.01, float(rate))) / 100.0
    return math.log(p / (1.0 - p))


def logistic(value):
    if value >= 0:
        e = math.exp(-value)
        return 100.0 / (1.0 + e)
    e = math.exp(value)
    return 100.0 * e / (1.0 + e)


def hours_since(stamp):
    from datetime import datetime

    start = datetime.fromisoformat(BASE_TIME.replace("Z", "+00:00"))
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return max(0.0, (when - start).total_seconds() / 3600.0)


def forecast(case):
    history = case["history"]
    trophies = case["trophies"]
    horizon_hours = case["horizon_minutes"] / 60.0
    if not history:
        raise ValueError("a case needs at least one complete read")

    # Exophase publishes batches. Collapse unchanged reads so an hour of polling
    # the same page does not look like evidence of a stable long-term rate.
    snapshots = []
    for read in history:
        state = (read["players_tracked"], tuple(read["rates"]))
        if snapshots and state == snapshots[-1][0]:
            continue
        snapshots.append((state, read))

    # Estimate the common response of trophy percentages to denominator growth.
    # A robust central slope is more useful than fitting each noisy, rounded rate.
    elasticities = []
    for (_, left), (_, right) in zip(snapshots, snapshots[1:]):
        n0, n1 = left["players_tracked"], right["players_tracked"]
        if n1 <= n0:
            continue
        growth = math.log(n1 / n0)
        for a, b in zip(left["rates"], right["rates"]):
            if a is None or b is None or a <= 0 or b <= 0 or a >= 100 or b >= 100:
                continue
            # Convert a logit change to an approximate proportional-rate change.
            elasticity = -(logit(b) - logit(a)) / growth
            if -0.5 < elasticity < 2.5:
                elasticities.append(elasticity)
    elasticity = sorted(elasticities)[len(elasticities) // 2] if elasticities else 0.90
    elasticity = min(1.25, max(0.35, elasticity))

    # Fit a softly regularized per-trophy logit trend over distinct page states.
    per_slopes = []
    for i in range(len(trophies)):
        points = []
        for _, read in snapshots:
            rate = read["rates"][i]
            if rate is not None:
                points.append((math.log1p(hours_since(read["at"])), logit(rate)))
        slope = 0.0
        if len(points) >= 2:
            xm = sum(x for x, _ in points) / len(points)
            ym = sum(y for _, y in points) / len(points)
            denom = sum((x - xm) ** 2 for x, _ in points)
            if denom > 1e-12:
                slope = sum((x - xm) * (y - ym) for x, y in points) / denom
        per_slopes.append(slope)

    # The final target can be several days out. Continue observed log-time trends,
    # but cap their influence so a single refresh cannot drive a rate to zero.
    target_x = math.log1p(hours_since(case["target"]))
    rows = []
    last = history[-1]
    for i, trophy in enumerate(trophies):
        raw = last["rates"][i]
        if raw is None:
            raw = 0.01
        # The launch snapshot's sole 100% trophy remained at 100% in the first
        # refresh, so keep an already universal unlock pinned at the ceiling.
        if raw >= 100.0:
            rows.append({"id": trophy["id"], "q": [100, 100, 100, 100, 100]})
            continue
        center = logit(raw)

        if horizon_hours <= 2.0 and len(snapshots) == 1:
            # Launch-night practice: the next Exophase batch brings a large
            # cohort whose completion share is below the tiny early sample.
            # The 6.06x observed launch refresh motivates a sublinear dilution.
            cohort_ratio = 638.0 / 3868.0
            center += elasticity * math.log(cohort_ratio)
        else:
            last_x = math.log1p(hours_since(last["at"]))
            trend = per_slopes[i] * (target_x - last_x)
            # The common count trend captures continuing casual-player arrivals;
            # observed per-trophy movement gets more weight once it exists.
            if len(snapshots) >= 2:
                center += max(-2.5, min(0.5, trend))
            else:
                # No post-refresh evidence yet: use conservative audience dilution.
                center -= min(1.6, 0.30 * math.log1p(horizon_hours / 12.0))

        # Quantile width grows with the forecast horizon and with refresh
        # uncertainty; it stays finite even for rates printed as 0 or 100.
        spread = 0.52 + 0.22 * math.sqrt(max(0.0, horizon_hours / 24.0))
        if len(snapshots) == 1 and horizon_hours > 2.0:
            spread += 0.20
        q = [round(logistic(center + z * spread), 4) for z in LEVEL_Z]
        rows.append({"id": trophy["id"], "q": q})
    return {"trophies": rows}


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for source in sorted(INPUT.glob("*.in"), key=lambda p: p.name):
        case = json.loads(source.read_text(encoding="utf-8"))
        result = forecast(case)
        target = OUTPUT / (source.stem + ".out")
        target.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
