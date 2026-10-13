#!/usr/bin/env bash
# Lance le modèle « Stages FLE / DIFLES » (Europe et Canada) sur le port 8001.
# Même application que ../run.sh, avec le profil fle et sa propre base (fle/data/).
# Options : --port 8001, --open (ouvre le navigateur).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export IA_PROFILE=fle
export IA_DATA_DIR="${IA_DATA_DIR:-$HERE/data}"
export IA_PORT="${IA_PORT:-8001}"
exec "$HERE/../run.sh" "$@"
