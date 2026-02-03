# Candlestick Sequence Probability Analysis

This is a **local research tool** for computing historical probabilities of candlestick sequences and the behavior of the *next* candle. It is **not** a trading system, charting platform, or signal generator.

## Data source (fixed path)

The application always loads market data from:

```
data/data.csv
```

If the file does not exist, the application throws a clear error. The tool does **not** scan directories or prompt for a file.

The CSV must contain these columns:

```
ts_event, open, high, low, close
```

`ts_event` is an ISO-8601 UTC timestamp (e.g. `2019-05-05T22:03:00.000000000Z`).

## Usage

1. Ensure your data is available at `data/data.csv`.
2. Create a sequence definition JSON file (see example below).
3. Run the analysis:

```
python app.py --timeframe 5m --sequence sequence_example.json
```

The output is JSON, including:

- `sample_size`
- Direction probabilities for the next candle
- Price action probabilities for the next candle
- Body percentage bucket distribution for the next candle

## Sequence definition format

A sequence is a JSON list. Each entry must include `direction` (`bullish` or `bearish`). Optional filters are `body_bucket` and `close_behavior`.

Valid `close_behavior` values:

- `closed_above_prev_high`
- `took_prev_high_closed_below`
- `closed_below_prev_low`
- `took_prev_low_closed_above`
- `took_both_high_low`
- `inside_prev_range`

Example (`sequence_example.json`):

```json
[
  {
    "direction": "bullish"
  },
  {
    "direction": "bearish",
    "body_bucket": "20-40%",
    "close_behavior": "inside_prev_range"
  }
]
```
