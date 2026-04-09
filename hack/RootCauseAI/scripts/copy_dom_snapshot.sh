#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

PROJECT_PATH="$1"
ARTIFACTS_DIR="${2:-$PROJECT_ROOT/artifacts}"

if [ -z "$PROJECT_PATH" ]; then
    echo "Usage: $0 <project_path> [artifacts_dir]"
    exit 1
fi

ROOTCAUSE_DIR="$PROJECT_PATH/run/RootcauseAI"
SNAPSHOT=$(find "$ROOTCAUSE_DIR" -name "*.html" -type f 2>/dev/null | head -1)

if [ -z "$SNAPSHOT" ]; then
    echo "No DOM snapshot found in $PROJECT_PATH"
    exit 0
fi

mkdir -p "$ARTIFACTS_DIR/dom_snapshots"

TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
BASENAME=$(basename "$SNAPSHOT" .html)
OUTPUT="$ARTIFACTS_DIR/dom_snapshots/${BASENAME}_${TIMESTAMP}.html"

cp "$SNAPSHOT" "$OUTPUT"
echo "Copied: $OUTPUT"
