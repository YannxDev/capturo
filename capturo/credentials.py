"""Stockage de la clé RTMP dans le trousseau GNOME.

La clé de diffusion ne doit pas traîner en clair dans ``config.json``.
Quand libsecret est disponible, elle est conservée dans le trousseau de
l'utilisateur ; sinon Capturo retombe sur l'ancien comportement (fichier de
configuration) pour ne rien casser.
"""

from __future__ import annotations

from .diagnostics import log

SERVICE = "io.github.yannick.Capturo"
LABEL = "Clé de diffusion RTMP — Capturo"
ATTRIBUTES = {"purpose": "rtmp"}

_schema = None
_available: bool | None = None


def _get_schema():
    global _schema
    if _schema is not None:
        return _schema
    try:
        import gi

        gi.require_version("Secret", "1")
        from gi.repository import Secret

        _schema = Secret.Schema.new(
            SERVICE,
            Secret.SchemaFlags.NONE,
            {"purpose": Secret.SchemaAttributeType.STRING},
        )
    except (ImportError, ValueError):
        _schema = False
    return _schema


def available() -> bool:
    """Vrai si le trousseau peut être utilisé sur cette machine."""
    global _available
    if _available is None:
        _available = bool(_get_schema())
    return _available


def load_key() -> str | None:
    """Relit la clé depuis le trousseau, ou None."""
    if not available():
        return None
    try:
        from gi.repository import Secret

        value = Secret.password_lookup_sync(
            _get_schema(), ATTRIBUTES, None
        )
    except Exception as exc:  # noqa: BLE001 - trousseau verrouillé/absent
        log(f"trousseau : lecture impossible ({exc})")
        return None
    return value or None


def store_key(key: str) -> bool:
    """Enregistre (ou efface) la clé. Renvoie True en cas de succès."""
    if not available():
        return False
    try:
        from gi.repository import Secret

        if key:
            Secret.password_store_sync(
                _get_schema(),
                ATTRIBUTES,
                Secret.COLLECTION_DEFAULT,
                LABEL,
                key,
                None,
            )
        else:
            Secret.password_clear_sync(_get_schema(), ATTRIBUTES, None)
    except Exception as exc:  # noqa: BLE001 - trousseau verrouillé/absent
        log(f"trousseau : écriture impossible ({exc})")
        return False
    return True
