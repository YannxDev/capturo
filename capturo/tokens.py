"""Manipulation du jeton d'accès des Capturo Webcam.

Le téléphone protège ``/video`` et ``/audio`` par un jeton. adb ne le
transmet pas : Capturo doit donc conserver celui déjà connu quand la
détection remplace l'URL de la caméra.
"""

from __future__ import annotations

from urllib.parse import parse_qs, quote, urlsplit, urlunsplit


def url_token(url: str) -> str:
    """Jeton éventuel présent dans l'URL de la caméra."""
    if not url:
        return ""
    try:
        values = parse_qs(urlsplit(url).query).get("token", [])
    except ValueError:
        return ""
    return values[0].strip() if values else ""


def url_with_token(url: str, token: str) -> str:
    """Ajoute le jeton à une URL qui n'en contient pas."""
    if not url or not token or url_token(url):
        return url
    parts = urlsplit(url)
    separator = "&" if parts.query else ""
    query = f"{parts.query}{separator}token={quote(token)}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def preserve_token(new_url: str, old_url: str = "", stored_token: str = "") -> str:
    """Réinjecte le jeton connu dans une URL détectée qui n'en a pas.

    Le jeton de l'ancienne URL est prioritaire, puis celui mémorisé dans la
    configuration. Une URL qui annonce déjà son propre jeton est conservée.
    """
    token = url_token(old_url) or stored_token
    return url_with_token(new_url, token)
