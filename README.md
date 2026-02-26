# Stocks Serverless Pipeline

A fully automated AWS serverless pipeline that tracks a watchlist of tech stocks, records the biggest daily mover, and displays the history on a public website.

## Architecture

```
EventBridge (daily cron)
    → Ingestion Lambda (fetches stock data, finds top mover, writes to DynamoDB)

API Gateway GET /movers
    → Query Lambda (reads last 7 days from DynamoDB)
    → S3 Static Website (fetches API, displays results)
```

## Watchlist
`AAPL`, `MSFT`, `GOOGL`, `AMZN`, `TSLA`, `NVDA`

---

## Prerequisites

- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html) configured (`aws configure`)
- [Python 3.11+](https://www.python.org/downloads/)
- [Node.js 18+](https://nodejs.org/) (required by AWS CDK)
- [AWS CDK CLI](https://docs.aws.amazon.com/cdk/v2/guide/getting_started.html): `npm install -g aws-cdk`
- A free API key from [Massive](https://massive.com) (no credit card required)

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/stocks-pipeline.git
cd stocks-pipeline
```

### 2. Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Store your Massive API key in AWS Secrets Manager

```bash
aws secretsmanager create-secret \
  --name "stocks/massive-api-key" \
  --secret-string "YOUR_MASSIVE_API_KEY_HERE"
```

### 4. Bootstrap CDK (first time only per AWS account/region)

```bash
cd cdk
cdk bootstrap
```

### 5. Deploy the stack

```bash
cdk deploy
```

CDK will output:
- `ApiUrl` — your API Gateway endpoint
- `WebsiteUrl` — your S3 static website URL

### 6. Deploy the frontend

Update the API URL in the frontend config, then upload to S3:

```bash
# Replace YOUR_API_URL with the ApiUrl output from cdk deploy
cd ../frontend
sed -i 's|__API_URL__|https://YOUR_API_URL/prod|g' index.html

# Upload to S3 (replace YOUR_BUCKET_NAME with the output from cdk deploy)
aws s3 sync . s3://YOUR_BUCKET_NAME --delete
```

---

## Testing the Lambda manually

```bash
aws lambda invoke \
  --function-name stocks-ingestion \
  --payload '{}' \
  response.json

cat response.json
```

## Checking logs

```bash
aws logs tail /aws/lambda/stocks-ingestion --follow
aws logs tail /aws/lambda/stocks-query --follow
```

## Tear down 

```bash
cd cdk
cdk destroy
```

---

## Project Structure

```
stocks-pipeline/
├── cdk/                    # AWS CDK infrastructure (Python)
│   ├── app.py              # CDK entry point
│   ├── stacks/
│   │   └── pipeline_stack.py
│   └── cdk.json
├── lambdas/
│   ├── ingestion/          # Cron-triggered: fetches + stores top mover
│   │   └── handler.py
│   └── query/              # API-triggered: returns last 7 days
│       └── handler.py
├── frontend/               # Plain HTML/JS SPA hosted on S3
│   └── index.html
├── requirements.txt
├── .gitignore
└── README.md
```

## Trade-offs & Notes

- **Scan vs GSI**: DynamoDB uses a simple Scan with filter for last 7 days. Given the tiny dataset (one record/day), this is efficient and avoids the complexity of a GSI. At scale, a GSI on `date` would be the right call.
- **Python across the board**: Using Python for both CDK and Lambda avoids context-switching and keeps the `boto3` SDK consistent everywhere.
- **Market closed handling**: The ingestion Lambda detects weekends and market holidays via an empty API response and exits gracefully without writing a bad record.
- **Error handling**: If a single ticker fails (rate limit, timeout), it's logged to CloudWatch and skipped. The pipeline continues with remaining tickers rather than failing the whole run.
- **S3 over Amplify**: Plain S3 static hosting is simpler to provision via CDK and stays fully within free tier.
