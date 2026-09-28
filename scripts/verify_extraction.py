#!/usr/bin/env python3
"""Read back extraction results from DynamoDB to verify a deployed run.

Use after `make trigger` to confirm the pipeline extracted fields end-to-end
(and, if a matching correction was seeded, that self-healing occurred). Prints
a table of results and exits non-zero if no results are found yet.
"""

import argparse
import sys
import time

import boto3


def main():
    parser = argparse.ArgumentParser(description="Verify extraction results")
    parser.add_argument("--env", default="dev", help="Environment name")
    parser.add_argument("--region", default="us-west-2", help="AWS region")
    parser.add_argument(
        "--wait",
        type=int,
        default=60,
        help="Seconds to poll for results before giving up",
    )
    args = parser.parse_args()

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    table = dynamodb.Table(f"extraction-results-{args.env}")

    deadline = time.time() + args.wait
    items = []
    while time.time() < deadline:
        items = table.scan().get("Items", [])
        if items:
            break
        print("  ...no results yet, waiting for async extraction...")
        time.sleep(5)

    if not items:
        print(
            f"No extraction results found in extraction-results-{args.env} "
            f"after {args.wait}s. Check Lambda logs (/aws/lambda/extraction-{args.env})."
        )
        sys.exit(1)

    print(
        f"\nFound {len(items)} extracted field(s) in extraction-results-{args.env}:\n"
    )
    print(f"  {'field_name':<20} {'value':<28} {'conf':<6} {'self_healed':<11} source")
    print("  " + "-" * 78)
    self_healed_count = 0
    for it in sorted(items, key=lambda x: x.get("field_name", "")):
        healed = str(it.get("self_healed", False))
        if healed.lower() == "true":
            self_healed_count += 1
        print(
            f"  {str(it.get('field_name', '')):<20} "
            f"{str(it.get('value', ''))[:26]:<28} "
            f"{str(it.get('confidence_score', '')):<6} "
            f"{healed:<11} "
            f"{str(it.get('model_id', ''))}"
        )

    print(
        f"\nSummary: {len(items)} fields extracted, "
        f"{self_healed_count} via self-healing."
    )


if __name__ == "__main__":
    main()
