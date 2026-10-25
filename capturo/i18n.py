"""Initialisation de la traduction (gettext).

Capturo est écrit en français. Les chaînes marquées avec ``_()`` peuvent
être traduites via les fichiers ``po/*.po`` ; sans traduction installée,
le français reste affiché tel quel.
"""

from __future__ import annotations

import gettext
import locale
import os

DOMAIN = "capturo"

# Dossier local utilisé pendant le développement (make install ou tests).
_LOCAL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "locale")

_ = gettext.gettext


def setup() -> None:
    """Configure le domaine de traduction pour GTK et gettext."""
    candidates = []
    override = os.environ.get("CAPTURO_LOCALE_DIR")
    if override:
        candidates.append(override)
    candidates.extend(
        (
            "/app/share/locale",  # Flatpak
            "/usr/share/locale",  # paquet Debian
            _LOCAL_DIR,
        )
    )
    for directory in candidates:
        if not directory or not os.path.isdir(directory):
            continue
        try:
            locale.bindtextdomain(DOMAIN, directory)
            locale.textdomain(DOMAIN)
        except (AttributeError, OSError):
            pass
        gettext.bindtextdomain(DOMAIN, directory)
        gettext.textdomain(DOMAIN)
        return
