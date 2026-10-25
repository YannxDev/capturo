"""Construction du pipeline GStreamer et pilotage de l'enregistrement."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable

import gi

gi.require_version("Gst", "1.0")

from gi.repository import GLib, Gst  # noqa: E402

from .diagnostics import log  # noqa: E402

FORMAT_IDS = ("mp4", "mkv", "webm")
FORMAT_LABELS = (
    "MP4 · H.264 (compatible partout)",
    "MKV · H.265 (fichiers plus petits)",
    "WebM · VP9 (format libre)",
)
FORMAT_EXTENSIONS = ("mp4", "mkv", "webm")

QUALITY_LABELS = ("Basse", "Moyenne", "Haute", "Très haute")
QUALITY_FACTORS = (0.06, 0.12, 0.20, 0.30)

FPS_CHOICES = (24, 30, 60)
RESOLUTIONS = ("Native", "4K", "1440p", "1080p", "720p", "480p")

# Ancienne liste (1.2.x) : Native, 1080p, 720p, 480p
_OLD_RESOLUTION_INDEXES = {0: 0, 1: 3, 2: 4, 3: 5}

CAMERA_POSITIONS = ("Bas droite", "Bas gauche", "Haut droite", "Haut gauche")
CAMERA_SIZES = ("Petite", "Moyenne", "Grande")
CAMERA_SIZE_FRACTIONS = (0.16, 0.22, 0.30)
CAMERA_SHAPES = ("Rectangle", "Carré", "Rond")

COUNTDOWN_CHOICES = (0, 3, 5, 10)

ENCODER_LABELS = (
    "Automatique (matériel si disponible)",
    "Logiciel (x264 / x265 / VP9)",
    "Matériel (VA-API / NVENC)",
)
ENCODER_AUTO = 0
ENCODER_SOFTWARE = 1
ENCODER_HARDWARE = 2

_HARDWARE_ENCODERS = {
    "mp4": ("vah264enc", "nvh264enc"),
    "mkv": ("vah265enc", "nvh265enc"),
    "webm": ("vaav1enc", "nvav1enc"),
}


def _quote(value: str) -> str:
    """Échappe un chemin ou un nom de périphérique pour gst_parse_launch."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def _resolution_height(resolution: str) -> int:
    if resolution in ("4K", "2160p"):
        return 2160
    return int(resolution.rstrip("p"))


def list_cameras() -> list[tuple[str, str]]:
    """Liste les caméras disponibles sous forme (nom, source GStreamer)."""
    try:
        monitor = Gst.DeviceMonitor.new()
        monitor.add_filter("Video/Source", None)
        devices = monitor.get_devices() or []
    except Exception:  # noqa: BLE001 - périphériques absents / backend indisponible
        return []

    cameras: list[tuple[str, str]] = []
    for device in devices:
        props = device.get_properties()
        if props is None:
            continue
        path = props.get_string("api.v4l2.path") or props.get_string(
            "device.path"
        )
        if path:
            source = f"v4l2src device={_quote(path)}"
        else:
            serial = props.get_string("object.serial")
            if not serial:
                continue
            source = f"pipewiresrc target-object={_quote(serial)}"
        name = device.get_display_name() or "Caméra"
        cameras.append((name, source))
    return cameras


def _is_virtual_audio_source(name: str) -> bool:
    """Vrai pour les moniteurs de sortie et les sources factices.

    Ces entrées ne sont pas des micros : les proposer dans la liste des
    microphones n'apporte que de la confusion. Le son du système passe par
    ``@DEFAULT_MONITOR@``, pas par ce choix.
    """
    lowered = name.casefold()
    return (
        lowered.startswith("monitor of")
        or lowered.startswith("monitor ")
        or "dummy source" in lowered
        or "restricted clients" in lowered
    )


def list_audio_inputs() -> list[tuple[str, str]]:
    """Liste les entrées audio sous forme (nom, source GStreamer)."""
    try:
        monitor = Gst.DeviceMonitor.new()
        monitor.add_filter("Audio/Source", None)
        devices = monitor.get_devices() or []
    except Exception:  # noqa: BLE001 - backend audio indisponible
        return []

    inputs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for device in devices:
        props = device.get_properties()
        if props is None:
            continue
        name = device.get_display_name() or "Microphone"
        if _is_virtual_audio_source(name):
            continue
        pulse = props.get_string("api.pulseaudio.source")
        serial = props.get_string("object.serial")
        if pulse:
            source = f"pulsesrc device={_quote(pulse)}"
        elif serial:
            source = f"pipewiresrc target-object={_quote(serial)}"
        else:
            continue
        if source in seen:
            continue
        seen.add(source)
        inputs.append((name, source))
    return inputs


def network_camera_source(url: str) -> str:
    """Source GStreamer pour une caméra réseau (MJPEG HTTP ou RTSP)."""
    url = url.strip()
    lowered = url.lower()
    if not lowered.startswith(("http://", "https://", "rtsp://", "rtsps://")):
        raise ValueError(
            "L'adresse doit commencer par http://, https:// ou rtsp://."
        )
    if lowered.startswith(("rtsp://", "rtsps://")):
        return f"rtspsrc location={_quote(url)} latency=0 ! decodebin"
    return (
        f"souphttpsrc location={_quote(url)} is-live=true do-timestamp=true "
        "! multipartdemux ! jpegdec"
    )


def network_audio_source(url: str) -> str:
    """Source audio brute (PCM 48 kHz mono 16 bits) de Capturo Webcam."""
    return (
        f"souphttpsrc location={_quote(url)} is-live=true do-timestamp=true "
        "! queue ! rawaudioparse format=pcm pcm-format=s16le "
        "sample-rate=48000 num-channels=1"
    )


def _target_size(source_size, resolution: str):
    """Calcule la taille de sortie, ou None pour garder la taille native."""
    if not source_size or resolution == "Native":
        return None
    width, height = source_size
    max_height = _resolution_height(resolution)
    if height <= max_height:
        return None
    new_width = max(2, int(round(width * max_height / height / 2)) * 2)
    new_height = max_height - (max_height % 2)
    return new_width, new_height


def _camera_box(
    width: int,
    height: int,
    size_index: int,
    position_index: int,
    shape_index: int = 0,
):
    """Taille et position de la webcam dans l'image finale."""
    camera_height = _even(height * CAMERA_SIZE_FRACTIONS[size_index])
    if shape_index in (1, 2):
        camera_width = camera_height
        limit = min(_even(width * 0.6), _even(height * 0.6))
        if camera_width > limit:
            camera_width = limit
            camera_height = camera_width
    else:
        camera_width = _even(camera_height * 16 / 9)
        camera_width = min(camera_width, _even(width * 0.6))
    margin = _even(min(width, height) * 0.02)

    positions = {
        0: (width - camera_width - margin, height - camera_height - margin),
        1: (margin, height - camera_height - margin),
        2: (width - camera_width - margin, margin),
        3: (margin, margin),
    }
    return camera_width, camera_height, positions[position_index]


def _circle_mask_path(width: int, height: int) -> str:
    """Génère (et met en cache) un masque circulaire blanc sur noir."""
    cache = os.path.join(GLib.get_user_cache_dir(), "capturo")
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, f"masque-rond-{width}x{height}.png")
    if os.path.exists(path):
        return path
    try:
        import cairo
    except ImportError as exc:  # pragma: no cover - dépendance système
        raise RuntimeError(
            "Le paquet python3-cairo est nécessaire pour le cadrage rond."
        ) from exc

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    context = cairo.Context(surface)
    context.set_source_rgb(0, 0, 0)
    context.paint()
    context.set_source_rgb(1, 1, 1)
    radius = min(width, height) / 2.0 - 1.0
    context.arc(width / 2.0, height / 2.0, radius, 0, 2 * math.pi)
    context.fill()
    surface.write_to_png(path)
    return path


def hardware_encoder(format_id: str) -> str | None:
    """Nom de l'encodeur matériel disponible, ou None."""
    for name in _HARDWARE_ENCODERS.get(format_id, ()):
        if Gst.ElementFactory.find(name) is not None:
            return name
    return None


def _hardware_encoder_chain(name: str, bitrate_kbps: int, fps: int) -> str:
    keyframe = max(1, fps * 2)
    if name in ("vah264enc", "vah265enc"):
        parser = "h264parse config-interval=-1" if name == "vah264enc" else "h265parse"
        # vapostproc téléverse l'image en mémoire VA-API quand il est là.
        prefix = "vapostproc ! " if Gst.ElementFactory.find("vapostproc") else ""
        return (
            f"{prefix}{name} bitrate={bitrate_kbps} key-int-max={keyframe} "
            f"rate-control=cbr ! {parser}"
        )
    if name in ("nvh264enc", "nvh265enc"):
        parser = "h264parse config-interval=-1" if name == "nvh264enc" else "h265parse"
        return (
            f"{name} bitrate={bitrate_kbps} gop-size={keyframe} rc-mode=cbr "
            f"! {parser}"
        )
    if name in ("vaav1enc", "nvav1enc"):
        return f"{name} bitrate={bitrate_kbps} key-int-max={keyframe} ! av1parse"
    return ""


def _video_encoder(
    format_id: str,
    bitrate_kbps: int,
    fps: int,
    encoder_mode: int = ENCODER_AUTO,
) -> str:
    hardware = hardware_encoder(format_id) if encoder_mode != ENCODER_SOFTWARE else None
    if hardware:
        chain = _hardware_encoder_chain(hardware, bitrate_kbps, fps)
        if chain:
            log(f"encodeur matériel : {hardware}")
            return chain
        log(f"encodeur matériel {hardware} sans chaîne connue, repli logiciel")
    if encoder_mode == ENCODER_HARDWARE:
        log("aucun encodeur matériel disponible, repli logiciel")
    if format_id == "mp4":
        return (
            f"x264enc speed-preset=ultrafast tune=zerolatency "
            f"bitrate={bitrate_kbps} key-int-max={fps * 2} ! "
            "h264parse config-interval=-1"
        )
    if format_id == "mkv":
        return (
            f"x265enc speed-preset=ultrafast tune=zerolatency "
            f"bitrate={bitrate_kbps} key-int-max={fps * 2} ! h265parse"
        )
    return (
        f"vp9enc target-bitrate={bitrate_kbps * 1000} cpu-used=8 "
        f"deadline=1 lag-in-frames=0 keyframe-max-dist={fps * 2}"
    )


def _muxer(format_id: str, fragmented: bool = False) -> str:
    if format_id == "mp4":
        if fragmented:
            # Un MP4 fragmenté reste lisible même si l'application est
            # interrompue avant l'écriture de l'index final.
            return (
                "mp4mux name=mux fragment-duration=1000 "
                "streamable=true"
            )
        return "mp4mux name=mux faststart=true"
    if format_id == "mkv":
        return "matroskamux name=mux"
    return "webmmux name=mux"


def estimate_bitrate(
    width: int,
    height: int,
    fps: int,
    quality_index: int,
    video_bitrate: int = 0,
) -> int:
    """Débit vidéo estimé en kb/s, pour l'affichage et l'espace disque."""
    if video_bitrate > 0:
        return max(500, min(100000, int(video_bitrate)))
    factor = QUALITY_FACTORS[max(0, min(len(QUALITY_FACTORS) - 1, quality_index))]
    return max(800, min(60000, int(width * height * fps * factor / 1000)))


def _audio_suffix(
    format_id: str,
    mic: bool,
    system_audio: bool,
    phone_audio_url: str | None = None,
    *,
    stream_url: str | None = None,
    output_path: str | None = None,
    mic_source: str = "pulsesrc",
    mic_volume: int = 100,
    system_volume: int = 100,
    audio_delay_ms: int = 0,
    audio_level: bool = True,
) -> str:
    if not (mic or system_audio or phone_audio_url):
        return ""

    if stream_url:
        # Twitch/YouTube exigent de l'AAC.
        encoder = "avenc_aac bitrate=128000 ! aacparse"
    elif format_id == "webm":
        encoder = "opusenc bitrate=128000"
    else:
        encoder = "avenc_aac bitrate=192000 ! aacparse"

    def chain(source: str, volume: int = 100, delay_ms: int = 0) -> str:
        parts = [source]
        if volume != 100:
            parts.append(f"volume volume={max(0, volume) / 100:.3f}")
        parts.extend(
            (
                "audioconvert",
                "audioresample",
                "audio/x-raw,rate=48000,channels=2",
            )
        )
        if delay_ms:
            # Décaler les horodatages revient à retarder cette piste par
            # rapport à la vidéo, sans bloquer le pipeline.
            parts.append(f"identity ts-offset={int(delay_ms * 1_000_000)}")
        return " ! ".join(parts)

    sources: list[str] = []
    if mic:
        sources.append(
            chain(mic_source or "pulsesrc", mic_volume, audio_delay_ms)
        )
    if system_audio:
        sources.append(
            chain(
                "pulsesrc device=@DEFAULT_MONITOR@",
                system_volume,
                0,
            )
        )
    if phone_audio_url:
        sources.append(
            f"{network_audio_source(phone_audio_url)} ! audioconvert ! "
            "audioresample ! audio/x-raw,rate=48000,channels=2"
        )

    meter = ""
    if audio_level and Gst.ElementFactory.find("level") is not None:
        meter = (
            " ! level name=audio_level post-messages=true "
            "interval=100000000 peak-falloff=20"
        )

    if stream_url:
        if output_path:
            tail = (
                " ! tee name=audiotee "
                "audiotee. ! queue ! mux.audio_0 "
                "audiotee. ! queue ! streammux.audio"
            )
        else:
            tail = " ! queue ! streammux.audio"
    else:
        tail = " ! queue ! mux.audio_0"

    if len(sources) == 1:
        return f"{sources[0]}{meter} ! {encoder}{tail}"

    inputs = " ".join(f"{source} ! mix." for source in sources)
    return f"audiomixer name=mix{meter} ! {encoder}{tail} {inputs}"


def _preview_branch() -> str:
    """Branche d'aperçu 640×360 RGB pour la fenêtre de Capturo."""
    return (
        "preview_tee. ! queue ! videoconvert ! videoscale ! "
        "video/x-raw,format=RGB,width=640,height=360 ! "
        "appsink name=preview_sink max-buffers=1 drop=true "
        "emit-signals=true sync=false "
    )


def build_pipeline(
    *,
    fd: int,
    node_id: int,
    source_size,
    output_path: str | None,
    format_id: str,
    quality_index: int,
    fps: int,
    resolution: str,
    mic: bool,
    system_audio: bool,
    camera_source: str | None = None,
    camera_position: int = 0,
    camera_size: int = 1,
    camera_mirror: bool = False,
    camera_shape: int = 0,
    phone_audio_url: str | None = None,
    stream_url: str | None = None,
    stream_bitrate: int = 4500,
    video_bitrate: int = 0,
    encoder_mode: int = ENCODER_AUTO,
    fragmented_mp4: bool = False,
    preview: bool = True,
    mic_source: str = "pulsesrc",
    mic_volume: int = 100,
    system_volume: int = 100,
    audio_delay_ms: int = 0,
    audio_level: bool = True,
) -> str:
    """Construit la description gst_parse_launch complète."""
    if output_path is None and not stream_url:
        raise ValueError("Aucune sortie : fichier ou streaming requis.")
    target = _target_size(source_size, resolution)
    if target:
        width, height = target
    elif source_size:
        width, height = source_size
    else:
        width, height = 1920, 1080
    width, height = _even(width), _even(height)

    bitrate_kbps = estimate_bitrate(
        width, height, fps, quality_index, video_bitrate
    )
    if stream_url:
        # Le streaming RTMP impose H.264/AAC ; le fichier local, s'il y en a
        # un, reçoit le même flux pour ne pas encoder deux fois.
        format_id = "mp4"
        bitrate_kbps = max(500, min(20000, int(stream_bitrate)))
    encoder = _video_encoder(format_id, bitrate_kbps, fps, encoder_mode)
    muxer = _muxer(format_id, fragmented_mp4) if output_path else ""
    if stream_url and output_path:
        output_tail = (
            f" ! tee name=outtee "
            f"outtee. ! queue ! {muxer} ! "
            f"filesink location={_quote(output_path)} "
            f"outtee. ! queue ! flvmux name=streammux streamable=true ! "
            f"rtmpsink location={_quote(stream_url)}"
        )
    elif stream_url:
        output_tail = (
            " ! queue ! flvmux name=streammux streamable=true ! "
            f"rtmpsink location={_quote(stream_url)}"
        )
    else:
        output_tail = (
            f" ! queue ! {muxer} ! filesink location={_quote(output_path)}"
        )
    screen_caps = (
        f"video/x-raw,format=I420,width={width},height={height}"
    )

    if camera_source:
        cam_w, cam_h, (cam_x, cam_y) = _camera_box(
            width, height, camera_size, camera_position, camera_shape
        )
        flip = "videoflip method=horizontal-flip ! " if camera_mirror else ""
        ratio = "1/1" if camera_shape in (1, 2) else "16/9"
        camera_branch = (
            f"{camera_source} ! queue ! videoconvert ! {flip}"
            f"aspectratiocrop aspect-ratio={ratio} ! videoscale ! "
            f"video/x-raw,format=I420,width={cam_w},height={cam_h},"
            # Le masque GRAY8 est toujours négocié en plage complète
            # (0–255). La caméra doit donc l'être aussi, sinon alphacombine
            # refuse le pipeline (« Color range mismatch »). La notation
            # numérique est obligatoire : « 1:bt709:bt709:bt709 » n'est pas
            # reconnue par GstVideoInfo.
            f"colorimetry=1:2:1:1"
        )
        if camera_shape == 2:
            mask = _circle_mask_path(cam_w, cam_h)
            camera_branch += (
                f" ! videorate ! "
                f"video/x-raw,framerate={fps}/1,colorimetry=1:2:1:1 ! "
                # alphacombine produit un format A420 (YUV + alpha). Sans
                # forcer ce format, videoconvert/le compositor choisissent
                # I420 et les coins du masque redeviennent opaques (carré).
                f"alphacombine name=cam_alpha ! "
                f"video/x-raw,format=A420 ! comp.sink_1 "
                # Ne pas imposer colorimetry=bt709 ici : GRAY8 serait
                # converti en 16–235 et les coins du masque resteraient
                # légèrement opaques (carré noir à ~6 %).
                f"filesrc location={_quote(mask)} ! pngdec ! videoconvert ! "
                f"video/x-raw,format=GRAY8,width={cam_w},height={cam_h} ! "
                f"imagefreeze ! "
                f"video/x-raw,framerate={fps}/1 ! cam_alpha.alpha"
            )
        else:
            camera_branch += " ! comp.sink_1"

        if preview:
            video = (
                f"compositor name=comp background=black "
                f"sink_0::xpos=0 sink_0::ypos=0 "
                f"sink_0::width={width} sink_0::height={height} "
                f"sink_1::xpos={cam_x} sink_1::ypos={cam_y} "
                f"sink_1::width={cam_w} sink_1::height={cam_h} ! "
                f"videorate ! videoconvert ! tee name=preview_tee "
                f"{_preview_branch()}"
                f"preview_tee. ! queue ! videoconvert ! "
                f"video/x-raw,format=I420,framerate={fps}/1,"
                f"width={width},height={height} ! "
                f"{encoder}{output_tail} "
                f"pipewiresrc fd={fd} path={node_id} do-timestamp=true ! "
                f"queue ! videoconvert ! videoscale ! {screen_caps} ! "
                f"comp.sink_0 "
                f"{camera_branch}"
            )
        else:
            video = (
                f"compositor name=comp background=black "
                f"sink_0::xpos=0 sink_0::ypos=0 "
                f"sink_0::width={width} sink_0::height={height} "
                f"sink_1::xpos={cam_x} sink_1::ypos={cam_y} "
                f"sink_1::width={cam_w} sink_1::height={cam_h} ! "
                f"videorate ! videoconvert ! "
                f"video/x-raw,format=I420,framerate={fps}/1,"
                f"width={width},height={height} ! "
                f"{encoder}{output_tail} "
                f"pipewiresrc fd={fd} path={node_id} do-timestamp=true ! "
                f"queue ! videoconvert ! videoscale ! {screen_caps} ! "
                f"comp.sink_0 "
                f"{camera_branch}"
            )
    else:
        if preview:
            video = (
                f"pipewiresrc fd={fd} path={node_id} do-timestamp=true ! "
                "queue ! videoconvert ! videorate ! videoscale ! "
                f"video/x-raw,format=I420,framerate={fps}/1,"
                f"width={width},height={height} ! "
                f"tee name=preview_tee "
                f"{_preview_branch()}"
                f"preview_tee. ! queue ! {encoder}{output_tail}"
            )
        else:
            video = (
                f"pipewiresrc fd={fd} path={node_id} do-timestamp=true ! "
                "queue ! videoconvert ! videorate ! videoscale ! "
                f"video/x-raw,format=I420,framerate={fps}/1,"
                f"width={width},height={height} ! "
                f"{encoder}{output_tail}"
            )

    audio = _audio_suffix(
        format_id,
        mic,
        system_audio,
        phone_audio_url,
        stream_url=stream_url,
        output_path=output_path,
        mic_source=mic_source,
        mic_volume=mic_volume,
        system_volume=system_volume,
        audio_delay_ms=audio_delay_ms,
        audio_level=audio_level,
    )
    return f"{video} {audio}".strip()


def verify_file(path: str | None, timeout: float = 8.0) -> tuple[bool, str]:
    """Vérifie qu'un fichier contient bien une piste lisible.

    Utilise ``gst-discoverer-1.0`` quand il est disponible ; sinon se
    contente de contrôler la taille. Ne lève jamais d'exception.
    """
    if not path:
        return False, "Aucun fichier produit."
    try:
        size = os.path.getsize(path)
    except OSError:
        return False, "Fichier introuvable."
    if size == 0:
        return False, "Le fichier est vide."
    discoverer = shutil.which("gst-discoverer-1.0")
    if not discoverer:
        return True, ""
    try:
        result = subprocess.run(
            [discoverer, path],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # La vérification est un confort : on ne bloque pas l'utilisateur.
        return True, ""
    output = f"{result.stdout}\n{result.stderr}"
    if "video #" not in output and "audio #" not in output:
        return False, "Le fichier ne contient aucune piste lisible."
    if result.returncode != 0:
        return False, "Le fichier semble incomplet ou corrompu."
    return True, ""


class Recorder:
    """Lance le pipeline et prévient quand le fichier est finalisé."""

    def __init__(
        self,
        on_finished: Callable[[str | None], None],
        on_level: Callable[[float], None] | None = None,
    ) -> None:
        self._on_finished = on_finished
        self._on_level = on_level
        self._pipeline: Gst.Pipeline | None = None
        self._bus: Gst.Bus | None = None
        self._bus_watch = 0
        self._stop_timeout = 0
        self._teardown_timeout = 0
        self._done = True
        self._stopping = False
        self._finished = False

    def start(self, description: str, setup=None) -> None:
        try:
            pipeline = Gst.parse_launch(description)
        except GLib.Error as exc:
            raise RuntimeError(f"Pipeline invalide : {exc.message}") from exc

        if setup is not None:
            setup(pipeline)

        self._pipeline = pipeline
        self._done = False
        self._stopping = False
        self._finished = False
        self._bus = pipeline.get_bus()
        self._bus.add_signal_watch()
        self._bus_watch = self._bus.connect("message", self._on_message)

        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._discard_started_pipeline()
            raise RuntimeError("Le pipeline n'a pas pu démarrer.")
        log("enregistrement démarré")

    def stop(self) -> None:
        """Demande l'arrêt sans jamais bloquer la boucle GTK.

        L'EOS est envoyé au pipeline ; si le muxeur ne le reçoit pas (source
        réseau bloquée, périphérique débranché…), un délai borné force la
        remise à NULL dans un fil d'arrière-plan.
        """
        if self._pipeline is None or self._done or self._stopping:
            return
        self._stopping = True
        log("arrêt demandé")
        # L'envoi d'EOS est lui aussi déporté : un périphérique bloqué ne
        # doit jamais figer la boucle GTK.
        threading.Thread(
            target=self._send_eos,
            name="capturo-eos",
            daemon=True,
        ).start()
        if not self._stop_timeout:
            # EOS arrive normalement en quelques millisecondes. Ce délai
            # borné évite d'attendre 3 s à chaque arrêt si une source live
            # (pipewiresrc) ne relaie pas l'événement.
            self._stop_timeout = GLib.timeout_add(1000, self._on_stop_timeout)

    def _send_eos(self) -> None:
        pipeline = self._pipeline
        if pipeline is None:
            return
        try:
            pipeline.send_event(Gst.Event.new_eos())
            log("EOS envoyé")
        except Exception as exc:  # noqa: BLE001 - pipeline déjà détruit
            log(f"EOS impossible : {exc}")

    # ----------------------------------------------------------- interne
    def _on_message(self, _bus, message: Gst.Message) -> None:
        if message.type == Gst.MessageType.EOS:
            log("EOS reçu")
            self._begin_cleanup(None)
        elif message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            log(f"erreur du pipeline : {error.message}")
            self._begin_cleanup(error.message)
        elif message.type == Gst.MessageType.ELEMENT:
            self._handle_element_message(message)

    def _handle_element_message(self, message: Gst.Message) -> None:
        if self._on_level is None:
            return
        structure = message.get_structure()
        if structure is None or structure.get_name() != "level":
            return
        try:
            peaks = structure.get_value("peak")
        except (TypeError, ValueError):
            return
        if peaks:
            self._on_level(float(peaks[0]))

    def _on_stop_timeout(self) -> bool:
        self._stop_timeout = 0
        log("délai EOS dépassé, finalisation directe du muxeur")
        self._force_mux_eos()
        GLib.timeout_add(200, self._on_mux_eos_timeout)
        return GLib.SOURCE_REMOVE

    def _on_mux_eos_timeout(self) -> bool:
        self._begin_cleanup(None)
        return GLib.SOURCE_REMOVE

    def _force_mux_eos(self) -> None:
        """Envoie l'EOS aux pads du muxeur pour ne pas perdre le fichier.

        Sans EOS, mp4mux/WebM mux n'écrit pas l'index final et le fichier
        reste vide même après la remise à NULL.
        """
        pipeline = self._pipeline
        if pipeline is None:
            return
        for name in ("mux", "streammux"):
            mux = pipeline.get_by_name(name)
            if mux is None:
                continue
            for pad in mux.sinkpads:
                try:
                    pad.send_event(Gst.Event.new_eos())
                except Exception:  # noqa: BLE001 - pad déjà libéré
                    pass

    def _remove_bus_watch(self) -> None:
        if self._bus is not None:
            try:
                self._bus.remove_signal_watch()
            except Exception:  # noqa: BLE001 - bus déjà libéré
                pass
            if self._bus_watch:
                try:
                    self._bus.disconnect(self._bus_watch)
                except Exception:  # noqa: BLE001
                    pass
                self._bus_watch = 0
            self._bus = None

    def _discard_started_pipeline(self) -> None:
        """Nettoyage synchrone après un échec de démarrage (pas de flux)."""
        if self._stop_timeout:
            GLib.source_remove(self._stop_timeout)
            self._stop_timeout = 0
        self._remove_bus_watch()
        pipeline = self._pipeline
        self._pipeline = None
        self._done = True
        if pipeline is not None:
            try:
                pipeline.set_state(Gst.State.NULL)
            except Exception:  # noqa: BLE001
                pass

    def _begin_cleanup(self, error: str | None) -> None:
        if self._done:
            return
        self._done = True
        log(f"nettoyage du pipeline (erreur={error!r})")
        if self._stop_timeout:
            GLib.source_remove(self._stop_timeout)
            self._stop_timeout = 0

        pipeline = self._pipeline
        self._pipeline = None
        self._remove_bus_watch()

        if pipeline is None:
            GLib.idle_add(self._emit_finished, error)
            return

        # set_state(NULL) peut attendre la fin des fils de streaming. Le
        # faire ici bloquerait la boucle GTK (et donc la fermeture demandée
        # depuis la barre des tâches) : on le déporte dans un fil dédié.
        thread = threading.Thread(
            target=self._finalize_pipeline,
            args=(pipeline, error),
            name="capturo-finalize",
            daemon=True,
        )
        thread.start()
        # Même si un élément refuse de passer à NULL, on ne laisse jamais
        # l'application bloquée : la fenêtre pourra se fermer après ce délai.
        if not self._teardown_timeout:
            self._teardown_timeout = GLib.timeout_add(
                4000, self._on_teardown_timeout
            )

    def _on_teardown_timeout(self) -> bool:
        self._teardown_timeout = 0
        log("garde-fou : le pipeline ne répond pas, finalisation forcée")
        self._emit_finished(None)
        return GLib.SOURCE_REMOVE

    def _finalize_pipeline(self, pipeline, error: str | None) -> None:
        log("remise à NULL du pipeline…")
        try:
            result = pipeline.set_state(Gst.State.NULL)
            log(f"set_state(NULL) -> {result}")
            if result == Gst.StateChangeReturn.ASYNC:
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    state, _pending = pipeline.get_state(100 * Gst.MSECOND)
                    if state == Gst.State.NULL:
                        break
                log(f"état final : {state}")
        except Exception as exc:  # noqa: BLE001 - on finalise quoi qu'il arrive
            log(f"remise à NULL impossible : {exc}")
        log("pipeline arrêté")
        GLib.idle_add(self._emit_finished, error)

    def _emit_finished(self, error: str | None) -> bool:
        if self._finished:
            return GLib.SOURCE_REMOVE
        self._finished = True
        if self._teardown_timeout:
            GLib.source_remove(self._teardown_timeout)
            self._teardown_timeout = 0
        log(f"enregistrement finalisé (erreur={error!r})")
        self._on_finished(error)
        return GLib.SOURCE_REMOVE
