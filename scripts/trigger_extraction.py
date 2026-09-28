#!/usr/bin/env python3
"""Upload a sample document to S3 to trigger extraction."""

import argparse
from pathlib import Path

import boto3


def main():
    parser = argparse.ArgumentParser(
        description="Upload document to trigger extraction"
    )
    parser.add_argument("--env", default="dev", help="Environment name")
    parser.add_argument("--region", default="us-west-2", help="AWS region")
    parser.add_argument(
        "--document",
        default="sample_data/documents/service-agreement.txt",
        help="Path to document to upload",
    )
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    account_id = session.client("sts").get_caller_identity()["Account"]
    bucket = f"sample-prompt-correction-memory-docs-{account_id}-{args.env}"

    doc_path = Path(args.document)
    if not doc_path.exists():
        print(f"Document not found: {doc_path}")
        return

    s3_key = f"documents/{doc_path.name}"
    s3 = session.client("s3")
    s3.upload_file(str(doc_path), bucket, s3_key)

    print(f"Uploaded: s3://{bucket}/{s3_key}")
    print("EventBridge will trigger extraction automatically.")
    print(f"Check results in DynamoDB table: extraction-results-{args.env}")


if __name__ == "__main__":
    main()
