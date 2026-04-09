#!/bin/bash
# Cleanup test resources - requires PROJECT_PATH environment variable
# Usage: PROJECT_PATH=/path/to/project ./cleanup_test_resources.sh

echo 'Cleaning up test resources...'

# Kill any lingering VSCode processes
pkill -f 'code.*test-data-dir' 2>/dev/null || true

if [ -z "$PROJECT_PATH" ]; then
    echo 'Warning: PROJECT_PATH not set, skipping directory cleanup'
else
    TEST_DIR="$PROJECT_PATH/tests"

    # Clear test data directory
    rm -rf "$TEST_DIR/test-data-dir"/* 2>/dev/null || true

    # Remove cloned repos
    rm -rf "$TEST_DIR/coolstore" 2>/dev/null || true
    rm -rf "$TEST_DIR/inventory_management" 2>/dev/null || true
fi

echo 'Cleanup complete'
exit 0
