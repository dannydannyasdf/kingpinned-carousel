#!/bin/bash

# Kingpinned Carousel Setup and Runner Script
# This script sets up a Python virtual environment, installs dependencies,
# checks for ClickHouse client, and runs the application.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "Kingpinned Carousel Setup & Runner"
echo "=================================="

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "Virtual environment created at $VENV_DIR"
fi

# Activate virtual environment
echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Install/upgrade dependencies
echo "Installing dependencies from requirements.txt..."
pip install --upgrade pip
pip install -r "$SCRIPT_DIR/requirements.txt"

# Check for ClickHouse client
echo ""
echo "Checking system dependencies..."
if ! command -v clickhouse-client >/dev/null 2>&1; then
    echo "⚠️  WARNING: clickhouse-client not found in PATH"
    echo "   The application will start but ClickHouse features will be disabled."
    echo "   To install ClickHouse client on Ubuntu/Debian:"
    echo "   sudo apt-get install clickhouse-client"
    echo ""
else
    echo "✓ clickhouse-client found"
fi

# Check CH_PASSWORD environment variable
if [ -z "$CH_PASSWORD" ]; then
    echo "⚠️  WARNING: CH_PASSWORD environment variable not set"
    echo "   Using default password 'asdf'. To set your password:"
    echo "   export CH_PASSWORD='your_password_here'"
    echo ""
else
    echo "✓ CH_PASSWORD is set"
fi

echo "Starting Kingpinned Carousel..."
echo ""

# Run the application
exec python3 "$SCRIPT_DIR/turntable_keysGit96.py"