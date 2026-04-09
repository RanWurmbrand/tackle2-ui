#!/bin/bash
# Renames the latest screenshot to match the latest DOM snapshot filename
# Usage: ./rename_screenshot_to_match_dom.sh [artifacts_dir]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

ARTIFACTS_DIR="${1:-$PROJECT_ROOT/artifacts}"
DOM_SNAPSHOTS_DIR="$ARTIFACTS_DIR/dom_snapshots"

if [ ! -d "$DOM_SNAPSHOTS_DIR" ]; then
    echo "No dom_snapshots directory found"
    exit 0
fi

# Find latest DOM snapshot
LATEST_DOM=$(ls -t "$DOM_SNAPSHOTS_DIR"/*.html 2>/dev/null | head -1)

if [ -z "$LATEST_DOM" ]; then
    echo "No DOM snapshot found"
    exit 0
fi

# Find latest screenshot
LATEST_PNG=$(ls -t "$DOM_SNAPSHOTS_DIR"/*.png 2>/dev/null | head -1)

if [ -z "$LATEST_PNG" ]; then
    echo "No screenshot found"
    exit 0
fi

# Get DOM filename without extension
DOM_BASENAME=$(basename "$LATEST_DOM" .html)

# Rename screenshot to match
NEW_PNG="$DOM_SNAPSHOTS_DIR/${DOM_BASENAME}.png"

if [ "$LATEST_PNG" != "$NEW_PNG" ]; then
    mv "$LATEST_PNG" "$NEW_PNG"
    echo "Renamed: $NEW_PNG"
else
    echo "Already matches: $NEW_PNG"
fi
