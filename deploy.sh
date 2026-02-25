#!/bin/bash
# deploy.sh — full deploy script
# Usage: ./deploy.sh
# Run from the project root after setting up prerequisites in README.md

set -e

echo "============================================"
echo " Stocks Serverless Pipeline — Deploy Script"
echo "============================================"
echo ""

# ── Step 1: Build Lambda layer ────────────────────────────────────────────────
echo "[1/4] Building requests Lambda layer..."
bash build_layer.sh

# ── Step 2: CDK Deploy ───────────────────────────────────────────────────────
echo ""
echo "[2/4] Deploying AWS infrastructure with CDK..."
cd cdk
cdk deploy --require-approval never --outputs-file ../cdk-outputs.json
cd ..

echo ""
echo "[3/4] Reading CDK outputs..."
API_URL=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print(list(d.values())[0]['ApiUrl'])" 2>/dev/null)
BUCKET=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print(list(d.values())[0]['BucketName'])" 2>/dev/null)
WEBSITE=$(python3 -c "import json; d=json.load(open('cdk-outputs.json')); print(list(d.values())[0]['WebsiteUrl'])" 2>/dev/null)

if [ -z "$API_URL" ] || [ -z "$BUCKET" ]; then
  echo "ERROR: Could not read CDK outputs. Check cdk-outputs.json manually."
  exit 1
fi

echo "  API URL:     $API_URL"
echo "  S3 Bucket:   $BUCKET"
echo "  Website URL: $WEBSITE"

# ── Step 3: Inject API URL into frontend ─────────────────────────────────────
echo ""
echo "[4/4] Deploying frontend to S3..."
cp frontend/index.html /tmp/index.html
sed -i "s|__API_URL__|${API_URL}|g" /tmp/index.html

aws s3 cp /tmp/index.html s3://$BUCKET/index.html \
  --content-type "text/html" \
  --cache-control "no-cache"

echo ""
echo "============================================"
echo " ✅ Deploy complete!"
echo "============================================"
echo ""
echo "  🌐 Frontend: $WEBSITE"
echo "  🔌 API:      ${API_URL}movers"
echo ""
echo "To trigger the ingestion Lambda manually:"
echo "  aws lambda invoke --function-name stocks-ingestion --payload '{}' /tmp/out.json && cat /tmp/out.json"
echo ""
echo "To tear down all resources:"
echo "  cd cdk && cdk destroy"
