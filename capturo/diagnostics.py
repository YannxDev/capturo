"""Journal de diagnostic de Capturo.

Les messages sont écrits dans ``~/.cache/capturo/capturo.log`` pour pouvoir
comprendre un blocage à l'arrêt sans lancer l'application dans un terminal.
Le journal est tronqué automatiquement au-delà de 256 Kio.
"""

from __future__ import annotations

import os
import threading
import time

LOG_PATH = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "capturo",
    "capturo.log",
)
MAX_SIZE = 256 * 1024


def log(message: str) -> None:
    """Ajoute une ligne horodatée au journal (sans jamais lever d'erreur)."""
    line = (
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"[{threading.current_thread().name}] {message}\n"
    )
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        try:
            if os.path.getsize(LOG_PATH) > MAX_SIZE:
                os.replace(LOG_PATH, LOG_PATH + ".1")
        except OSError:
            pass
        with open(LOG_PATH, "a", encoding="utf-8") as stream:
            stream.write(line)
    except OSError:
        pass


def path() -> str:
    """Chemin du journal, pour l'afficher dans l'interface."""
    return LOG_PATH
