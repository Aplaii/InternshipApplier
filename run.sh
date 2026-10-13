#!/usr/bin/env bash
# Lance InternshipApplier. Au premier lancement, crée l'environnement Python (.venv)
# et installe les dépendances. Options : --port 8000, --open (ouvre le navigateur).
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV=".venv"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3 est introuvable : installez-le (ex. sudo apt install python3 python3-venv)." >&2
  exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Python 3.10 ou plus récent est nécessaire ($("$PYTHON" --version 2>&1) trouvé)." >&2
  exit 1
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "Création de l'environnement Python (.venv)…"
  rm -rf "$VENV"
  if ! "$PYTHON" -m venv "$VENV" 2>/dev/null; then
    # Certains dossiers réseau (NFS, CIFS…) refusent les liens symboliques : copie des fichiers.
    rm -rf "$VENV"
    mkdir -p "$VENV/lib64"
    "$PYTHON" -m venv --copies "$VENV"
  fi
fi

STAMP="$VENV/.requirements.sha256"
CURRENT="$("$VENV/bin/python" -c 'import hashlib; print(hashlib.sha256(open("requirements.txt", "rb").read()).hexdigest())')"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$CURRENT" ]; then
  echo "Installation des dépendances…"
  "$VENV/bin/python" -m pip install --disable-pip-version-check -q --upgrade pip
  "$VENV/bin/python" -m pip install --disable-pip-version-check -q -r requirements.txt
  echo "$CURRENT" > "$STAMP"
fi

exec "$VENV/bin/python" -m app "$@"
