"""
Ingestion Lambda — stocks-ingestion
Triggered by EventBridge daily cron after market close.

Responsibilities:
  1. Fetch daily Open/Close for each stock in WATCHLIST from Massive API
  2. Calculate % change: ((Close - Open) / Open) * 100
  3. Find the ticker with the highest absolute % change (winner)
  4. Write the result to DynamoDB

Error handling:
  - Individual ticker failures are logged and skipped (pipeline continues)
  - If ALL tickers fail, logs critical error and exits without writing
  - If market is closed (empty data), exits gracefully without writing
"""

import os
import json
import logging
import time
from datetime import date, datetime, timezone

import boto3
import requests
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Config ────────────────────────────────────────────────────────────────────
WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA"]
TABLE_NAME = os.environ["TABLE_NAME"]
SECRET_NAME = os.environ["SECRET_NAME"]

MASSIVE_BASE_URL = "https://api.massive.com/v1"  # adjust if Massive endpoint differs
REQUEST_TIMEOUT = 10   # seconds per API call
RETRY_DELAY = 1.0      # seconds between ticker requests (rate limit safety)
MAX_RETRIES = 2        # retries per ticker on transient failures

# ── AWS clients (module-level for Lambda container reuse) ─────────────────────
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)
secrets_client = boto3.client("secretsmanager")


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_api_key() -> str:
    """Retrieve Massive API key from Secrets Manager."""
    try:
        response = secrets_client.get_secret_value(SecretId=SECRET_NAME)
        return response["SecretString"].strip()
    except ClientError as e:
        logger.critical(f"Failed to retrieve API key from Secrets Manager: {e}")
        raise


def fetch_ticker(ticker: str, api_key: str, trade_date: str) -> dict | None:
    """
    Fetch daily Open/Close for a single ticker from the Massive API.
    Returns {"O": float, "C": float} or None if data unavailable.

    Retries up to MAX_RETRIES times on transient HTTP errors.
    """
    url = f"{MASSIVE_BASE_URL}/aggs/ticker/{ticker}/range/1/day/{trade_date}/{trade_date}"
    headers = {"Authorization": f"Bearer {api_key}"}

    for attempt in range(1, MAX_RETRIES + 2):  # +2 because range is exclusive
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)

            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])

                if not results:
                    logger.warning(f"{ticker}: No results for {trade_date} — market may be closed")
                    return None

                bar = results[0]
                open_price = bar.get("o") or bar.get("O")
                close_price = bar.get("c") or bar.get("C")

                if open_price is None or close_price is None:
                    logger.warning(f"{ticker}: Missing open/close in response: {bar}")
                    return None

                if open_price == 0:
                    logger.warning(f"{ticker}: Open price is 0, cannot calculate % change")
                    return None

                return {"O": float(open_price), "C": float(close_price)}

            elif resp.status_code == 429:
                wait = RETRY_DELAY * (2 ** (attempt - 1))  # exponential backoff
                logger.warning(f"{ticker}: Rate limited (429). Waiting {wait}s before retry {attempt}/{MAX_RETRIES}")
                time.sleep(wait)
                continue

            elif resp.status_code in (500, 502, 503, 504):
                logger.warning(f"{ticker}: Server error {resp.status_code} on attempt {attempt}")
                time.sleep(RETRY_DELAY)
                continue

            else:
                logger.error(f"{ticker}: Unexpected status {resp.status_code}: {resp.text[:200]}")
                return None

        except requests.exceptions.Timeout:
            logger.warning(f"{ticker}: Request timed out on attempt {attempt}")
            time.sleep(RETRY_DELAY)
            continue

        except requests.exceptions.ConnectionError as e:
            logger.error(f"{ticker}: Connection error: {e}")
            return None

        except Exception as e:
            logger.error(f"{ticker}: Unexpected error: {e}")
            return None

    logger.error(f"{ticker}: All {MAX_RETRIES + 1} attempts failed")
    return None


def calculate_pct_change(open_price: float, close_price: float) -> float:
    """Calculate percentage change from open to close."""
    return ((close_price - open_price) / open_price) * 100


def write_winner(trade_date: str, ticker: str, pct_change: float, closing_price: float) -> None:
    """Write the top mover record to DynamoDB."""
    try:
        table.put_item(
            Item={
                "date": trade_date,
                "ticker": ticker,
                "percent_change": str(round(pct_change, 4)),  # DynamoDB Decimal-safe
                "closing_price": str(round(closing_price, 2)),
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        logger.info(f"Wrote winner to DynamoDB: {ticker} {pct_change:+.2f}% on {trade_date}")
    except ClientError as e:
        logger.critical(f"DynamoDB write failed: {e}")
        raise


# ── Handler ───────────────────────────────────────────────────────────────────

def main(event, context):
    """Lambda entry point."""
    trade_date = date.today().isoformat()  # e.g. "2025-06-10"
    logger.info(f"Starting ingestion for {trade_date}")

    # Retrieve API key once
    try:
        api_key = get_api_key()
    except Exception:
        return {"statusCode": 500, "body": "Failed to retrieve API key"}

    # Collect results for all tickers
    results = []
    failed_tickers = []

    for ticker in WATCHLIST:
        data = fetch_ticker(ticker, api_key, trade_date)
        time.sleep(RETRY_DELAY)  # be polite to the API between tickers

        if data is None:
            failed_tickers.append(ticker)
            continue

        pct = calculate_pct_change(data["O"], data["C"])
        results.append({
            "ticker": ticker,
            "pct_change": pct,
            "closing_price": data["C"],
        })
        logger.info(f"{ticker}: open={data['O']:.2f} close={data['C']:.2f} change={pct:+.2f}%")

    # Log partial failures
    if failed_tickers:
        logger.warning(f"Failed to fetch data for: {failed_tickers}")

    # Guard: no data at all (market closed, holiday, total API failure)
    if not results:
        logger.warning("No results collected — market likely closed or API unavailable. Skipping write.")
        return {
            "statusCode": 200,
            "body": json.dumps({"message": "No data — market closed or API unavailable", "date": trade_date}),
        }

    # Find the winner: highest absolute % change
    winner = max(results, key=lambda x: abs(x["pct_change"]))
    logger.info(
        f"Winner: {winner['ticker']} with {winner['pct_change']:+.2f}% change "
        f"(close: ${winner['closing_price']:.2f})"
    )

    # Write to DynamoDB
    write_winner(
        trade_date=trade_date,
        ticker=winner["ticker"],
        pct_change=winner["pct_change"],
        closing_price=winner["closing_price"],
    )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "date": trade_date,
            "winner": winner["ticker"],
            "percent_change": round(winner["pct_change"], 4),
            "closing_price": round(winner["closing_price"], 2),
            "tickers_processed": len(results),
            "tickers_failed": failed_tickers,
        }),
    }
