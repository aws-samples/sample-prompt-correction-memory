#!/usr/bin/env python3
"""Seed the correction log table with sample corrections."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import boto3


def main():
    parser = argparse.ArgumentParser(description="Seed correction log with sample data")
    parser.add_argument("--env", default="dev", help="Environment name")
    parser.add_argument("--region", default="us-west-2", help="AWS region")
    args = parser.parse_args()

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    table = dynamodb.Table(f"correction-log-{args.env}")

    corrections_dir = Path(__file__).parent.parent / "sample_data" / "corrections"
    written = 0

    for path in sorted(corrections_dir.glob("*.json")):
        with open(path) as f:
            data = json.load(f)

        items = data if isinstance(data, list) else [data]
        for item in items:
            item["timestamp"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            table.put_item(Item=item)
            written += 1
            print(
                f"  ✓ {item['field_name']}: {item['original_value']} → {item['corrected_value']}"
            )

    print(f"\nSeeded {written} corrections into {table.table_name}")


if __name__ == "__main__":
    main()
