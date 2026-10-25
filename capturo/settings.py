"""Préférences persistantes de Capturo."""

import json
import os

from gi.repository import GLib

APP_DIR = os.path.join(GLib.get_user_config_dir(), "capturo")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")


def default_folder() -> str:
    """Dossier « Vidéos » de l'utilisateur, avec repli sur le dossier personnel."""
    folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_VIDEOS)
    if not folder:
        folder = os.path.join(os.path.expanduser("~"), "Vidéos")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        folder = os.path.expanduser("~")
    return folder


def load() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict) -> None:
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
    except OSError:
        pass
