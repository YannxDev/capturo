"""Détection et lancement des lecteurs et éditeurs vidéo installés."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess

# (nom affiché, commande)
KNOWN_EDITORS = (
    ("Kdenlive", "kdenlive"),
    ("Shotcut", "shotcut"),
    ("Pitivi", "pitivi"),
    ("OpenShot", "openshot-qt"),
    ("OpenShot", "openshot"),
    ("Avidemux", "avidemux3_qt5"),
    ("Avidemux", "avidemux3_qt"),
    ("Avidemux", "avidemux"),
    ("LosslessCut", "losslesscut"),
    ("Flowblade", "flowblade"),
    ("Olive", "olive-editor"),
    ("Olive", "olive"),
    ("Lightworks", "lightworks"),
    ("Cinelerra", "cinelerra-gg"),
    ("Cinelerra", "cinelerra"),
    ("DaVinci Resolve", "davinci-resolve"),
    ("DaVinci Resolve", "resolve"),
)

# Lecteurs vidéo natifs : ils permettent de relire l'enregistrement même
# lorsqu'aucun éditeur n'est installé.
KNOWN_PLAYERS = (
    ("VLC", "vlc"),
    ("Celluloid", "celluloid"),
    ("Haruna", "haruna"),
    ("Clapper", "clapper"),
    ("MPV", "mpv"),
    ("SMPlayer", "smplayer"),
    ("MPlayer", "mplayer"),
    ("Dragon Player", "dragon"),
    ("Parole", "parole"),
    ("Showtime", "showtime"),
    ("Vidéos", "totem"),
)

# Applications Flatpak courantes (nom affiché, identifiant Flatpak).
FLATPAK_EDITORS = (
    ("Kdenlive", "org.kde.kdenlive"),
    ("Shotcut", "org.shotcut.Shotcut"),
    ("Pitivi", "org.pitivi.Pitivi"),
    ("OpenShot", "org.openshot.OpenShot"),
    ("LosslessCut", "no.mifi.losslesscut"),
    ("Flowblade", "io.github.jliljebl.Flowblade"),
    ("Olive", "org.oliveeditor.Olive"),
    ("Blender", "org.blender.Blender"),
)

FLATPAK_PLAYERS = (
    ("VLC", "org.videolan.VLC"),
    ("Showtime", "org.gnome.Showtime"),
    ("Vidéos (Totem)", "org.gnome.Totem"),
    ("Celluloid", "io.github.celluloid_player.Celluloid"),
    ("Haruna", "io.github.hhkbp2.Haruna"),
    ("Haruna", "org.kde.haruna"),
    ("Clapper", "com.github.rafostar.Clapper"),
    ("SMPlayer", "org.smplayer.SMPlayer"),
    ("MPV", "io.mpv.Mpv"),
    ("Dragon Player", "org.kde.dragonplayer"),
)

# Applications Snap courantes (nom affiché, nom du snap).
SNAP_EDITORS = (
    ("Kdenlive", "kdenlive"),
    ("Shotcut", "shotcut"),
    ("Pitivi", "pitivi"),
    ("OpenShot", "openshot"),
    ("LosslessCut", "losslesscut"),
    ("Flowblade", "flowblade"),
    ("Olive", "olive-editor"),
    ("Blender", "blender"),
)

SNAP_PLAYERS = (
    ("VLC", "vlc"),
    ("Celluloid", "celluloid"),
    ("Haruna", "haruna"),
    ("MPV", "mpv"),
)

# Certains éditeurs propriétaires ne sont pas dans le PATH.
COMMON_EDITOR_PATHS = (
    ("DaVinci Resolve", "/opt/resolve/bin/resolve"),
    ("DaVinci Resolve", "/opt/davinci-resolve/bin/resolve"),
    ("DaVinci Resolve", "/usr/local/bin/resolve"),
    ("DaVinci Resolve", "~/.local/bin/resolve"),
    ("Lightworks", "/usr/lib/lightworks/ntsc"),
    ("Lightworks", "/usr/lib/lightworks/lightworks"),
)

# Dossiers d'AppImage et de lanceurs de bureau.
APPIMAGE_DIRS = (
    "~/.local/bin",
    "~/Applications",
    "~/AppImages",
)
DESKTOP_DIRS = (
    "~/.local/share/applications",
    "/usr/local/share/applications",
    "/usr/share/applications",
    "~/.local/share/flatpak/exports/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    "/var/lib/snapd/desktop/applications",
)

PLAYER_KEYWORDS = (
    "vlc",
    "celluloid",
    "haruna",
    "clapper",
    "mpv",
    "smplayer",
    "mplayer",
    "dragon player",
    "parole",
    "showtime",
    "totem",
    "video player",
    "vidéo player",
    "lecteur vidéo",
    "lecteur video",
    "vidéos",
    "videos",
)

EDITOR_KEYWORDS = (
    "kdenlive",
    "shotcut",
    "pitivi",
    "openshot",
    "avidemux",
    "losslesscut",
    "flowblade",
    "olive-editor",
    "olive editor",
    "davinci",
    "resolve",
    "lightworks",
    "cinelerra",
    "blender",
    "kino",
    "natron",
    "video editor",
    "éditeur vidéo",
)


def _add(
    found: list[tuple[str, str]],
    names: set[str],
    name: str,
    command: str,
) -> None:
    key = name.strip().casefold()
    if not key or key in names:
        return
    found.append((name.strip(), command.strip()))
    names.add(key)


def _which(command: str) -> str | None:
    """Cherche une commande, y compris dans les dossiers snap/Flatpak."""
    found = shutil.which(command)
    if found:
        return found
    for folder in ("/snap/bin", "/var/lib/flatpak/exports/bin"):
        candidate = os.path.join(folder, command)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _flatpak_apps() -> set[str]:
    flatpak = shutil.which("flatpak")
    if not flatpak:
        return set()
    try:
        result = subprocess.run(
            [flatpak, "list", "--app", "--columns=application"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return {line.strip() for line in result.stdout.splitlines() if "." in line}


def _snap_apps() -> set[str]:
    snap = shutil.which("snap")
    if not snap:
        return set()
    try:
        result = subprocess.run(
            [snap, "list"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    apps: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] != "Name":
            apps.add(parts[0])
    return apps


def _read_desktop_entry(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    in_entry = False
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            for raw in stream:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("["):
                    in_entry = line == "[Desktop Entry]"
                    continue
                if not in_entry or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    except OSError:
        return {}
    return values


def _looks_like_editor(
    name: str,
    exec_line: str,
    categories: str,
    filename: str,
) -> bool:
    haystack = f"{name} {exec_line} {categories} {filename}".casefold()
    return any(keyword in haystack for keyword in EDITOR_KEYWORDS)


def _looks_like_player(
    name: str,
    exec_line: str,
    categories: str,
    filename: str,
) -> bool:
    # On se limite aux lecteurs vidéo connus : les catégories « Player »
    # seules incluent aussi des lecteurs de musique (Spotify, Red…).
    haystack = f"{name} {exec_line} {categories} {filename}".casefold()
    return any(keyword in haystack for keyword in PLAYER_KEYWORDS)


def _desktop_editors() -> list[tuple[str, str]]:
    editors: list[tuple[str, str]] = []
    for raw_dir in DESKTOP_DIRS:
        folder = os.path.expanduser(raw_dir)
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            continue
        for filename in entries:
            if not filename.endswith(".desktop"):
                continue
            path = os.path.join(folder, filename)
            values = _read_desktop_entry(path)
            if not values:
                continue
            if values.get("NoDisplay", "false").casefold() == "true":
                continue
            if values.get("Hidden", "false").casefold() == "true":
                continue
            name = values.get("Name[fr]") or values.get("Name") or filename[:-8]
            exec_line = values.get("Exec", "")
            categories = values.get("Categories", "")
            if not exec_line or not _looks_like_editor(
                name, exec_line, categories, filename
            ):
                continue
            try:
                arguments = shlex.split(exec_line)
            except ValueError:
                continue
            # Retire les jetons %f/%U/%i… : le chemin est ajouté par launch().
            arguments = [arg for arg in arguments if "%" not in arg]
            if not arguments:
                continue
            base = arguments[0]
            if base == "env":
                index = 1
                while index < len(arguments) and "=" in arguments[index]:
                    index += 1
                if index >= len(arguments):
                    continue
                base = arguments[index]
            if base.startswith("/"):
                if not os.path.exists(base):
                    continue
            elif not shutil.which(base):
                continue
            editors.append((name, shlex.join(arguments)))
    return editors


def _desktop_players() -> list[tuple[str, str]]:
    """Détecte les lecteurs exposés par un lanceur .desktop."""
    players: list[tuple[str, str]] = []
    known_flatpak_ids = {
        app_id for _name, app_id in (*FLATPAK_EDITORS, *FLATPAK_PLAYERS)
    }
    for raw_dir in DESKTOP_DIRS:
        folder = os.path.expanduser(raw_dir)
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            continue
        for filename in entries:
            if not filename.endswith(".desktop"):
                continue
            path = os.path.join(folder, filename)
            values = _read_desktop_entry(path)
            if not values:
                continue
            if values.get("NoDisplay", "false").casefold() == "true":
                continue
            if values.get("Hidden", "false").casefold() == "true":
                continue
            name = values.get("Name[fr]") or values.get("Name") or filename[:-8]
            exec_line = values.get("Exec", "")
            categories = values.get("Categories", "")
            if not exec_line or not _looks_like_player(
                name, exec_line, categories, filename
            ):
                continue
            # Les applications Flatpak déjà connues sont ajoutées plus haut :
            # évite « Showtime » et « Lecteur vidéo » en double.
            if any(app_id in exec_line for app_id in known_flatpak_ids):
                continue
            try:
                arguments = shlex.split(exec_line)
            except ValueError:
                continue
            arguments = [arg for arg in arguments if "%" not in arg]
            if not arguments:
                continue
            base = arguments[0]
            if base == "env":
                index = 1
                while index < len(arguments) and "=" in arguments[index]:
                    index += 1
                if index >= len(arguments):
                    continue
                base = arguments[index]
            if base.startswith("/"):
                if not os.path.exists(base):
                    continue
            elif not shutil.which(base):
                continue
            players.append((name, shlex.join(arguments)))
    return players


def _appimage_editors() -> list[tuple[str, str]]:
    editors: list[tuple[str, str]] = []
    for raw_dir in APPIMAGE_DIRS:
        folder = os.path.expanduser(raw_dir)
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            continue
        for filename in entries:
            if not filename.endswith(".AppImage"):
                continue
            lower = filename.casefold()
            keyword = next((k for k in EDITOR_KEYWORDS if k in lower), None)
            if keyword is None:
                continue
            path = os.path.join(folder, filename)
            if not os.access(path, os.X_OK):
                continue
            name = filename.removesuffix(".AppImage").replace("_", " ").strip()
            editors.append((name, shlex.quote(path)))
    return editors


def detect() -> list[tuple[str, str]]:
    """Renvoie les lecteurs et éditeurs détectés (nom, commande)."""
    found: list[tuple[str, str]] = []
    names: set[str] = set()

    # Les lecteurs passent en premier : c'est le choix par défaut le plus
    # utile après un enregistrement.
    for name, command in KNOWN_PLAYERS:
        executable = _which(command)
        if executable:
            _add(found, names, name, executable)

    for name, command in KNOWN_EDITORS:
        if name.casefold() in names:
            continue
        executable = _which(command)
        if executable:
            _add(found, names, name, executable)

    flatpak_apps = _flatpak_apps()
    for name, app_id in FLATPAK_PLAYERS:
        if app_id in flatpak_apps:
            _add(found, names, name, f"flatpak run {app_id}")

    for name, app_id in FLATPAK_EDITORS:
        if app_id in flatpak_apps:
            _add(found, names, name, f"flatpak run {app_id}")

    snap_apps = _snap_apps()
    for name, snap_name in SNAP_PLAYERS:
        if snap_name in snap_apps:
            _add(found, names, name, f"snap run {snap_name}")

    for name, snap_name in SNAP_EDITORS:
        if snap_name in snap_apps:
            _add(found, names, name, f"snap run {snap_name}")

    for name, command in _desktop_players():
        _add(found, names, name, command)

    for name, command in _desktop_editors():
        _add(found, names, name, command)

    for name, command in COMMON_EDITOR_PATHS:
        path = os.path.expanduser(command)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            _add(found, names, name, shlex.quote(path))

    for name, command in _appimage_editors():
        _add(found, names, name, command)

    return found


# Lecteurs de musique et navigateurs : ils savent lire une vidéo mais ne
# sont pas des lecteurs/éditeurs vidéo au sens de Capturo.
NON_VIDEO_APPS = (
    "firefox",
    "chromium",
    "chrome",
    "brave",
    "vivaldi",
    "opera",
    "epiphany",
    "microsoft-edge",
    "thunderbird",
    "libreoffice",
    "spotify",
    "rhythmbox",
    "amberol",
    "lollypop",
)


def _is_video_app(key: str, name: str, exec_line: str) -> bool:
    haystack = f"{key} {name} {exec_line}".casefold()
    return not any(word in haystack for word in NON_VIDEO_APPS)


VIDEO_CONTENT_TYPES = (
    "video/mp4",
    "video/x-matroska",
    "video/webm",
    "video/quicktime",
    "video/mpeg",
    "video/x-msvideo",
    "video/x-ms-wmv",
    "video/ogg",
    "video/x-flv",
    "video/3gpp",
    "video/x-m4v",
)

_APP_NAMES: dict[str, str] = {}


def _launcher() -> str | None:
    """Commande capable de lancer un fichier .desktop (gtk-launch ou gio)."""
    if shutil.which("gtk-launch"):
        return "gtk-launch"
    if shutil.which("gio"):
        return "gio launch"
    return None


def _desktop_id_command(app_id: str, exec_line: str) -> str:
    launcher = _launcher()
    if launcher:
        return f"{launcher} {app_id}"
    try:
        arguments = shlex.split(exec_line)
    except ValueError:
        return ""
    arguments = [argument for argument in arguments if "%" not in argument]
    return " ".join(shlex.quote(argument) for argument in arguments)


def video_apps() -> list[dict[str, str]]:
    """Toutes les applications installées qui savent ouvrir une vidéo.

    Renvoie des dictionnaires ``name``, ``command`` et ``icon``. La commande
    passe par le lanceur du bureau (``gtk-launch``) afin de respecter les
    arguments, l'activation D-Bus et les applications Flatpak/Snap.
    """
    apps: dict[str, dict[str, str]] = {}

    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError):
        Gio = None

    if Gio is not None:
        for content_type in VIDEO_CONTENT_TYPES:
            try:
                infos = Gio.AppInfo.get_all_for_type(content_type)
            except Exception:  # noqa: BLE001 - GIO indisponible
                infos = []
            for info in infos:
                app_id = info.get_id() or ""
                if not app_id:
                    continue
                key = app_id.removesuffix(".desktop")
                if key in apps:
                    continue
                icon = info.get_icon()
                command = _desktop_id_command(key, info.get_commandline() or "")
                if not command:
                    continue
                name = info.get_display_name() or info.get_name() or key
                if not _is_video_app(key, name, info.get_commandline() or ""):
                    continue
                apps[key] = {
                    "name": name,
                    "command": command,
                    "icon": (
                        icon.to_string() if icon else "video-x-generic-symbolic"
                    ),
                }

    # Certains éditeurs déclarent leurs types vidéo dans le lanceur sans que
    # GIO les associe au type MIME principal : on lit aussi les .desktop.
    for raw_dir in DESKTOP_DIRS:
        folder = os.path.expanduser(raw_dir)
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            continue
        for filename in entries:
            if not filename.endswith(".desktop"):
                continue
            key = filename[:-8]
            if key in apps:
                continue
            values = _read_desktop_entry(os.path.join(folder, filename))
            if not values:
                continue
            if values.get("NoDisplay", "false").casefold() == "true":
                continue
            if values.get("Hidden", "false").casefold() == "true":
                continue
            if "video/" not in values.get("MimeType", "").casefold():
                continue
            command = _desktop_id_command(key, values.get("Exec", ""))
            if not command:
                continue
            name = values.get("Name[fr]") or values.get("Name") or key
            if not _is_video_app(key, name, values.get("Exec", "")):
                continue
            apps[key] = {
                "name": name,
                "command": command,
                "icon": values.get("Icon") or "video-x-generic-symbolic",
            }

    for app in apps.values():
        _APP_NAMES[app["command"]] = app["name"]
    return sorted(apps.values(), key=lambda app: app["name"].casefold())


def launch(command: str, path: str) -> None:
    """Ouvre le fichier dans le lecteur/éditeur. Lève OSError/ValueError."""
    arguments = shlex.split(command)
    if not arguments:
        raise ValueError("Commande de lecteur ou d'éditeur vide.")
    target = path
    if arguments[0] == "gtk-launch":
        # gtk-launch attend des URI, pas des chemins bruts.
        from urllib.parse import quote

        target = "file://" + quote(os.path.abspath(path))
    subprocess.Popen(
        [*arguments, target],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def pretty_command(command: str) -> str:
    """Nom lisible pour une commande personnalisée."""
    if not command:
        return "Aucun"
    if command in _APP_NAMES:
        return _APP_NAMES[command]
    if command.startswith("flatpak run "):
        app_id = command.removeprefix("flatpak run ").strip()
        for name, candidate in (*FLATPAK_EDITORS, *FLATPAK_PLAYERS):
            if candidate == app_id:
                return name
        return app_id
    if command.startswith(("gtk-launch ", "gio launch ")):
        app_id = shlex.split(command)[-1].removesuffix(".desktop")
        return app_id.rsplit(".", 1)[-1]
    if command.startswith("snap run "):
        return command.removeprefix("snap run ").strip().capitalize()
    try:
        return shlex.split(command)[0].rsplit("/", 1)[-1]
    except ValueError:
        return command
