#!/bin/bash
# Copies the latest Cypress screenshot to artifacts/dom_snapshots
# Usage: ./copy_cypress_screenshot.sh <project_path> [artifacts_dir]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

PROJECT_PATH="$1"
ARTIFACTS_DIR="${2:-$PROJECT_ROOT/artifacts}"

if [ -z "$PROJECT_PATH" ]; then
    echo "Usage: $0 <project_path> [artifacts_dir]"
    exit 1
fi

# Find latest screenshot
SCREENSHOT=$(find "$PROJECT_PATH" -path "*/screenshots/*.png" -type f 2>/dev/null | head -1)

if [ -z "$SCREENSHOT" ]; then
    echo "No screenshot found in $PROJECT_PATH"
    exit 0
fi

# Create output dir
mkdir -p "$ARTIFACTS_DIR/dom_snapshots"

# Generate timestamped filename
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
BASENAME=$(basename "$SCREENSHOT" .png)
OUTPUT="$ARTIFACTS_DIR/dom_snapshots/${BASENAME}_${TIMESTAMP}.png"

cp "$SCREENSHOT" "$OUTPUT"
echo "Copied: $OUTPUT"
