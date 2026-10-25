#!/usr/bin/env python3
"""Tests de non-régression du jeton de la Capturo Webcam.

Vérifie que la détection Wi-Fi/USB ne remplace jamais une URL protégée par
une URL sans jeton (erreur 401 Unauthorized), et que le jeton mémorisé est
réinjecté quand adb ne peut pas le transmettre.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from capturo import discovery, tokens  # noqa: E402

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def test_url_token() -> None:
    token = tokens.url_token
    check(token("") == "", "URL vide : aucun jeton attendu")
    check(
        token("http://127.0.0.1:8080/video?token=G4T8YQG2") == "G4T8YQG2",
        "jeton simple non extrait",
    )
    check(
        token("http://127.0.0.1:8080/video?x=1&token=A%20B") == "A B",
        "jeton encodé non décodé",
    )
    check(
        token("http://127.0.0.1:8080/video") == "",
        "URL sans jeton : aucun jeton attendu",
    )


def test_url_with_token() -> None:
    add = tokens.url_with_token
    check(
        add("http://127.0.0.1:8080/video", "G4T8YQG2")
        == "http://127.0.0.1:8080/video?token=G4T8YQG2",
        "jeton non ajouté à une URL simple",
    )
    check(
        add("http://192.168.1.20:8080/video?x=1", "A B")
        == "http://192.168.1.20:8080/video?x=1&token=A%20B",
        "jeton mal ajouté à une URL avec paramètres",
    )
    check(
        add("http://127.0.0.1:8080/video?token=OLD", "NEW")
        == "http://127.0.0.1:8080/video?token=OLD",
        "un jeton existant ne doit pas être écrasé",
    )
    check(add("", "NEW") == "", "une URL vide doit rester vide")


def test_usb_with_token() -> None:
    add = discovery.usb_with_token
    check(
        add("http://127.0.0.1:8080/video", {"G4T8YQG2"})
        == "http://127.0.0.1:8080/video?token=G4T8YQG2",
        "le jeton Wi-Fi doit être réutilisé pour l'USB",
    )
    check(
        add("http://127.0.0.1:8080/video?token=OLD", {"NEW"})
        == "http://127.0.0.1:8080/video?token=OLD",
        "l'URL USB déjà protégée ne doit pas changer",
    )
    check(
        add("http://127.0.0.1:8080/video", {"A", "B"})
        == "http://127.0.0.1:8080/video",
        "plusieurs jetons : ne pas deviner",
    )
    check(
        add("http://127.0.0.1:8080/video", set())
        == "http://127.0.0.1:8080/video",
        "aucun jeton découvert : URL inchangée",
    )
    check(
        add("http://127.0.0.1:8080/video", {"A B"})
        == "http://127.0.0.1:8080/video?token=A%20B",
        "jeton USB non encodé",
    )


def test_detection_preserves_token() -> None:
    preserve = tokens.preserve_token
    check(
        preserve(
            "http://127.0.0.1:8080/video",
            "http://127.0.0.1:8080/video?token=G4T8YQG2",
        )
        == "http://127.0.0.1:8080/video?token=G4T8YQG2",
        "le jeton de l'ancienne URL doit être conservé",
    )
    check(
        preserve(
            "http://127.0.0.1:8080/video",
            "http://127.0.0.1:8080/video",
            "G4T8YQG2",
        )
        == "http://127.0.0.1:8080/video?token=G4T8YQG2",
        "le jeton mémorisé doit être réinjecté",
    )
    check(
        preserve(
            "http://192.168.1.20:8080/video?token=NEW",
            "http://127.0.0.1:8080/video?token=OLD",
        )
        == "http://192.168.1.20:8080/video?token=NEW",
        "le jeton annoncé par le téléphone doit primer",
    )
    check(
        preserve(
            "http://127.0.0.1:8080/video",
            "http://127.0.0.1:8080/video",
        )
        == "http://127.0.0.1:8080/video",
        "sans jeton connu, l'URL doit rester inchangée",
    )


def main() -> int:
    for test in (
        test_url_token,
        test_url_with_token,
        test_usb_with_token,
        test_detection_preserves_token,
    ):
        test()
    if failures:
        for failure in failures:
            print(f"échec : {failure}", file=sys.stderr)
        return 1
    print("OK · jeton conservé en Wi-Fi et en USB/adb")
    return 0


if __name__ == "__main__":
    sys.exit(main())
