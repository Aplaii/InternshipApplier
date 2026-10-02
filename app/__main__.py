"""Lancement : python -m app [--port 8000] [--open]"""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from . import config


def main() -> None:
    parser = argparse.ArgumentParser(description="InternshipApplier : offres de stage + candidatures.")
    parser.add_argument("--host", default=config.HOST, help="adresse d'écoute (défaut : 127.0.0.1)")
    parser.add_argument("--port", type=int, default=config.PORT, help="port (défaut : 8000)")
    parser.add_argument("--open", action="store_true", help="ouvrir l'interface dans le navigateur")
    args = parser.parse_args()

    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "::", "") else args.host
    url = f"http://{shown_host}:{args.port}"
    print(f"\n  InternshipApplier démarre sur {url}  (Ctrl+C pour arrêter)\n")
    if args.open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
