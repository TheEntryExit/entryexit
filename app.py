#!/usr/bin/env python3
import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

DATA_PATH = Path("data/data.csv")


@dataclass(frozen=True)
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class CandleAttributes:
    direction: str
    body_bucket: str
    close_behavior: Optional[str]


BODY_BUCKETS = [
    (0, 20),
    (20, 40),
    (40, 60),
    (60, 80),
    (80, 100),
]
BUCKET_LABELS = [f"{lower}-{upper}%" for lower, upper in BODY_BUCKETS]

CLOSE_BEHAVIORS = {
    "closed_above_prev_high",
    "took_prev_high_closed_below",
    "closed_below_prev_low",
    "took_prev_low_closed_above",
    "took_both_high_low",
    "inside_prev_range",
}


def parse_timestamp(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError(f"Timestamp is not UTC ISO-8601 with Z suffix: {value}")
    base, _, fractional = value[:-1].partition(".")
    dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    if fractional:
        micro = int((fractional + "000000")[:6])
        dt = dt.replace(microsecond=micro)
    return dt


def load_candles(path: Path) -> List[Candle]:
    if not path.exists():
        raise FileNotFoundError(f"Data file not found at fixed path: {path}")
    candles: List[Candle] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        expected = {"ts_event", "open", "high", "low", "close"}
        if set(reader.fieldnames or []) != expected:
            raise ValueError(f"CSV header must be exactly {sorted(expected)}")
        for row in reader:
            ts = parse_timestamp(row["ts_event"])
            candle = Candle(
                ts=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
            candles.append(candle)
    candles.sort(key=lambda c: c.ts)
    return candles


def normalize_one_minute(candles: Iterable[Candle]) -> List[Candle]:
    normalized: List[Candle] = []
    prev: Optional[Candle] = None
    for candle in candles:
        if candle.ts.second != 0 or candle.ts.microsecond != 0:
            raise ValueError(f"Candle timestamp not aligned to minute boundary: {candle.ts}")
        if prev is None:
            normalized.append(candle)
            prev = candle
            continue
        delta = candle.ts - prev.ts
        if delta.total_seconds() < 60:
            raise ValueError(f"Non-increasing or duplicate timestamp at {candle.ts}")
        missing_minutes = int(delta.total_seconds() // 60) - 1
        for _ in range(missing_minutes):
            prev_ts = prev.ts + timedelta(minutes=1)
            fill_price = prev.close
            filler = Candle(
                ts=prev_ts,
                open=fill_price,
                high=fill_price,
                low=fill_price,
                close=fill_price,
            )
            normalized.append(filler)
            prev = filler
        normalized.append(candle)
        prev = candle
    return normalized


def aggregate_timeframe(candles: List[Candle], minutes: int) -> List[Candle]:
    if minutes == 1:
        return candles
    aggregated: List[Candle] = []
    buffer: List[Candle] = []
    for candle in candles:
        buffer.append(candle)
        end_time = candle.ts + timedelta(minutes=1)
        total_minutes = end_time.hour * 60 + end_time.minute
        if total_minutes % minutes == 0 and end_time.second == 0:
            if len(buffer) != minutes:
                buffer = []
                continue
            first = buffer[0]
            last = buffer[-1]
            aggregated.append(
                Candle(
                    ts=end_time - timedelta(minutes=minutes),
                    open=first.open,
                    high=max(c.high for c in buffer),
                    low=min(c.low for c in buffer),
                    close=last.close,
                )
            )
            buffer = []
    return aggregated


def direction(candle: Candle) -> str:
    if candle.close > candle.open:
        return "bullish"
    if candle.close < candle.open:
        return "bearish"
    return "doji"


def body_bucket(candle: Candle) -> str:
    range_size = candle.high - candle.low
    if range_size == 0:
        percent = 0.0
    else:
        percent = abs(candle.close - candle.open) / range_size * 100
    for lower, upper in BODY_BUCKETS:
        if lower <= percent <= upper:
            return f"{lower}-{upper}%"
    return "80-100%"


def close_behavior(current: Candle, prev: Candle) -> str:
    took_high = current.high >= prev.high
    took_low = current.low <= prev.low
    if took_high and took_low:
        return "took_both_high_low"
    if current.close > prev.high:
        return "closed_above_prev_high"
    if took_high and current.close <= prev.high:
        return "took_prev_high_closed_below"
    if current.close < prev.low:
        return "closed_below_prev_low"
    if took_low and current.close >= prev.low:
        return "took_prev_low_closed_above"
    return "inside_prev_range"


def compute_attributes(candles: List[Candle]) -> List[CandleAttributes]:
    attrs: List[CandleAttributes] = []
    for idx, candle in enumerate(candles):
        prev = candles[idx - 1] if idx > 0 else None
        attrs.append(
            CandleAttributes(
                direction=direction(candle),
                body_bucket=body_bucket(candle),
                close_behavior=close_behavior(candle, prev) if prev else None,
            )
        )
    return attrs


def matches_filter(attrs: CandleAttributes, spec: Dict[str, str]) -> bool:
    if attrs.direction != spec["direction"]:
        return False
    if "body_bucket" in spec and attrs.body_bucket != spec["body_bucket"]:
        return False
    if "close_behavior" in spec:
        if attrs.close_behavior != spec["close_behavior"]:
            return False
    return True


def analyze_sequence(
    candles: List[Candle],
    attrs: List[CandleAttributes],
    sequence: List[Dict[str, str]],
) -> Dict[str, object]:
    if len(sequence) < 2:
        raise ValueError("Sequence must contain at least two candles (C1 and C2).")
    for item in sequence:
        if "direction" not in item:
            raise ValueError("Each sequence candle must include a direction.")
        if "close_behavior" in item and item["close_behavior"] not in CLOSE_BEHAVIORS:
            raise ValueError(f"Invalid close_behavior: {item['close_behavior']}")
    counts = {
        "bullish": 0,
        "bearish": 0,
        "body_buckets": {bucket_label: 0 for bucket_label in BUCKET_LABELS},
        "closed_above_c2_high": 0,
        "closed_above_c1_high": 0,
        "closed_below_c2_low": 0,
        "closed_below_c1_low": 0,
        "took_c2_high_closed_below": 0,
        "took_c2_low_closed_above": 0,
        "took_both_high_low": 0,
    }
    sample_size = 0
    seq_len = len(sequence)
    for start in range(0, len(candles) - seq_len):
        window_attrs = attrs[start : start + seq_len]
        if all(matches_filter(attr, sequence[idx]) for idx, attr in enumerate(window_attrs)):
            next_candle = candles[start + seq_len]
            next_attr = attrs[start + seq_len]
            c1 = candles[start]
            c2 = candles[start + 1]
            sample_size += 1
            if next_attr.direction in ("bullish", "bearish"):
                counts[next_attr.direction] += 1
            counts["body_buckets"][next_attr.body_bucket] += 1
            if next_candle.close > c2.high:
                counts["closed_above_c2_high"] += 1
            if next_candle.close > c1.high:
                counts["closed_above_c1_high"] += 1
            if next_candle.close < c2.low:
                counts["closed_below_c2_low"] += 1
            if next_candle.close < c1.low:
                counts["closed_below_c1_low"] += 1
            if next_candle.high >= c2.high and next_candle.close < c2.high:
                counts["took_c2_high_closed_below"] += 1
            if next_candle.low <= c2.low and next_candle.close > c2.low:
                counts["took_c2_low_closed_above"] += 1
            if next_candle.high >= c2.high and next_candle.low <= c2.low:
                counts["took_both_high_low"] += 1
    def pct(value: int) -> float:
        if sample_size == 0:
            return 0.0
        return round(value / sample_size * 100, 2)

    return {
        "sample_size": sample_size,
        "direction_probabilities": {
            "bullish": pct(counts["bullish"]),
            "bearish": pct(counts["bearish"]),
        },
        "price_action_probabilities": {
            "closed_above_c2_high": pct(counts["closed_above_c2_high"]),
            "closed_above_c1_high": pct(counts["closed_above_c1_high"]),
            "closed_below_c2_low": pct(counts["closed_below_c2_low"]),
            "closed_below_c1_low": pct(counts["closed_below_c1_low"]),
            "took_c2_high_closed_below": pct(counts["took_c2_high_closed_below"]),
            "took_c2_low_closed_above": pct(counts["took_c2_low_closed_above"]),
            "took_both_high_low": pct(counts["took_both_high_low"]),
        },
        "body_bucket_probabilities": {
            bucket: pct(counts["body_buckets"][bucket]) for bucket in counts["body_buckets"]
        },
    }


def load_sequence(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("Sequence file must be a JSON list.")
    return payload


def timeframe_minutes(label: str) -> int:
    normalized = label.strip().lower()
    mapping = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "2h": 120,
        "4h": 240,
        "1d": 1440,
        "daily": 1440,
    }
    if normalized not in mapping:
        raise ValueError(f"Unsupported timeframe: {label}")
    return mapping[normalized]


def main() -> None:
    parser = argparse.ArgumentParser(description="Candlestick sequence probability analysis")
    parser.add_argument(
        "--timeframe",
        default="1m",
        help="Timeframe: 1m,5m,15m,30m,1h,2h,4h,1d",
    )
    parser.add_argument(
        "--sequence",
        required=True,
        help="Path to JSON file describing the candle sequence",
    )
    args = parser.parse_args()

    candles = load_candles(DATA_PATH)
    normalized = normalize_one_minute(candles)
    minutes = timeframe_minutes(args.timeframe)
    timeframe_candles = aggregate_timeframe(normalized, minutes)
    attrs = compute_attributes(timeframe_candles)
    sequence = load_sequence(Path(args.sequence))
    result = analyze_sequence(timeframe_candles, attrs, sequence)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
