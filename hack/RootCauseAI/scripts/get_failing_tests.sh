#!/bin/bash
# get_failing_tests.sh - Get failing test file paths from nightly main e2e

REPO="konveyor/tackle2-ui"
WORKFLOW="nightly-main-e2e.yaml"
TMP_DIR=$(mktemp -d)

# Get last 2 run IDs
RUN_IDS=$(gh run list --repo "$REPO" --workflow "$WORKFLOW" --limit 1 --json databaseId --jq '.[].databaseId')

for RUN_ID in $RUN_IDS; do
  echo "Fetching artifacts from run $RUN_ID..." >&2

  # Get artifact names
  ARTIFACTS=$(gh api "repos/$REPO/actions/runs/$RUN_ID/artifacts" --jq '.artifacts[].name')

  # Download each artifact (skip secretsNeeded - requires private credentials)
  for name in $ARTIFACTS; do
    gh run download "$RUN_ID" --repo "$REPO" --name "$name" --dir "$TMP_DIR/$name" 2>/dev/null
  done
done

# Extract only test files that have failures
FILES=$(for xml in "$TMP_DIR"/*/report/junit/*.xml; do
  [ -f "$xml" ] || continue
  if grep -q '<failure' "$xml"; then
    grep 'file=' "$xml" | head -1 | sed 's/.*file="\([^"]*\)".*/\1/'
  fi
done | awk '!seen[$0]++')

# Write JSON file
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_FILE="$SCRIPT_DIR/artifacts/failing_tests.json"
mkdir -p "$(dirname "$OUTPUT_FILE")"

echo "$FILES" | jq -R -s 'split("\n") | map(select(length > 0))' > "$OUTPUT_FILE"

echo "Saved to $OUTPUT_FILE" >&2

rm -rf "$TMP_DIR"
