"""
Clean advice_logs JSONL files before fine-tuning.

Fixes:
  - STT score transcription errors (e.g. "score 600" → "score 6")
  - Removes duplicate interactions (same user_input + same minute)
  - Removes interactions with empty user_input or assistant_response

Usage:
    python3 scripts/clean_training_data.py
    python3 scripts/clean_training_data.py --input data/advice_logs_2026-03.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def normalize_score_in_text(text: str) -> str:
    """Fix STT trailing-zero score errors in user_input strings."""
    def fix_score(m: re.Match) -> str:
        value = int(m.group(1))
        while value > 15 and value % 10 == 0:
            value //= 10
        return f"score {value}"

    return re.sub(r'\bscore\s+(\d+)', fix_score, text, flags=re.IGNORECASE)


def is_valid(record: dict) -> bool:
    user_input = record.get("user_input", "").strip()
    response = record.get("assistant_response", "").strip()
    return bool(user_input) and bool(response)


def clean_file(input_path: Path, output_path: Path) -> None:
    records = []
    with input_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  Skipping malformed line: {e}")

    print(f"Loaded {len(records)} records from {input_path.name}")

    cleaned = []
    seen = set()
    fixed_scores = 0
    removed_empty = 0
    removed_dupes = 0

    for r in records:
        # Drop empty
        if not is_valid(r):
            removed_empty += 1
            continue

        # Fix STT score errors
        original = r["user_input"]
        r["user_input"] = normalize_score_in_text(original)
        if r["user_input"] != original:
            fixed_scores += 1
            print(f"  Fixed score: '{original}' → '{r['user_input']}'")

        # Deduplicate (same input within same minute)
        ts_minute = r.get("timestamp_utc", "")[:16]
        key = (r["user_input"].lower().strip(), ts_minute)
        if key in seen:
            removed_dupes += 1
            continue
        seen.add(key)

        cleaned.append(r)

    print(f"\nResults:")
    print(f"  Fixed score transcriptions : {fixed_scores}")
    print(f"  Removed empty records      : {removed_empty}")
    print(f"  Removed duplicates         : {removed_dupes}")
    print(f"  Final record count         : {len(cleaned)} (was {len(records)})")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for r in cleaned:
            f.write(json.dumps(r, ensure_ascii=True) + "\n")

    print(f"\nSaved to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=None, help="Path to a specific JSONL file")
    args = parser.parse_args()

    data_dir = Path(__file__).parent.parent / "data"

    if args.input:
        input_paths = [Path(args.input)]
    else:
        input_paths = sorted(data_dir.glob("advice_logs_*.jsonl"))

    if not input_paths:
        print("No advice_logs_*.jsonl files found.")
        return

    for input_path in input_paths:
        output_path = input_path.parent / (input_path.stem + "_cleaned.jsonl")
        print(f"\n{'='*60}")
        print(f"Processing: {input_path.name}")
        clean_file(input_path, output_path)


if __name__ == "__main__":
    main()
