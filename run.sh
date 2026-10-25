#!/usr/bin/env bash
# Lance l'application depuis les sources, sans installation.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec python3 -m capturo "$@"
