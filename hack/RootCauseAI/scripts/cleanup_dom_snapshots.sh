#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

rm -rf "$PROJECT_ROOT/artifacts/dom_snapshots"/*.html 2>/dev/null || true
echo 'DOM snapshots cleaned'
exit 0
