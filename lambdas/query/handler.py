"""
Query Lambda — stocks-query
Triggered by API Gateway: GET /movers

Responsibilities:
  - Scan DynamoDB for records from the last 7 days
  - Return sorted results (most recent first) as JSON
  - Attach CORS headers so the S3 frontend can call this endpoint
"""

import os
import json
import logging
from datetime import date, timedelta
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Config ────────────────────────────────────────────────────────────────────
TABLE_NAME = os.environ["TABLE_NAME"]
DAYS_TO_RETURN = 7

# ── AWS client ────────────────────────────────────────────────────────────────
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)

# ── CORS headers (required for S3-hosted frontend) ────────────────────────────
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
    "Content-Type": "application/json",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

class DecimalEncoder(json.JSONEncoder):
    """Handle Decimal types returned by DynamoDB."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def get_date_range(days: int) -> list[str]:
    """Return a list of ISO date strings for the last N days."""
    today = date.today()
    return [(today - timedelta(days=i)).isoformat() for i in range(days)]


def query_movers(date_range: list[str]) -> list[dict]:
    """
    Fetch mover records for the given dates.
    Uses batch_get_item for efficiency — avoids a full table scan.
    """
    if not date_range:
        return []

    try:
        response = dynamodb.batch_get_item(
            RequestItems={
                TABLE_NAME: {
                    "Keys": [{"date": d} for d in date_range],
                    "ProjectionExpression": "#dt, ticker, percent_change, closing_price",
                    "ExpressionAttributeNames": {"#dt": "date"},  # 'date' is a reserved word
                }
            }
        )

        items = response.get("Responses", {}).get(TABLE_NAME, [])

        # Handle unprocessed keys (DynamoDB throughput limit — rare but possible)
        unprocessed = response.get("UnprocessedKeys", {})
        if unprocessed:
            logger.warning(f"UnprocessedKeys returned — consider retrying: {list(unprocessed.keys())}")

        return items

    except ClientError as e:
        logger.error(f"DynamoDB query failed: {e}")
        raise


def format_item(item: dict) -> dict:
    """Normalize a DynamoDB item for the API response."""
    return {
        "date": item["date"],
        "ticker": item["ticker"],
        "percent_change": round(float(item.get("percent_change", 0)), 2),
        "closing_price": round(float(item.get("closing_price", 0)), 2),
    }


# ── Handler ───────────────────────────────────────────────────────────────────

def main(event, context):
    """Lambda entry point for GET /movers."""
    logger.info(f"Query request received: {json.dumps(event)}")

    # Handle preflight OPTIONS request
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    try:
        date_range = get_date_range(DAYS_TO_RETURN)
        logger.info(f"Querying for dates: {date_range}")

        items = query_movers(date_range)
        logger.info(f"Found {len(items)} records")

        # Format and sort by date descending (most recent first)
        movers = sorted(
            [format_item(item) for item in items],
            key=lambda x: x["date"],
            reverse=True,
        )

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps(
                {"movers": movers, "count": len(movers)},
                cls=DecimalEncoder,
            ),
        }

    except Exception as e:
        logger.error(f"Unhandled error in query handler: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Internal server error", "message": str(e)}),
        }
