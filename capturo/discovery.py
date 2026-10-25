"""Découverte des Capturo Webcam sur le réseau local et en USB.

L'application Android répond aux diffusions UDP « CAPTURO_DISCOVER » sur le
port 8081. Capturo envoie la requête sur l'adresse de diffusion de chaque
interface (Wi-Fi, Ethernet, partage de connexion USB) et utilise l'adresse
source de la réponse, qui est joignable depuis l'ordinateur.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import socket
import struct
import subprocess
import time
from urllib.parse import quote

DISCOVERY_PORT = 8081
VIDEO_PORT = 8080
REQUEST = b"CAPTURO_DISCOVER"
RESPONSE = b"CAPTURO_WEBCAM"

SIOCGIFADDR = 0x8915
SIOCGIFBRDADDR = 0x8919

# Plages habituelles du partage de connexion USB d'Android (RNDIS/NCM).
USB_PREFIXES = ("192.168.42.", "192.168.137.", "192.168.49.")

# Dernier diagnostic USB, affiché par l'interface quand rien n'est trouvé.
_last_usb_hint = ""


def usb_hint() -> str:
    """Explication du dernier échec de détection USB (ou chaîne vide)."""
    return _last_usb_hint


def _ioctl_address(sock: socket.socket, name: str, request: int) -> str | None:
    ifreq = struct.pack("256s", name.encode("utf-8")[:15])
    try:
        result = fcntl.ioctl(sock.fileno(), request, ifreq)
    except OSError:
        return None
    address = socket.inet_ntoa(result[20:24])
    if address == "0.0.0.0":
        return None
    return address


def broadcast_addresses() -> set[str]:
    """Adresses de diffusion de toutes les interfaces actives."""
    targets: set[str] = {"255.255.255.255"}
    try:
        interfaces = socket.if_nameindex()
    except OSError:
        return targets

    for _index, name in interfaces:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            broadcast = _ioctl_address(sock, name, SIOCGIFBRDADDR)
            if broadcast:
                targets.add(broadcast)
            address = _ioctl_address(sock, name, SIOCGIFADDR)
            if address:
                # Filet de sécurité pour les interfaces sans diffusion
                # explicite (certains pilotes USB) : on suppose un /24.
                parts = address.split(".")
                targets.add(".".join(parts[:3] + ["255"]))
        finally:
            sock.close()
    return targets


def _is_usb(url: str) -> bool:
    host = url.split("//", 1)[-1].split(":", 1)[0]
    return host.startswith(USB_PREFIXES) or host in (
        "127.0.0.1",
        "localhost",
        "::1",
    )


def probe_localhost() -> str | None:
    """Détecte un port 8080 transféré par USB avec ``adb forward``.

    ``adb forward tcp:8080 tcp:8080`` expose le serveur du téléphone sur
    localhost ; on vérifie que la page d'accueil est bien celle de Capturo
    Webcam avant de l'utiliser.
    """
    try:
        with socket.create_connection(("127.0.0.1", VIDEO_PORT), timeout=0.5) as sock:
            sock.settimeout(0.35)
            sock.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
            chunks: list[bytes] = []
            size = 0
            while size < 16384:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if b"Capturo Webcam" in chunk:
                    break
            data = b"".join(chunks)
    except OSError:
        return None
    if b"Capturo Webcam" not in data:
        return None
    return f"http://127.0.0.1:{VIDEO_PORT}/video"


def _adb_candidates():
    """Emplacements habituels d'adb, sans dépendre du PATH du bureau."""
    override = os.environ.get("CAPTURO_ADB")
    if override:
        yield override
    found = shutil.which("adb")
    if found:
        yield found
    roots = (
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        os.path.join(os.path.expanduser("~"), "Android", "Sdk"),
        os.path.join(os.path.expanduser("~"), "android-sdk"),
        "/usr/lib/android-sdk",
        "/opt/android-sdk",
        "/opt/android-sdk-linux",
        "/usr/local/android-sdk",
    )
    for root in roots:
        if root:
            yield os.path.join(root, "platform-tools", "adb")
    yield "/usr/bin/adb"
    yield "/snap/bin/adb"


def _adb_path() -> str | None:
    for candidate in _adb_candidates():
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _run_adb(adb: str, *arguments: str):
    try:
        return subprocess.run(
            [adb, *arguments],
            capture_output=True,
            text=True,
            timeout=2.5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _adb_devices(adb: str) -> list[str]:
    result = _run_adb(adb, "devices")
    if result is None:
        return []
    serials: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def _adb_states(adb: str) -> dict[str, str]:
    """États renvoyés par ``adb devices`` (device, unauthorized, offline…)."""
    result = _run_adb(adb, "devices")
    if result is None:
        return {}
    states: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.startswith(("List", "*")):
            states[parts[0]] = parts[1]
    return states


def _forward_present(adb: str) -> bool:
    result = _run_adb(adb, "forward", "--list")
    return result is not None and "tcp:8080" in result.stdout


def probe_usb() -> str | None:
    """Détecte Capturo Webcam par câble USB.

    Deux cas sont couverts :
    * un ``adb forward`` existe déjà (ou adb est installé et le téléphone est
      autorisé en débogage USB) : Capturo crée le transfert automatiquement ;
    * le téléphone partage sa connexion USB : la découverte UDP classique
      trouve alors son adresse 192.168.42.x.
    """
    global _last_usb_hint
    _last_usb_hint = ""

    local = probe_localhost()
    if local:
        return local

    adb = _adb_path()
    if adb is None:
        _last_usb_hint = (
            "adb n'est pas installé : installe android-tools-adb pour "
            "utiliser le câble USB."
        )
        return None

    # Un ancien transfert peut pointer vers une autre application ou un
    # téléphone débranché : on le remplace avant de tester les appareils.
    if _forward_present(adb):
        _run_adb(adb, "forward", "--remove", "tcp:8080")

    states = _adb_states(adb)
    if not states:
        _last_usb_hint = (
            "Aucun téléphone vu par adb : branche le câble, active le "
            "débogage USB et accepte la demande d'autorisation."
        )
        return None

    unauthorized = [s for s, state in states.items() if state == "unauthorized"]
    if unauthorized:
        _last_usb_hint = (
            "Téléphone détecté mais non autorisé : accepte la demande "
            "« Autoriser le débogage USB » sur l'écran du téléphone."
        )
        return None

    offline = [s for s, state in states.items() if state == "offline"]
    if offline:
        _last_usb_hint = (
            "Téléphone détecté mais hors ligne : débranche puis rebranche "
            "le câble USB."
        )
        return None

    for serial in _adb_devices(adb):
        result = _run_adb(adb, "-s", serial, "forward", "tcp:8080", "tcp:8080")
        if result is None or result.returncode != 0:
            _last_usb_hint = (
                "adb n'a pas pu créer le transfert USB. Vérifie que le "
                "débogage USB est autorisé pour cet ordinateur."
            )
            continue
        local = probe_localhost()
        if local:
            return local

    _last_usb_hint = (
        "Le téléphone est connecté mais Capturo Webcam ne répond pas : "
        "démarre l'application et sa diffusion, puis réessaie."
    )
    return None


def usb_with_token(local: str, tokens: set[str]) -> str:
    """Ajoute à l'URL USB le jeton unique découvert en Wi-Fi.

    adb ne transmet pas le jeton du téléphone. Quand la découverte Wi-Fi a
    annoncé exactement un jeton, on le réutilise pour l'adresse USB ; sinon
    l'URL reste inchangée et Capturo utilisera le jeton mémorisé.
    """
    if local and "token=" not in local and len(tokens) == 1:
        return f"{local}?token={quote(next(iter(tokens)))}"
    return local


def discover_cameras(timeout: float = 1.5) -> list[str]:
    """Cherche les Capturo Webcam actives.

    Renvoie une liste d'URL vidéo (``http://adresse:8080/video``) triées en
    donnant la priorité aux adresses de partage USB.
    """
    targets = broadcast_addresses()
    found: dict[str, str] = {}
    tokens: set[str] = set()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", 0))
    sock.settimeout(0.25)
    deadline = time.monotonic() + max(0.2, timeout)

    try:
        while time.monotonic() < deadline:
            for target in targets:
                try:
                    sock.sendto(REQUEST, (target, DISCOVERY_PORT))
                except OSError:
                    pass

            end = min(deadline, time.monotonic() + 0.35)
            while time.monotonic() < end:
                try:
                    data, address = sock.recvfrom(512)
                except socket.timeout:
                    break
                except OSError:
                    break
                if not data.startswith(RESPONSE):
                    continue
                host = address[0]
                port = VIDEO_PORT
                parts = data.split()
                if len(parts) > 1 and parts[1].isdigit():
                    port = int(parts[1])
                token = ""
                if len(parts) > 2:
                    token = parts[2].decode("utf-8", "ignore").strip()
                if token:
                    tokens.add(token)
                suffix = f"?token={quote(token)}" if token else ""
                found[host] = f"http://{host}:{port}/video{suffix}"
    finally:
        sock.close()

    local = probe_usb()
    if local:
        found["127.0.0.1"] = usb_with_token(local, tokens)

    return sorted(
        found.values(), key=lambda url: (0 if _is_usb(url) else 1, url)
    )
