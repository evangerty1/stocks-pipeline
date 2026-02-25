#!/bin/bash
# build_layer.sh — builds the requests Lambda layer
# Run this ONCE before `cdk deploy`
# Requires: pip3, Docker (optional), or just pip3 for simple builds

set -e

LAYER_DIR="layers/requests/python"

echo "Building requests Lambda layer..."
mkdir -p "$LAYER_DIR"

pip3 install requests -t "$LAYER_DIR" --quiet --upgrade

echo "Layer built at $LAYER_DIR"
echo "You can now run: cd cdk && cdk deploy"
