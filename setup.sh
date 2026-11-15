#!/usr/bin/env sh
# macOS/Linux (and Git Bash) entry point: finds a supported Python and runs setup_dev.py.
# Usage: ./setup.sh [options]   e.g. --provision-db, --recreate-venv, --help
set -eu

DIR=$(cd "$(dirname "$0")" && pwd)
VERSION_TEST='import sys; sys.exit(0 if (3, 12) <= sys.version_info[:2] <= (3, 14) else 1)'

for py in python3.13 python3.12 python3.14 python3 python; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c "$VERSION_TEST" >/dev/null 2>&1; then
        exec "$py" "$DIR/setup_dev.py" "$@"
    fi
done

# Git Bash on Windows: fall back to the py launcher.
if command -v py >/dev/null 2>&1; then
    for v in 3.13 3.12 3.14; do
        if py "-$v" -c "$VERSION_TEST" >/dev/null 2>&1; then
            exec py "-$v" "$DIR/setup_dev.py" "$@"
        fi
    done
fi

echo "ERROR: Python 3.12 or 3.13 was not found." >&2
echo "Install it (https://www.python.org/downloads/, or your package manager) and re-run." >&2
exit 1
