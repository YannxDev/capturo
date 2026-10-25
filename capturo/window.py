"""Fenêtre principale de Capturo."""

import math
import os
import shlex
import shutil
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gst", "1.0")

from gi.repository import (  # noqa: E402
    Adw,
    Gdk,
    GdkPixbuf,
    Gio,
    GLib,
    Gst,
    Gtk,
    Pango,
)

from . import APP_ID, discovery, editors  # noqa: E402
from . import credentials, recorder, settings  # noqa: E402
from .diagnostics import log, path as log_path  # noqa: E402
from .portal import ScreenCastSession  # noqa: E402

COUNTDOWN_LABELS = ("Aucun", "3 s", "5 s", "10 s")

THEME_LABELS = ("Automatique", "Clair", "Sombre")
THEME_VALUES = ("auto", "light", "dark")
ACCENTS = {
    "Bleu": "#3584e4",
    "Violet": "#9141ac",
    "Rose": "#e4399c",
    "Rouge": "#e01b24",
    "Orange": "#ff7800",
    "Jaune": "#e5a50a",
    "Vert": "#2ec27e",
    "Turquoise": "#00b5ad",
    "Gris": "#77767b",
}
ACCENT_LABELS = tuple(ACCENTS)
OUTPUT_MODE_LABELS = (
    "Enregistrement",
    "Streaming RTMP",
    "Enregistrement + streaming",
)
RTMP_PLATFORMS = ("Personnalisé", "Twitch", "YouTube")
DEFAULT_FILENAME_TEMPLATE = "Capture_%Y-%m-%d_%H-%M-%S"
RTMP_PRESETS = {
    "Twitch": "rtmp://live.twitch.tv/app/",
    "YouTube": "rtmp://a.rtmp.youtube.com/live2/",
}


def _hex_luminance(color: str) -> float:
    """Luminance approximative d'une couleur #rrggbb (0 = noir, 1 = blanc)."""
    value = color.lstrip("#")
    red, green, blue = (
        int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)
    )
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _format_duration(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_size(size: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if size < 1024 or unit == "To":
            return f"{size:.0f} {unit}" if unit == "o" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} To"


class CapturoWindow(Adw.ApplicationWindow):
    __gtype_name__ = "CapturoWindow"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_default_size(640, 900)
        # Taille de secours pour les tests d'interface sous Broadway.
        debug_size = os.environ.get("CAPTURO_DEBUG_SIZE")
        if debug_size:
            try:
                debug_width, debug_height = (
                    int(part) for part in debug_size.lower().split("x", 1)
                )
                self.set_default_size(debug_width, debug_height)
            except ValueError:
                pass
        self.set_title("Capturo")

        self._config = settings.load()
        self._portal: ScreenCastSession | None = None
        self._recorder: recorder.Recorder | None = None
        self._recording = False
        self._starting = False
        self._finalizing = False
        self._closing = False
        self._close_pending = False
        self._quit_watchdog = 0
        self._start_time = 0.0
        self._timer_id = 0
        self._countdown_id = 0
        self._countdown_left = 0
        self._pending = None  # (session, chemin, fd, description)
        self._output_path: str | None = None
        self._pipewire_fd: int | None = None
        self._folder = settings.default_folder()
        self._cameras = recorder.list_cameras()
        self._audio_inputs = recorder.list_audio_inputs()
        self._preview_last_update = 0.0
        self._detecting = False
        self._camera_token = ""
        self._accent_provider = None
        self._editor_pending_path: str | None = None
        self._editors: list[tuple[str, str]] = []
        self._editor_commands: list[str | None] = []
        self._format_saved_index = 0
        self._quality_saved_index = 2
        self._stream_active = False
        self._hidden_for_recording = False
        self._hide_timer = 0
        self._key_save_id = 0
        self._key_in_keyring = False
        self._verifying = False

        self._build_ui()
        self._load_config()
        self._set_status("Prêt", "Choisis le format puis lance l'enregistrement.")

        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self._toast_overlay = Adw.ToastOverlay()

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(
                title="Capturo", subtitle="Enregistreur & diffusion"
            )
        )

        menu = Gio.Menu()
        menu.append("Ouvrir le journal", "win.open-log")
        menu.append("À propos de Capturo", "app.about")
        menu.append("Quitter", "app.quit")
        menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu"
        )
        header.pack_end(menu_button)
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()
        self._page = page

        preview_section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6
        )
        self._preview_section = preview_section
        preview_section.set_margin_top(10)
        preview_section.set_margin_bottom(4)
        preview_section.set_margin_start(18)
        preview_section.set_margin_end(18)

        preview_title = Gtk.Label(label="Aperçu", xalign=0)
        preview_title.add_css_class("heading")
        preview_section.append(preview_title)

        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        preview_box.add_css_class("preview-box")
        self._preview_picture = Gtk.Picture()
        self._preview_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._preview_picture.set_can_shrink(True)
        preview_frame = Gtk.AspectFrame(ratio=16 / 9, obey_child=False)
        preview_frame.set_child(self._preview_picture)
        preview_box.append(preview_frame)

        preview_overlay = Gtk.Overlay()
        preview_overlay.set_child(preview_box)
        self._preview_placeholder = Gtk.Label(
            label="L'aperçu s'affiche pendant l'enregistrement ou la diffusion"
        )
        self._preview_placeholder.add_css_class("dim-label")
        self._preview_placeholder.set_halign(Gtk.Align.CENTER)
        self._preview_placeholder.set_valign(Gtk.Align.CENTER)
        preview_overlay.add_overlay(self._preview_placeholder)

        self._live_badge = Gtk.Label(label="● REC")
        self._live_badge.add_css_class("live-badge")
        self._live_badge.set_halign(Gtk.Align.START)
        self._live_badge.set_valign(Gtk.Align.START)
        self._live_badge.set_margin_start(12)
        self._live_badge.set_margin_top(12)
        self._live_badge.set_visible(False)
        preview_overlay.add_overlay(self._live_badge)
        preview_section.append(preview_overlay)

        css = Gtk.CssProvider()
        css.load_from_data(
            b".preview-box { background-color: #0b0b0f; border-radius: 12px; }"
            b".live-badge { background-color: rgba(0, 0, 0, 0.72);"
            b" color: #ff5c5c; border-radius: 999px; padding: 4px 12px;"
            b" font-weight: 700; font-size: 11px; }"
        )
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        video_group = Adw.PreferencesGroup(
            title="Vidéo",
            description="Le format et la qualité sont appliqués pendant la capture.",
        )
        self._format_row, self._format_dropdown = self._add_dropdown_row(
            video_group, "Format", "Conteneur et codec",
            recorder.FORMAT_LABELS,
        )
        self._quality_row, self._quality_dropdown = self._add_dropdown_row(
            video_group, "Qualité", "Débit adapté à la résolution et aux images/s",
            recorder.QUALITY_LABELS,
        )
        _row, self._fps_dropdown = self._add_dropdown_row(
            video_group,
            "Images par seconde",
            "24 i/s pour un rendu cinéma, 30 ou 60 i/s pour le reste",
            [f"{fps} i/s" for fps in recorder.FPS_CHOICES],
        )
        self._video_bitrate_row = Adw.SpinRow.new_with_range(0, 100000, 500)
        self._video_bitrate_row.set_title("Débit vidéo")
        self._video_bitrate_row.set_subtitle(
            "0 = automatique · kb/s (500 à 100000)"
        )
        self._video_bitrate_row.set_digits(0)
        self._video_bitrate_row.set_value(0)
        video_group.add(self._video_bitrate_row)
        _row, self._resolution_dropdown = self._add_dropdown_row(
            video_group,
            "Résolution",
            "4K si l'écran le permet ; réduit l'image s'il est plus grand",
            recorder.RESOLUTIONS,
        )
        self._encoder_row, self._encoder_dropdown = self._add_dropdown_row(
            video_group,
            "Encodeur",
            "Matériel = moins de CPU ; repli logiciel automatique",
            recorder.ENCODER_LABELS,
        )
        self._fragmented_row = Adw.SwitchRow(
            title="MP4 fragmenté",
            subtitle=(
                "Plus robuste si Capturo est interrompu (MP4 uniquement)"
            ),
        )
        video_group.add(self._fragmented_row)
        self._preview_row = Adw.SwitchRow(
            title="Aperçu en direct",
            subtitle="Désactive-le pour économiser du CPU en 4K",
        )
        video_group.add(self._preview_row)
        page.add(video_group)

        stream_group = Adw.PreferencesGroup(
            title="Diffusion en direct",
            description=(
                "Twitch, YouTube ou tout serveur RTMP. La diffusion utilise "
                "H.264/AAC ; en mode simultané, le fichier MP4 reprend le "
                "même flux."
            ),
        )
        self._output_mode_row, self._output_mode_dropdown = (
            self._add_dropdown_row(
                stream_group,
                "Mode",
                "Enregistrer, diffuser ou les deux",
                OUTPUT_MODE_LABELS,
            )
        )
        self._platform_row, self._platform_dropdown = self._add_dropdown_row(
            stream_group,
            "Plateforme",
            "Préremplit l'adresse du serveur",
            RTMP_PLATFORMS,
        )
        self._rtmp_row = Adw.EntryRow(
            title="URL RTMP (serveur, avec la clé si besoin)",
            text="",
        )
        stream_group.add(self._rtmp_row)
        self._rtmp_key_row = Adw.PasswordEntryRow(
            title="Clé de diffusion (optionnelle)",
            text="",
        )
        self._rtmp_key_row.set_tooltip_text(
            "Si l'URL ne contient pas la clé, elle est ajoutée à la fin."
        )
        stream_group.add(self._rtmp_key_row)
        self._bitrate_row = Adw.SpinRow.new_with_range(1000, 20000, 500)
        self._bitrate_row.set_title("Débit de diffusion")
        self._bitrate_row.set_subtitle(
            "4500 kb/s convient à Twitch/YouTube en 1080p"
        )
        self._bitrate_row.set_digits(0)
        self._bitrate_row.set_value(4500)
        stream_group.add(self._bitrate_row)
        page.add(stream_group)

        camera_group = Adw.PreferencesGroup(
            title="Caméra",
            description=(
                "Incruste une webcam ou un flux Capturo Webcam par-dessus "
                "la capture. Le téléphone est détecté automatiquement en "
                "Wi-Fi, en USB (adb) ou par partage de connexion USB."
            ),
        )
        self._camera_switch = Adw.SwitchRow(
            title="Incruster la caméra",
            subtitle="Ajoute la caméra dans un coin de la vidéo",
        )
        camera_group.add(self._camera_switch)

        _row, self._camera_mode_dropdown = self._add_dropdown_row(
            camera_group,
            "Source",
            "Webcam branchée, flux Wi-Fi ou USB d'un téléphone",
            ("Caméra locale", "Caméra réseau (URL)"),
        )

        camera_labels = (
            [name for name, _source in self._cameras]
            if self._cameras
            else ["Aucune caméra détectée"]
        )
        self._camera_row, self._camera_dropdown = self._add_dropdown_row(
            camera_group, "Caméra", "Périphérique vidéo à afficher", camera_labels
        )
        refresh_button = Gtk.Button(
            icon_name="view-refresh-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Rechercher les caméras branchées",
        )
        refresh_button.add_css_class("flat")
        refresh_button.connect("clicked", lambda *_: self._refresh_cameras())
        self._camera_row.add_suffix(refresh_button)

        self._url_row = Adw.EntryRow(
            title="Adresse du flux (http://…/video ou rtsp://…)",
            visible=False,
        )
        self._detect_button = Gtk.Button(label="Détecter")
        self._detect_button.add_css_class("flat")
        self._detect_button.set_valign(Gtk.Align.CENTER)
        self._detect_button.set_tooltip_text(
            "Chercher Capturo Webcam en Wi-Fi ou en USB (adb / partage)"
        )
        self._detect_button.connect("clicked", self._on_detect_clicked)
        self._url_row.add_suffix(self._detect_button)
        camera_group.add(self._url_row)

        self._position_row, self._position_dropdown = self._add_dropdown_row(
            camera_group, "Position", "Coin de l'image",
            recorder.CAMERA_POSITIONS,
        )
        self._size_row, self._size_dropdown = self._add_dropdown_row(
            camera_group, "Taille", "Taille de l'incrustation",
            recorder.CAMERA_SIZES,
        )
        self._shape_row, self._shape_dropdown = self._add_dropdown_row(
            camera_group,
            "Forme",
            "Rectangle, carré ou cercle autour de la webcam",
            recorder.CAMERA_SHAPES,
        )
        self._mirror_row = Adw.SwitchRow(
            title="Miroir",
            subtitle="Retourne l'image de la caméra horizontalement",
        )
        camera_group.add(self._mirror_row)
        page.add(camera_group)

        audio_group = Adw.PreferencesGroup(
            title="Audio",
            description="Les pistes audio sont encodées avec la vidéo.",
        )
        self._mic_row = Adw.SwitchRow(
            title="Microphone",
            subtitle="Enregistre ta voix",
        )
        audio_group.add(self._mic_row)

        mic_labels = ["Microphone par défaut"] + [
            name for name, _source in self._audio_inputs
        ]
        self._mic_device_row, self._mic_device_dropdown = (
            self._add_dropdown_row(
                audio_group,
                "Entrée micro",
                "Périphérique utilisé pour la voix",
                mic_labels,
            )
        )
        mic_refresh = Gtk.Button(
            icon_name="view-refresh-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Rechercher les micros branchés",
        )
        mic_refresh.add_css_class("flat")
        mic_refresh.connect("clicked", lambda *_: self._refresh_audio_inputs())
        self._mic_device_row.add_suffix(mic_refresh)

        self._mic_volume_row = Adw.SpinRow.new_with_range(0, 200, 5)
        self._mic_volume_row.set_title("Volume du microphone")
        self._mic_volume_row.set_subtitle("0 à 200 % · 100 % = volume d'origine")
        self._mic_volume_row.set_digits(0)
        self._mic_volume_row.set_value(100)
        audio_group.add(self._mic_volume_row)

        self._system_row = Adw.SwitchRow(
            title="Son du système",
            subtitle="Enregistre ce qui joue sur les haut-parleurs",
        )
        audio_group.add(self._system_row)

        self._system_volume_row = Adw.SpinRow.new_with_range(0, 200, 5)
        self._system_volume_row.set_title("Volume du son système")
        self._system_volume_row.set_subtitle("0 à 200 % · 100 % = volume d'origine")
        self._system_volume_row.set_digits(0)
        self._system_volume_row.set_value(100)
        audio_group.add(self._system_volume_row)

        self._audio_delay_row = Adw.SpinRow.new_with_range(-500, 500, 10)
        self._audio_delay_row.set_title("Décalage du micro")
        self._audio_delay_row.set_subtitle(
            "Millisecondes · positif = retarde le micro"
        )
        self._audio_delay_row.set_digits(0)
        self._audio_delay_row.set_value(0)
        audio_group.add(self._audio_delay_row)

        self._level_row = Adw.ActionRow(
            title="Niveau audio",
            subtitle="Niveau du mixage pendant l'enregistrement",
            visible=False,
        )
        self._level_bar = Gtk.LevelBar.new_for_interval(0.0, 1.0)
        self._level_bar.set_size_request(140, -1)
        self._level_bar.set_valign(Gtk.Align.CENTER)
        self._level_row.add_suffix(self._level_bar)
        audio_group.add(self._level_row)

        self._phone_audio_row = Adw.SwitchRow(
            title="Micro du téléphone",
            subtitle=(
                "Son de Capturo Webcam, à la même adresse que la vidéo "
                "(/audio)"
            ),
            visible=False,
        )
        audio_group.add(self._phone_audio_row)
        page.add(audio_group)

        start_group = Adw.PreferencesGroup(
            title="Démarrage",
            description="Un délai pour se préparer avant l'enregistrement.",
        )
        _row, self._countdown_dropdown = self._add_dropdown_row(
            start_group, "Compte à rebours", "Avant le début de la capture",
            COUNTDOWN_LABELS,
        )
        self._hide_row = Adw.SwitchRow(
            title="Réduire Capturo pendant l'enregistrement",
            subtitle=(
                "La notification permet d'arrêter ou de rouvrir la fenêtre"
            ),
        )
        start_group.add(self._hide_row)
        page.add(start_group)

        output_group = Adw.PreferencesGroup(title="Destination")
        self._folder_row = Adw.ActionRow(
            title="Dossier de destination", subtitle=self._folder
        )

        choose_button = Gtk.Button(
            label="Choisir…", valign=Gtk.Align.CENTER
        )
        choose_button.connect("clicked", self._on_choose_folder)
        self._folder_row.add_suffix(choose_button)

        reset_button = Gtk.Button(
            label="Par défaut",
            valign=Gtk.Align.CENTER,
            tooltip_text="Revenir au dossier Vidéos",
        )
        reset_button.add_css_class("flat")
        reset_button.connect("clicked", self._on_reset_folder)
        self._folder_row.add_suffix(reset_button)

        open_button = Gtk.Button(
            icon_name="folder-open-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Ouvrir le dossier",
        )
        open_button.add_css_class("flat")
        open_button.connect("clicked", lambda *_: self._open_folder())
        self._folder_row.add_suffix(open_button)

        output_group.add(self._folder_row)

        self._filename_row = Adw.EntryRow(
            title="Modèle de nom de fichier",
            text=DEFAULT_FILENAME_TEMPLATE,
        )
        self._filename_row.set_tooltip_text(
            "Codes date/heure : %Y année, %m mois, %d jour, %H heure, "
            "%M minute, %S seconde. L'extension est ajoutée automatiquement."
        )
        output_group.add(self._filename_row)

        self._disk_row = Adw.ActionRow(
            title="Espace libre",
            subtitle="",
        )
        output_group.add(self._disk_row)
        page.add(output_group)

        editor_group = Adw.PreferencesGroup(
            title="Lecture et montage",
            description=(
                "Après l'enregistrement, Capturo peut ouvrir la vidéo dans "
                "le lecteur ou l'éditeur installé sur l'ordinateur."
            ),
        )
        self._editor_prompt_row = Adw.SwitchRow(
            title="Proposer d'ouvrir la vidéo",
            subtitle="Demande à la fin de l'enregistrement",
        )
        editor_group.add(self._editor_prompt_row)

        self._editors = editors.detect()
        self._editor_row, self._editor_dropdown = self._add_dropdown_row(
            editor_group,
            "Lecteur / éditeur vidéo",
            "Applications détectées automatiquement sur l'ordinateur",
            self._editor_labels(),
        )
        editor_refresh = Gtk.Button(
            icon_name="view-refresh-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Rechercher les lecteurs et éditeurs installés",
        )
        editor_refresh.add_css_class("flat")
        editor_refresh.connect("clicked", lambda *_: self._refresh_editors())
        self._editor_row.add_suffix(editor_refresh)

        self._editor_choose_row = Adw.ActionRow(
            title="Choisir une autre application",
            subtitle="Parcourir tous les lecteurs et éditeurs installés",
        )
        editor_choose = Gtk.Button(
            label="Choisir…",
            valign=Gtk.Align.CENTER,
            tooltip_text="Parcourir toutes les applications vidéo installées",
        )
        editor_choose.add_css_class("flat")
        editor_choose.connect("clicked", self._on_choose_editor)
        self._editor_choose_row.add_suffix(editor_choose)
        self._editor_choose_row.set_activatable_widget(editor_choose)
        editor_group.add(self._editor_choose_row)
        page.add(editor_group)

        appearance_group = Adw.PreferencesGroup(
            title="Apparence",
            description="Thème et couleur d'accent de Capturo.",
        )
        self._theme_row, self._theme_dropdown = self._add_dropdown_row(
            appearance_group,
            "Thème",
            "Automatique suit le réglage du système",
            THEME_LABELS,
        )
        self._accent_row, self._accent_dropdown = self._add_dropdown_row(
            appearance_group,
            "Couleur des boutons",
            "Couleur d'accent de l'interface",
            ACCENT_LABELS,
        )
        page.add(appearance_group)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(preview_section)
        content.append(page)
        page.set_vexpand(True)
        toolbar.set_content(content)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bar.set_margin_top(10)
        bar.set_margin_bottom(10)
        bar.set_margin_start(18)
        bar.set_margin_end(18)

        texts = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        self._status_label = Gtk.Label(label="Prêt", xalign=0)
        self._status_label.add_css_class("heading")
        self._detail_label = Gtk.Label(label="", xalign=0)
        self._detail_label.add_css_class("dim-label")
        self._detail_label.add_css_class("caption")
        self._detail_label.set_ellipsize(Pango.EllipsizeMode.END)
        texts.append(self._status_label)
        texts.append(self._detail_label)

        self._record_button = Gtk.Button(label="Enregistrer")
        self._record_button.add_css_class("suggested-action")
        self._record_button.add_css_class("pill")
        self._record_button.set_size_request(140, -1)
        self._record_button.connect("clicked", self._on_record_clicked)

        bar.append(texts)
        bar.append(self._record_button)
        toolbar.add_bottom_bar(bar)

        self._toast_overlay.set_child(toolbar)
        self.set_content(self._toast_overlay)

        record_action = Gio.SimpleAction.new("record", None)
        record_action.connect("activate", lambda *_: self._on_record_clicked(None))
        self.add_action(record_action)

        log_action = Gio.SimpleAction.new("open-log", None)
        log_action.connect("activate", lambda *_: self._open_log())
        self.add_action(log_action)

        cancel_action = Gio.SimpleAction.new("cancel-countdown", None)
        cancel_action.connect("activate", lambda *_: self._cancel_countdown())
        self.add_action(cancel_action)

        if self.get_application() is not None:
            self.get_application().set_accels_for_action(
                "win.record", ["<Primary>r"]
            )

    def _add_dropdown_row(self, group, title, subtitle, labels):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        dropdown = Gtk.DropDown.new_from_strings(list(labels))
        dropdown.set_valign(Gtk.Align.CENTER)
        row.add_suffix(dropdown)
        row.set_activatable_widget(dropdown)
        group.add(row)
        return row, dropdown

    # --------------------------------------------------------- préférences
    def _load_config(self) -> None:
        config = self._config

        def pick(key: str, default: int, count: int) -> int:
            try:
                value = int(config.get(key, default))
            except (TypeError, ValueError):
                value = default
            return max(0, min(count - 1, value))

        self._format_dropdown.set_selected(
            pick("format", 0, len(recorder.FORMAT_LABELS))
        )
        self._quality_dropdown.set_selected(
            pick("quality", 2, len(recorder.QUALITY_LABELS))
        )
        self._fps_dropdown.set_selected(
            pick("fps", 0, len(recorder.FPS_CHOICES))
        )
        resolution_index = pick("resolution", 0, len(recorder.RESOLUTIONS))
        if "config_version" not in config:
            resolution_index = recorder._OLD_RESOLUTION_INDEXES.get(
                resolution_index, resolution_index
            )
        self._resolution_dropdown.set_selected(resolution_index)
        self._encoder_dropdown.set_selected(
            pick("encoder", 0, len(recorder.ENCODER_LABELS))
        )
        self._fragmented_row.set_active(
            bool(config.get("fragmented_mp4", False))
        )
        self._preview_row.set_active(bool(config.get("preview", True)))
        self._countdown_dropdown.set_selected(
            pick("countdown", 0, len(COUNTDOWN_LABELS))
        )

        has_camera = bool(self._cameras)
        self._camera_mode_dropdown.set_selected(pick("camera_mode", 0, 2))
        self._camera_token = str(config.get("camera_token", "") or "")
        self._url_row.set_text(str(config.get("camera_url", "") or ""))
        self._camera_switch.set_active(
            (has_camera or self._url_row.get_text().strip())
            and bool(config.get("camera", False))
        )
        if has_camera:
            self._camera_dropdown.set_selected(
                pick("camera_index", 0, len(self._cameras))
            )
        self._position_dropdown.set_selected(
            pick("camera_position", 0, len(recorder.CAMERA_POSITIONS))
        )
        self._size_dropdown.set_selected(
            pick("camera_size", 1, len(recorder.CAMERA_SIZES))
        )
        self._shape_dropdown.set_selected(
            pick("camera_shape", 0, len(recorder.CAMERA_SHAPES))
        )
        self._mirror_row.set_active(bool(config.get("camera_mirror", False)))

        self._mic_row.set_active(bool(config.get("mic", False)))
        self._system_row.set_active(bool(config.get("system_audio", True)))
        saved_mic = config.get("mic_source", "")
        mic_index = 0
        if isinstance(saved_mic, str) and saved_mic:
            for index, (_name, source) in enumerate(self._audio_inputs, start=1):
                if source == saved_mic:
                    mic_index = index
                    break
        self._mic_device_dropdown.set_selected(mic_index)

        def spin(key: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(config.get(key, default))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(maximum, value))

        self._mic_volume_row.set_value(spin("mic_volume", 100, 0, 200))
        self._system_volume_row.set_value(
            spin("system_volume", 100, 0, 200)
        )
        self._audio_delay_row.set_value(spin("audio_delay", 0, -500, 500))
        self._phone_audio_row.set_active(
            bool(config.get("phone_audio", False))
        )

        self._output_mode_dropdown.set_selected(
            pick("output_mode", 0, len(OUTPUT_MODE_LABELS))
        )
        self._platform_dropdown.set_selected(
            pick("stream_platform", 0, len(RTMP_PLATFORMS))
        )
        self._rtmp_row.set_text(str(config.get("rtmp_url", "") or ""))
        stored_key = credentials.load_key()
        legacy_key = str(config.get("rtmp_key", "") or "")
        if credentials.available():
            # Le trousseau fait foi : on n'écrit plus la clé dans le JSON,
            # même quand elle est encore vide.
            self._key_in_keyring = True
            key = stored_key or ""
            if not stored_key and legacy_key:
                if credentials.store_key(legacy_key):
                    key = legacy_key
                    log("clé RTMP migrée vers le trousseau")
                else:
                    # Trousseau verrouillé : on garde l'ancien stockage.
                    self._key_in_keyring = False
                    key = legacy_key
        else:
            key = legacy_key
        self._rtmp_key_row.set_text(key)
        try:
            bitrate = int(config.get("stream_bitrate", 4500))
        except (TypeError, ValueError):
            bitrate = 4500
        self._bitrate_row.set_value(max(1000, min(20000, bitrate)))
        try:
            video_bitrate = int(config.get("video_bitrate", 0))
        except (TypeError, ValueError):
            video_bitrate = 0
        self._video_bitrate_row.set_value(
            max(0, min(100000, video_bitrate))
        )
        self._hide_row.set_active(
            bool(config.get("hide_on_record", True))
        )
        template = config.get("filename_template")
        if not isinstance(template, str) or not template.strip():
            template = DEFAULT_FILENAME_TEMPLATE
        self._filename_row.set_text(template)
        self._theme_dropdown.set_selected(
            pick("theme", 0, len(THEME_LABELS))
        )
        self._accent_dropdown.set_selected(
            pick("accent", 0, len(ACCENT_LABELS))
        )
        self._editor_prompt_row.set_active(
            bool(config.get("editor_prompt", True))
        )
        saved_editor = config.get("editor")
        if not isinstance(saved_editor, str):
            saved_editor = ""
        try:
            config_version = int(config.get("config_version", 0) or 0)
        except (TypeError, ValueError):
            config_version = 0
        if config_version < 5 and not saved_editor and self._editors:
            # Les versions précédentes laissaient « Aucun » sélectionné même
            # quand des lecteurs étaient installés : on présélectionne le
            # premier détecté pour qu'il soit immédiatement visible.
            self._populate_editors(None)
        else:
            self._populate_editors(saved_editor)

        folder = config.get("folder")
        if isinstance(folder, str) and os.path.isdir(folder):
            self._folder = folder
            self._folder_row.set_subtitle(folder)

        for widget in (
            self._format_dropdown,
            self._quality_dropdown,
            self._fps_dropdown,
            self._resolution_dropdown,
            self._encoder_dropdown,
            self._countdown_dropdown,
            self._camera_mode_dropdown,
            self._camera_dropdown,
            self._position_dropdown,
            self._size_dropdown,
            self._shape_dropdown,
            self._mic_device_dropdown,
        ):
            widget.connect("notify::selected", lambda *_: self._save_config())
        for widget in (
            self._mic_row,
            self._system_row,
            self._phone_audio_row,
            self._mirror_row,
            self._fragmented_row,
            self._preview_row,
            self._hide_row,
        ):
            widget.connect("notify::active", lambda *_: self._save_config())
        for widget in (
            self._mic_volume_row,
            self._system_volume_row,
            self._audio_delay_row,
        ):
            widget.connect("notify::value", lambda *_: self._save_config())
        self._filename_row.connect(
            "notify::text", lambda *_: self._save_config()
        )
        self._preview_row.connect(
            "notify::active", lambda *_: self._update_preview_visibility()
        )
        self._mic_row.connect(
            "notify::active", lambda *_: self._update_audio_sensitivity()
        )
        self._system_row.connect(
            "notify::active", lambda *_: self._update_audio_sensitivity()
        )
        self._format_dropdown.connect(
            "notify::selected", lambda *_: self._update_encoder_subtitle()
        )
        for widget in (
            self._quality_dropdown,
            self._fps_dropdown,
            self._resolution_dropdown,
        ):
            widget.connect(
                "notify::selected", lambda *_: self._update_disk_row()
            )
        self._video_bitrate_row.connect(
            "notify::value", lambda *_: self._update_disk_row()
        )
        self._camera_switch.connect(
            "notify::active", self._on_camera_switch_changed
        )
        self._camera_mode_dropdown.connect(
            "notify::selected", lambda *_: self._on_camera_mode_changed()
        )
        self._url_row.connect("notify::text", self._on_camera_url_changed)

        self._output_mode_dropdown.connect(
            "notify::selected", lambda *_: self._on_output_mode_changed()
        )
        self._platform_dropdown.connect(
            "notify::selected", lambda *_: self._on_platform_changed()
        )
        self._rtmp_row.connect("notify::text", lambda *_: self._save_config())
        self._rtmp_key_row.connect(
            "notify::text", lambda *_: self._on_rtmp_key_changed()
        )
        self._bitrate_row.connect("notify::value", lambda *_: self._save_config())
        self._video_bitrate_row.connect(
            "notify::value", lambda *_: self._save_config()
        )
        self._theme_dropdown.connect(
            "notify::selected", lambda *_: self._on_theme_changed()
        )
        self._accent_dropdown.connect(
            "notify::selected", lambda *_: self._on_accent_changed()
        )
        self._editor_prompt_row.connect(
            "notify::active", lambda *_: self._save_config()
        )
        self._editor_dropdown.connect(
            "notify::selected", lambda *_: self._on_editor_changed()
        )

        self._update_camera_sensitivity()
        self._update_output_mode_ui()
        self._update_preview_visibility()
        self._update_audio_sensitivity()
        self._update_encoder_subtitle()
        self._update_disk_row()
        self._apply_appearance()
        if (
            self._camera_mode_is_network()
            and not self._url_row.get_text().strip()
        ):
            GLib.timeout_add(700, self._auto_detect)

    @staticmethod
    def _url_token(url: str) -> str:
        """Jeton éventuel présent dans l'URL de la caméra."""
        if not url:
            return ""
        try:
            values = parse_qs(urlsplit(url).query).get("token", [])
        except ValueError:
            return ""
        return values[0].strip() if values else ""

    @staticmethod
    def _url_with_token(url: str, token: str) -> str:
        """Ajoute le jeton à une URL qui n'en contient pas."""
        if not url or not token or CapturoWindow._url_token(url):
            return url
        parts = urlsplit(url)
        separator = "&" if parts.query else ""
        query = f"{parts.query}{separator}token={quote(token)}"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))

    def _save_config(self) -> None:
        camera_url = self._url_row.get_text().strip()
        token = self._url_token(camera_url)
        if token:
            self._camera_token = token
        data = {
            "format": self._format_dropdown.get_selected(),
            "quality": self._quality_dropdown.get_selected(),
            "fps": self._fps_dropdown.get_selected(),
            "resolution": self._resolution_dropdown.get_selected(),
            "encoder": self._encoder_dropdown.get_selected(),
            "fragmented_mp4": self._fragmented_row.get_active(),
            "preview": self._preview_row.get_active(),
            "countdown": self._countdown_dropdown.get_selected(),
            "camera": self._camera_switch.get_active(),
            "camera_mode": self._camera_mode_dropdown.get_selected(),
            "camera_url": camera_url,
            "camera_token": getattr(self, "_camera_token", ""),
            "camera_index": self._camera_dropdown.get_selected(),
            "camera_position": self._position_dropdown.get_selected(),
            "camera_size": self._size_dropdown.get_selected(),
            "camera_shape": self._shape_dropdown.get_selected(),
            "camera_mirror": self._mirror_row.get_active(),
            "mic": self._mic_row.get_active(),
            "mic_source": self._selected_mic_source(),
            "mic_volume": int(self._mic_volume_row.get_value()),
            "system_audio": self._system_row.get_active(),
            "system_volume": int(self._system_volume_row.get_value()),
            "audio_delay": int(self._audio_delay_row.get_value()),
            "phone_audio": self._phone_audio_row.get_active(),
            "output_mode": self._output_mode_dropdown.get_selected(),
            "stream_platform": self._platform_dropdown.get_selected(),
            "rtmp_url": self._rtmp_row.get_text().strip(),
            "stream_bitrate": int(self._bitrate_row.get_value()),
            "video_bitrate": int(self._video_bitrate_row.get_value()),
            "theme": self._theme_dropdown.get_selected(),
            "accent": self._accent_dropdown.get_selected(),
            "editor": self._selected_editor(),
            "editor_prompt": self._editor_prompt_row.get_active(),
            "folder": self._folder,
            "filename_template": self._filename_row.get_text().strip()
            or DEFAULT_FILENAME_TEMPLATE,
            "hide_on_record": self._hide_row.get_active(),
            "config_version": 6,
        }
        if not self._key_in_keyring:
            data["rtmp_key"] = self._rtmp_key_row.get_text().strip()
        settings.save(data)

    def _selected_mic_source(self) -> str:
        index = self._mic_device_dropdown.get_selected()
        if 1 <= index <= len(self._audio_inputs):
            return self._audio_inputs[index - 1][1]
        return "pulsesrc"

    def _on_rtmp_key_changed(self) -> None:
        self._save_config()
        if not credentials.available():
            return
        if self._key_save_id:
            GLib.source_remove(self._key_save_id)
        self._key_save_id = GLib.timeout_add(700, self._store_rtmp_key)

    def _store_rtmp_key(self) -> bool:
        self._key_save_id = 0
        key = self._rtmp_key_row.get_text().strip()
        self._key_in_keyring = credentials.store_key(key)
        if self._key_in_keyring:
            log(
                "clé RTMP enregistrée dans le trousseau"
                if key
                else "clé RTMP effacée du trousseau"
            )
            self._save_config()
        return GLib.SOURCE_REMOVE

    def _update_encoder_subtitle(self) -> None:
        index = self._format_dropdown.get_selected()
        format_id = recorder.FORMAT_IDS[index]
        hardware = recorder.hardware_encoder(format_id)
        if hardware:
            self._encoder_row.set_subtitle(
                f"Encodeur matériel détecté : {hardware}"
            )
        else:
            self._encoder_row.set_subtitle(
                "Aucun encodeur matériel détecté — repli logiciel"
            )

    def _update_preview_visibility(self) -> None:
        self._preview_section.set_visible(self._preview_row.get_active())

    def _update_audio_sensitivity(self) -> None:
        mic = self._mic_row.get_active()
        system = self._system_row.get_active()
        self._mic_device_row.set_sensitive(mic)
        self._mic_volume_row.set_sensitive(mic)
        self._audio_delay_row.set_sensitive(mic)
        self._system_volume_row.set_sensitive(system)

    def _update_disk_row(self) -> None:
        try:
            free = shutil.disk_usage(self._folder).free
        except OSError:
            self._disk_row.set_subtitle("Espace disque inconnu")
            return
        width, height = 1920, 1080
        try:
            resolution = recorder.RESOLUTIONS[
                self._resolution_dropdown.get_selected()
            ]
            if resolution != "Native":
                height = recorder._resolution_height(resolution)
                width = int(round(height * 16 / 9 / 2)) * 2
        except (ValueError, IndexError):
            pass
        fps = recorder.FPS_CHOICES[self._fps_dropdown.get_selected()]
        quality = self._quality_dropdown.get_selected()
        bitrate = recorder.estimate_bitrate(
            width, height, fps, quality,
            int(self._video_bitrate_row.get_value()),
        )
        per_minute = (bitrate + 192) * 1000 / 8 * 60
        self._disk_row.set_subtitle(
            f"{_format_size(free)} libres · "
            f"≈ {_format_size(per_minute)} par minute"
        )

    def _camera_mode_is_network(self) -> bool:
        return self._camera_mode_dropdown.get_selected() == 1

    def _update_camera_sensitivity(self) -> None:
        network = self._camera_mode_is_network()
        self._camera_row.set_visible(not network)
        self._url_row.set_visible(network)

        if network:
            has_source = bool(self._url_row.get_text().strip())
        else:
            has_source = bool(self._cameras)

        self._camera_switch.set_sensitive(has_source)
        if not has_source and self._camera_switch.get_active():
            self._camera_switch.set_active(False)

        enabled = has_source and self._camera_switch.get_active()
        self._camera_dropdown.set_sensitive(enabled)
        self._position_row.set_sensitive(enabled)
        self._size_row.set_sensitive(enabled)
        self._shape_row.set_sensitive(enabled)
        self._mirror_row.set_sensitive(enabled)
        self._phone_audio_row.set_visible(network)
        self._phone_audio_row.set_sensitive(enabled)
        self._detect_button.set_sensitive(network and not self._detecting)

    def _on_camera_switch_changed(self, *_args) -> None:
        self._update_camera_sensitivity()
        self._save_config()

    def _on_camera_mode_changed(self) -> None:
        self._update_camera_sensitivity()
        self._save_config()
        if (
            self._camera_mode_is_network()
            and not self._url_row.get_text().strip()
        ):
            self._on_detect_clicked()

    def _on_camera_url_changed(self, *_args) -> None:
        self._update_camera_sensitivity()
        self._save_config()

    def _refresh_cameras(self) -> None:
        self._cameras = recorder.list_cameras()
        labels = (
            [name for name, _source in self._cameras]
            if self._cameras
            else ["Aucune caméra détectée"]
        )
        self._camera_dropdown.set_model(Gtk.StringList.new(labels))
        self._camera_dropdown.set_selected(0)

        if self._cameras:
            count = len(self._cameras)
            self._toast(
                f"{count} caméra détectée" if count == 1
                else f"{count} caméras détectées"
            )
            if not self._camera_mode_is_network():
                self._camera_switch.set_sensitive(True)
                if not self._camera_switch.get_active():
                    self._camera_switch.set_active(True)
        else:
            self._toast("Aucune caméra détectée.")

        self._update_camera_sensitivity()
        self._save_config()

    def _refresh_audio_inputs(self) -> None:
        current = self._selected_mic_source()
        self._audio_inputs = recorder.list_audio_inputs()
        labels = ["Microphone par défaut"] + [
            name for name, _source in self._audio_inputs
        ]
        self._mic_device_dropdown.set_model(Gtk.StringList.new(labels))
        selected = 0
        for index, (_name, source) in enumerate(self._audio_inputs, start=1):
            if source == current:
                selected = index
                break
        self._mic_device_dropdown.set_selected(selected)
        count = len(self._audio_inputs)
        if count:
            self._toast(
                f"{count} micro détecté" if count == 1
                else f"{count} micros détectés"
            )
        else:
            self._toast("Aucun micro détecté.")
        self._save_config()

    def _auto_detect(self) -> bool:
        if (
            self._camera_mode_is_network()
            and not self._url_row.get_text().strip()
            and not self._recording
            and not self._starting
        ):
            self._on_detect_clicked()
        return GLib.SOURCE_REMOVE

    def _on_detect_clicked(self, _button=None) -> None:
        if self._detecting or not self._camera_mode_is_network():
            return
        self._detecting = True
        self._detect_button.set_sensitive(False)
        self._detect_button.set_label("Recherche…")
        threading.Thread(target=self._detect_worker, daemon=True).start()

    def _detect_worker(self) -> None:
        try:
            urls = discovery.discover_cameras(timeout=1.5)
        except Exception:  # noqa: BLE001 - la découverte ne doit rien casser
            urls = []
        GLib.idle_add(self._on_detected, urls)

    def _on_detected(self, urls: list[str]) -> bool:
        self._detecting = False
        self._detect_button.set_label("Détecter")
        self._detect_button.set_sensitive(self._camera_mode_is_network())
        if not urls:
            hint = discovery.usb_hint()
            message = (
                "Aucune Capturo Webcam détectée. "
                "Vérifie le Wi-Fi, l'USB et que l'app est démarrée."
            )
            if hint:
                message = f"{message} {hint}"
            self._toast(message)
            return GLib.SOURCE_REMOVE
        url = urls[0]
        # adb ne transmet pas le jeton du téléphone : on réutilise celui
        # déjà connu pour ne pas remplacer une URL valide par un 401.
        known_token = self._url_token(self._url_row.get_text()) or getattr(
            self, "_camera_token", ""
        )
        if known_token:
            url = self._url_with_token(url, known_token)
        self._url_row.set_text(url)
        self._save_config()
        host = urlsplit(url).hostname or url
        if host in ("127.0.0.1", "localhost", "::1"):
            source = "USB (adb)"
        elif host.startswith(discovery.USB_PREFIXES):
            source = "USB (partage)"
        else:
            source = "Wi-Fi"
        if len(urls) > 1:
            self._toast(
                f"Capturo Webcam détectée en {source} : {host} "
                f"(+{len(urls) - 1} autre)"
            )
        else:
            self._toast(f"Capturo Webcam détectée en {source} : {host}")
        return GLib.SOURCE_REMOVE

    # ---------------------------------------------------------- capture
    def _on_record_clicked(self, _button) -> None:
        if self._countdown_id:
            self._cancel_countdown()
        elif self._recording:
            self._stop_recording()
        elif not self._starting:
            self._start_recording()

    def _start_recording(self) -> None:
        if self._streaming_mode() != 0:
            location = self._rtmp_location()
            if not location.startswith("rtmp://"):
                self._toast(
                    "Renseigne une URL RTMP valide (rtmp://…) dans Diffusion."
                )
                return

        if self._camera_switch.get_active() and self._camera_mode_is_network():
            url = self._url_row.get_text().strip()
            try:
                recorder.network_camera_source(url)
            except ValueError as exc:
                self._toast(str(exc))
                return

        if (
            self._camera_switch.get_active()
            and self._camera_mode_is_network()
            and self._phone_audio_row.get_active()
            and self._phone_audio_url() is None
        ):
            self._toast(
                "Renseigne l'adresse de la caméra réseau pour le micro."
            )
            return

        if self._streaming_mode() != 1:
            try:
                free = shutil.disk_usage(self._folder).free
            except OSError:
                free = None
            if free is not None and free < 300 * 1024 * 1024:
                self._toast(
                    "Espace disque insuffisant : libère au moins 300 Mo "
                    "dans le dossier de destination."
                )
                return

        if self._streaming_mode() != 0 and credentials.available():
            self._store_rtmp_key()

        self._starting = True
        self._record_button.set_sensitive(False)
        self._set_status(
            "Sélection de l'écran…",
            "Choisis l'écran ou la fenêtre dans la fenêtre de GNOME.",
        )
        self._portal = ScreenCastSession(
            self._on_portal_ready, self._on_portal_error
        )
        self._portal.start()

    def _on_portal_ready(self, session, node_id, fd, size) -> None:
        self._starting = False
        if self._closing:
            # La fenêtre a été fermée pendant le sélecteur d'écran : on
            # n'ouvre pas d'enregistrement fantôme.
            self._close_session(session)
            self._close_pipewire_fd()
            return
        self._record_button.set_sensitive(True)

        try:
            mode = self._streaming_mode()
            stream_url = self._rtmp_location() if mode != 0 else None
            output_path = (
                None if mode == 1
                else self._make_output_path(force_mp4=(mode == 2))
            )
            description = recorder.build_pipeline(
                fd=fd,
                node_id=node_id,
                source_size=size,
                output_path=output_path,
                format_id=recorder.FORMAT_IDS[
                    self._format_dropdown.get_selected()
                ],
                quality_index=self._quality_dropdown.get_selected(),
                fps=recorder.FPS_CHOICES[self._fps_dropdown.get_selected()],
                resolution=recorder.RESOLUTIONS[
                    self._resolution_dropdown.get_selected()
                ],
                mic=self._mic_row.get_active(),
                system_audio=self._system_row.get_active(),
                camera_source=self._selected_camera_source(),
                camera_position=self._position_dropdown.get_selected(),
                camera_size=self._size_dropdown.get_selected(),
                camera_mirror=self._mirror_row.get_active(),
                camera_shape=self._shape_dropdown.get_selected(),
                phone_audio_url=self._phone_audio_url(),
                stream_url=stream_url,
                stream_bitrate=int(self._bitrate_row.get_value()),
                video_bitrate=int(self._video_bitrate_row.get_value()),
                encoder_mode=self._encoder_dropdown.get_selected(),
                fragmented_mp4=self._fragmented_row.get_active(),
                preview=self._preview_row.get_active(),
                mic_source=self._selected_mic_source(),
                mic_volume=int(self._mic_volume_row.get_value()),
                system_volume=int(self._system_volume_row.get_value()),
                audio_delay_ms=int(self._audio_delay_row.get_value()),
            )
        except Exception as exc:  # noqa: BLE001 - message à l'utilisateur
            self._close_session(session)
            self._on_portal_error(
                f"Impossible de préparer l'enregistrement : {exc}", False
            )
            return

        self._pending = (session, output_path, fd, description)
        self._pipewire_fd = fd

        seconds = recorder.COUNTDOWN_CHOICES[
            self._countdown_dropdown.get_selected()
        ]
        if seconds:
            self._start_countdown(seconds)
        else:
            self._begin_recording()

    def _selected_camera_source(self) -> str | None:
        if not self._camera_switch.get_active():
            return None
        if self._camera_mode_is_network():
            url = self._url_row.get_text().strip()
            return recorder.network_camera_source(url) if url else None
        if not self._cameras:
            return None
        index = self._camera_dropdown.get_selected()
        if 0 <= index < len(self._cameras):
            return self._cameras[index][1]
        return None

    def _phone_audio_url(self) -> str | None:
        """Dérive l'URL audio (/audio) de l'URL vidéo de Capturo Webcam."""
        if not (self._camera_switch.get_active() and self._camera_mode_is_network()):
            return None
        if not self._phone_audio_row.get_active():
            return None
        url = self._url_row.get_text().strip()
        if not url:
            return None
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return None
        return urlunsplit((parts.scheme, parts.netloc, "/audio", parts.query, ""))

    def _start_countdown(self, seconds: int) -> None:
        self._countdown_left = seconds
        self._page.set_sensitive(False)
        self._record_button.set_label("Annuler")
        self._record_button.remove_css_class("suggested-action")
        self._record_button.add_css_class("destructive-action")
        self._set_status(
            f"Démarrage dans {seconds} s",
            "Clique sur Annuler pour interrompre.",
        )
        self._countdown_id = GLib.timeout_add(1000, self._tick_countdown)

    def _tick_countdown(self) -> bool:
        self._countdown_left -= 1
        if self._countdown_left <= 0:
            self._countdown_id = 0
            self._begin_recording()
            return GLib.SOURCE_REMOVE
        self._set_status(
            f"Démarrage dans {self._countdown_left} s",
            "Clique sur Annuler pour interrompre.",
        )
        return GLib.SOURCE_CONTINUE

    def _cancel_countdown(self) -> None:
        if self._countdown_id:
            GLib.source_remove(self._countdown_id)
            self._countdown_id = 0
        if self._pending is not None:
            session, _path, _fd, _description = self._pending
            self._close_session(session)
            self._pending = None
        self._close_pipewire_fd()
        self._set_recording_ui(False)
        self._set_status("Prêt", "Démarrage annulé.")

    def _begin_recording(self) -> None:
        if self._pending is None:
            return
        session, output_path, fd, description = self._pending
        self._pending = None
        self._output_path = output_path
        self._pipewire_fd = fd

        self._recorder = recorder.Recorder(
            self._on_recording_finished, self._on_audio_level
        )
        try:
            self._recorder.start(description, self._setup_preview)
        except RuntimeError as exc:
            self._recorder = None
            self._close_session(session)
            self._close_pipewire_fd()
            self._set_recording_ui(False)
            self._set_status("Erreur", str(exc))
            self._toast(str(exc))
            return

        self._recording = True
        self._start_time = time.monotonic()
        self._timer_id = GLib.timeout_add(1000, self._update_timer)
        self._set_recording_ui(True)
        self._notify_recording()
        self._enter_recording_mode()

        mode = self._streaming_mode()
        if mode == 1:
            self._set_status("Diffusion en direct", "00:00 · En direct")
        elif mode == 2:
            self._set_status("Enregistrement + diffusion", "00:00 · En direct")
        else:
            self._set_status("Enregistrement en cours", "00:00 · 0 o")

    # ------------------------------------------------------------ aperçu
    def _setup_preview(self, pipeline) -> None:
        sink = pipeline.get_by_name("preview_sink")
        if sink is not None:
            sink.connect("new-sample", self._on_preview_sample)

    def _on_preview_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        # L'échantillon est toujours retiré, même après l'arrêt : sinon
        # l'appsink bloque la branche d'aperçu et l'EOS n'arrive jamais.
        if not self._recording:
            return Gst.FlowReturn.OK
        now = time.monotonic()
        if now - self._preview_last_update < 0.08:
            return Gst.FlowReturn.OK
        self._preview_last_update = now

        buffer = sample.get_buffer()
        caps = sample.get_caps()
        if buffer is None or caps is None:
            return Gst.FlowReturn.OK
        structure = caps.get_structure(0)
        width = structure.get_value("width")
        height = structure.get_value("height")
        ok, info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(bytes(info.data)),
                GdkPixbuf.Colorspace.RGB,
                False,
                8,
                width,
                height,
                width * 3,
            )
        except (TypeError, ValueError):
            pixbuf = None
        finally:
            buffer.unmap(info)

        if pixbuf is not None and self._recording:
            GLib.idle_add(self._show_preview, pixbuf)
        return Gst.FlowReturn.OK

    def _show_preview(self, pixbuf) -> bool:
        if self._recording:
            self._preview_picture.set_pixbuf(pixbuf)
            self._preview_placeholder.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _on_audio_level(self, peak: float) -> None:
        GLib.idle_add(self._show_audio_level, peak)

    def _show_audio_level(self, peak: float) -> bool:
        if self._recording:
            decibels = 20 * math.log10(max(peak, 1e-6))
            self._level_bar.set_value(
                max(0.0, min(1.0, (decibels + 60) / 60))
            )
        return GLib.SOURCE_REMOVE

    def _enter_recording_mode(self) -> None:
        if self._close_pending or not self._hide_row.get_active():
            return
        self._hidden_for_recording = True
        # Attendre un peu avant de réduire : si le pipeline échoue tout de
        # suite (URL refusée, caméra absente), la fenêtre reste visible et
        # l'utilisateur voit le message d'erreur.
        self._hide_timer = GLib.timeout_add(1500, self._minimize_for_recording)

    def _minimize_for_recording(self) -> bool:
        self._hide_timer = 0
        if (
            not self._recording
            or self._close_pending
            or not self._hidden_for_recording
        ):
            return GLib.SOURCE_REMOVE
        try:
            self.minimize()
            log("fenêtre réduite pendant l'enregistrement")
        except Exception as exc:  # noqa: BLE001 - compositeur sans minimisation
            log(f"réduction impossible : {exc}")
        return GLib.SOURCE_REMOVE

    def _restore_window(self) -> None:
        if self._hide_timer:
            GLib.source_remove(self._hide_timer)
            self._hide_timer = 0
        if not self._hidden_for_recording:
            return
        self._hidden_for_recording = False
        try:
            self.present()
            if hasattr(self, "unminimize"):
                self.unminimize()
        except Exception as exc:  # noqa: BLE001 - fenêtre déjà détruite
            log(f"restauration de la fenêtre impossible : {exc}")

    def _notify_recording(self) -> None:
        notification = Gio.Notification.new("Enregistrement en cours")
        notification.set_body(
            "Capturo enregistre. Utilise « Arrêter » pour terminer."
        )
        notification.set_icon(Gio.ThemedIcon.new(APP_ID))
        notification.add_button("Arrêter", "app.stop-recording")
        notification.set_default_action("app.show-window")
        self.get_application().send_notification(
            "capture-en-cours", notification
        )

    def _clear_preview(self) -> None:
        self._preview_picture.set_pixbuf(None)
        self._preview_placeholder.set_visible(True)

    def _on_portal_error(self, message: str, cancelled: bool) -> None:
        self._starting = False
        self._portal = None
        self._pending = None
        self._close_pipewire_fd()
        if self._closing:
            return
        self._record_button.set_sensitive(True)
        self._set_recording_ui(False)
        if cancelled:
            self._set_status("Prêt", "Sélection annulée.")
        else:
            self._set_status("Erreur", message)
            self._toast(message)

    def _stop_recording(self) -> None:
        if self._recorder is None or self._finalizing:
            return
        log("arrêt de l'enregistrement demandé")
        self._finalizing = True
        self._recording = False
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._streaming_mode() == 1:
            self._set_status("Finalisation…", "Fin de la diffusion en cours.")
        else:
            self._set_status("Finalisation…", "Écriture du fichier en cours.")
        self._record_button.set_sensitive(False)
        self._recorder.stop()

    def _close_session(self, session) -> None:
        """Ferme une session du portail hors de la boucle GTK."""
        if session is None:
            return
        # Close() est un appel D-Bus bloquant : le faire ici figerait la
        # fenêtre, notamment quand on la ferme pendant un enregistrement.
        threading.Thread(
            target=session.close,
            name="capturo-portal",
            daemon=True,
        ).start()

    def _force_quit(self) -> bool:
        """Dernier recours : ne jamais laisser Capturo bloqué au milieu."""
        self._quit_watchdog = 0
        log("garde-fou de fermeture : arrêt immédiat du processus")
        os._exit(0)
        return GLib.SOURCE_REMOVE

    def _on_recording_finished(self, error: str | None) -> None:
        log(f"interface : enregistrement terminé (erreur={error!r})")
        self._recording = False
        self._finalizing = False
        if self._quit_watchdog:
            GLib.source_remove(self._quit_watchdog)
            self._quit_watchdog = 0
        self._record_button.set_sensitive(True)

        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0

        self._close_session(self._portal)
        self._portal = None

        self._close_pipewire_fd()
        self._recorder = None
        self._level_row.set_visible(False)
        self._level_bar.set_value(0.0)
        self._set_recording_ui(False)
        self.get_application().withdraw_notification("capture-en-cours")

        if error:
            message = self._humanize_error(error)
            self._set_status("Erreur", message)
            self._toast(message)
            self._notify_error(message)
            if (
                self._output_path
                and os.path.exists(self._output_path)
                and os.path.getsize(self._output_path) == 0
            ):
                try:
                    os.unlink(self._output_path)
                except OSError:
                    pass
            self._restore_window()
        else:
            path = self._output_path or ""
            if path:
                self._restore_window()
                self._set_status("Vérification…", path)
                self._verifying = True
                threading.Thread(
                    target=self._verify_output,
                    args=(path,),
                    name="capturo-verify",
                    daemon=True,
                ).start()
                return
            self._set_status("Diffusion terminée", "")
            self._toast("Diffusion terminée.")
            self._restore_window()

        if self._close_pending:
            self._close_pending = False
            self.close()

    def _verify_output(self, path: str) -> None:
        ok, message = recorder.verify_file(path)
        GLib.idle_add(self._on_output_verified, path, ok, message)

    def _on_output_verified(
        self, path: str, ok: bool, message: str
    ) -> bool:
        self._verifying = False
        if self._closing:
            return GLib.SOURCE_REMOVE
        if not ok:
            log(f"vérification échouée : {message}")
            self._set_status("Vérification échouée", message)
            self._toast(message)
        else:
            name = os.path.basename(path) or "Vidéo"
            self._set_status("Vidéo enregistrée", path)
            self._notify(name, path)
            toast = Adw.Toast.new(f"Enregistré : {name}")
            toast.set_button_label("Ouvrir le dossier")
            toast.connect("button-clicked", lambda *_: self._open_folder())
            self._toast_overlay.add_toast(toast)
            if not self._close_pending:
                self._maybe_offer_editor(path)
        if self._close_pending:
            self._close_pending = False
            self.close()
        return GLib.SOURCE_REMOVE

    # ---------------------------------------------------------- diffusion
    def _streaming_mode(self) -> int:
        return self._output_mode_dropdown.get_selected()

    def _rtmp_location(self) -> str:
        url = self._rtmp_row.get_text().strip()
        key = self._rtmp_key_row.get_text().strip()
        if not url:
            return ""
        if key and not url.endswith(key):
            if not url.endswith("/"):
                url += "/"
            url += key
        return url

    def _on_output_mode_changed(self) -> None:
        self._update_output_mode_ui()
        self._save_config()

    def _on_platform_changed(self) -> None:
        label = RTMP_PLATFORMS[self._platform_dropdown.get_selected()]
        preset = RTMP_PRESETS.get(label)
        if preset:
            current = self._rtmp_row.get_text().strip()
            if not current or current.startswith(
                ("rtmp://live.twitch.tv", "rtmp://a.rtmp.youtube.com")
            ):
                self._rtmp_row.set_text(preset)
        self._save_config()

    def _update_output_mode_ui(self) -> None:
        streaming = self._streaming_mode() != 0
        for row in (
            self._platform_row,
            self._rtmp_row,
            self._rtmp_key_row,
            self._bitrate_row,
        ):
            row.set_sensitive(streaming)

        if streaming and not self._stream_active:
            self._format_saved_index = self._format_dropdown.get_selected()
            self._quality_saved_index = self._quality_dropdown.get_selected()
        elif not streaming and self._stream_active:
            self._format_dropdown.set_selected(self._format_saved_index)
            self._quality_dropdown.set_selected(self._quality_saved_index)
        self._stream_active = streaming

        self._format_row.set_sensitive(not streaming)
        self._quality_row.set_sensitive(not streaming)
        self._video_bitrate_row.set_sensitive(not streaming)
        if streaming:
            if self._format_dropdown.get_selected() != 0:
                self._format_dropdown.set_selected(0)
            self._format_row.set_subtitle(
                "MP4 · H.264 (requis pour le streaming)"
            )
            self._quality_row.set_subtitle(
                "Le débit est défini par la diffusion"
            )
            self._video_bitrate_row.set_subtitle(
                "Le débit de diffusion est aussi utilisé pour le fichier"
            )
        else:
            self._format_row.set_subtitle("Conteneur et codec")
            self._quality_row.set_subtitle(
                "Débit adapté à la résolution et aux images/s"
            )
            self._video_bitrate_row.set_subtitle(
                "0 = automatique · kb/s (500 à 100000)"
            )
        self._update_record_button_label()

    def _update_record_button_label(self) -> None:
        if self._recording or self._countdown_id:
            return
        mode = self._streaming_mode()
        label = ("Enregistrer", "Diffuser", "Enreg. + direct")[mode]
        self._record_button.set_label(label)

    # ----------------------------------------------------------- apparence
    def _on_theme_changed(self) -> None:
        self._save_config()
        self._apply_appearance()

    def _on_accent_changed(self) -> None:
        self._save_config()
        self._apply_appearance()

    def _apply_appearance(self) -> None:
        scheme = (
            Adw.ColorScheme.DEFAULT,
            Adw.ColorScheme.FORCE_LIGHT,
            Adw.ColorScheme.FORCE_DARK,
        )[self._theme_dropdown.get_selected()]
        Adw.StyleManager.get_default().set_color_scheme(scheme)
        self._apply_accent(
            ACCENT_LABELS[self._accent_dropdown.get_selected()]
        )
        self.queue_draw()

    def _apply_accent(self, name: str) -> None:
        color = ACCENTS.get(name, ACCENTS["Bleu"])
        foreground = (
            "#000000" if _hex_luminance(color) > 0.62 else "#ffffff"
        )
        display = Gdk.Display.get_default()
        if display is None:
            return
        if self._accent_provider is not None:
            Gtk.StyleContext.remove_provider_for_display(
                display, self._accent_provider
            )
        provider = Gtk.CssProvider()
        provider.load_from_data(
            (
                f"@define-color accent_bg_color {color};"
                f"@define-color accent_color {color};"
                f"@define-color accent_fg_color {foreground};"
                f"@define-color accent_bg_color_active {color};"
                # Adwaita utilise souvent une version translucide pour les
                # sélections ; on force la couleur pleine pour éviter le côté
                # délavé, tout en gardant un texte lisible.
                "button.suggested-action {"
                f" background-color: {color}; color: {foreground}; }}"
                "button.suggested-action:hover {"
                f" background-color: shade({color}, 0.88);"
                f" color: {foreground}; }}"
                "row:selected, listview row:selected,"
                " dropdown popover listview row:selected {"
                f" background-color: {color}; color: {foreground}; }}"
                "row:selected label, listview row:selected label,"
                " dropdown popover listview row:selected label {"
                f" color: {foreground}; }}"
                # Certains thèmes (Yaru, etc.) utilisent leurs propres
                # variables pour les interrupteurs et les cases à cocher.
                # On les colore explicitement pour que l'accent choisi soit
                # appliqué partout, pas seulement au bouton d'enregistrement.
                "switch:checked {"
                f" background-color: {color}; }}"
                "switch:checked > slider {"
                f" background-color: {foreground}; }}"
                "checkbutton:checked > check {"
                f" background-color: {color}; color: {foreground}; }}"
                "radiobutton:checked > radio {"
                f" background-color: {color}; color: {foreground}; }}"
                "scale > trough > highlight {"
                f" background-color: {color}; }}"
                "progressbar > trough > progress {"
                f" background-color: {color}; }}"
                "entry:focus-within {"
                f" outline-color: {color}; }}"
            ).encode()
        )
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )
        self._accent_provider = provider
        for widget in (
            self._page,
            self._record_button,
            self._accent_dropdown,
            self._theme_dropdown,
        ):
            widget.queue_draw()

    # -------------------------------------------------------------- montage
    def _editor_labels(self) -> list[str]:
        labels = ["Aucun"]
        labels.extend(name for name, _command in self._editors)
        return labels

    def _populate_editors(self, saved_command: str | None = "") -> None:
        labels = self._editor_labels()
        commands: list[str] = [""]
        commands.extend(command for _name, command in self._editors)
        self._editor_commands = commands
        self._editor_dropdown.set_model(Gtk.StringList.new(labels))

        selected = 0
        if saved_command and saved_command in commands:
            selected = commands.index(saved_command)
        elif saved_command is None and len(commands) > 1:
            # Aucun choix enregistré : afficher directement le premier
            # lecteur/éditeur détecté plutôt que « Aucun ».
            selected = 1
        self._editor_dropdown.set_selected(selected)

        count = len(self._editors)
        if count == 0:
            self._editor_row.set_subtitle(
                "Aucun lecteur vidéo détecté — installe VLC, Showtime, "
                "Shotcut ou un autre lecteur"
            )
        elif count == 1:
            self._editor_row.set_subtitle("1 lecteur ou éditeur détecté")
        else:
            self._editor_row.set_subtitle(
                f"{count} lecteurs ou éditeurs détectés"
            )

    def _selected_editor(self) -> str:
        index = self._editor_dropdown.get_selected()
        if 1 <= index < len(self._editor_commands):
            return self._editor_commands[index]
        return ""

    def _on_editor_changed(self) -> None:
        self._save_config()

    def _refresh_editors(self) -> None:
        current = self._selected_editor()
        self._editors = editors.detect()
        self._populate_editors(current)
        count = len(self._editors)
        if count == 0:
            self._toast(
                "Aucun lecteur vidéo détecté. Utilise « Choisir… » pour "
                "parcourir les applications installées."
            )
        elif count == 1:
            self._toast("1 lecteur ou éditeur vidéo détecté.")
        else:
            self._toast(f"{count} lecteurs ou éditeurs vidéo détectés.")

    # ------------------------------------------- choix d'une application
    def _on_choose_editor(self, _button) -> None:
        """Ouvre la liste de toutes les applications vidéo installées."""
        applications = editors.video_apps()
        dialog = Adw.Dialog()
        dialog.set_title("Choisir une application vidéo")
        dialog.set_content_width(440)
        dialog.set_content_height(560)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        listbox.set_margin_top(12)
        listbox.set_margin_bottom(12)
        listbox.set_margin_start(12)
        listbox.set_margin_end(12)

        current = self._selected_editor()

        browse_row = Adw.ActionRow(
            title="Autre application…",
            subtitle="Choisir un exécutable ou un fichier .desktop",
        )
        browse_button = Gtk.Button(label="Parcourir…", valign=Gtk.Align.CENTER)
        browse_button.add_css_class("flat")
        browse_row.add_suffix(browse_button)
        browse_row.set_activatable_widget(browse_button)
        browse_button.connect("clicked", lambda *_: self._browse_editor(dialog))
        listbox.append(browse_row)

        if not applications:
            empty = Adw.ActionRow(
                title="Aucune application vidéo déclarée",
                subtitle=(
                    "Utilise « Parcourir… » pour choisir directement un "
                    "programme, par exemple une AppImage."
                ),
            )
            empty.set_activatable(False)
            listbox.append(empty)

        for application in applications:
            row = Adw.ActionRow(title=application["name"])
            icon_name = application.get("icon") or "video-x-generic-symbolic"
            try:
                icon = Gio.icon_new_for_string(icon_name)
                image = Gtk.Image.new_from_gicon(icon)
            except GLib.Error:
                image = Gtk.Image.new_from_icon_name(
                    "video-x-generic-symbolic"
                )
            image.set_pixel_size(24)
            row.add_prefix(image)
            if application["command"] == current:
                check = Gtk.Image.new_from_icon_name("object-select-symbolic")
                check.set_valign(Gtk.Align.CENTER)
                row.add_suffix(check)
            row.set_activatable(True)
            row.connect(
                "activated",
                lambda _row, name=application["name"],
                command=application["command"]: self._apply_editor(
                    name, command, dialog
                ),
            )
            listbox.append(row)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(listbox)
        scroller.set_vexpand(True)
        toolbar.set_content(scroller)
        dialog.set_child(toolbar)
        dialog.present(self)

    def _apply_editor(self, name: str, command: str, dialog) -> None:
        command = command.strip()
        if not command:
            return
        if command not in [candidate for _n, candidate in self._editors]:
            self._editors.append((name, command))
        self._populate_editors(command)
        self._save_config()
        dialog.close()
        self._toast(f"Application choisie : {name}")

    def _browse_editor(self, dialog) -> None:
        file_dialog = Gtk.FileDialog(title="Choisir une application vidéo")
        filters = Gio.ListStore.new(Gtk.FileFilter)

        programs = Gtk.FileFilter()
        programs.set_name("Applications et AppImages")
        for mime in (
            "application/x-executable",
            "application/x-shellscript",
            "application/x-appimage",
            "application/vnd.appimage",
            "application/x-desktop",
        ):
            programs.add_mime_type(mime)
        filters.append(programs)

        everything = Gtk.FileFilter()
        everything.set_name("Tous les fichiers")
        everything.add_pattern("*")
        filters.append(everything)

        file_dialog.set_filters(filters)
        file_dialog.open(
            self,
            None,
            lambda chooser, result: self._on_editor_binary_chosen(
                chooser, result, dialog
            ),
        )

    def _on_editor_binary_chosen(self, chooser, result, dialog) -> None:
        try:
            chosen = chooser.open_finish(result)
        except GLib.Error:
            return
        path = chosen.get_path() if chosen is not None else None
        if not path:
            return
        if path.endswith(".desktop"):
            name = os.path.basename(path)[: -len(".desktop")]
            self._apply_editor(name, f"gtk-launch {name}", dialog)
            return
        self._apply_editor(
            os.path.basename(path), shlex.quote(path), dialog
        )

    def _maybe_offer_editor(self, path: str) -> None:
        if not path or not os.path.isfile(path):
            return
        if not self._editor_prompt_row.get_active():
            return
        command = self._selected_editor()
        if not command:
            return
        name = editors.pretty_command(command)
        self._editor_pending_path = path
        dialog = Adw.AlertDialog(
            heading="Ouvrir la vidéo ?",
            body=f"« {os.path.basename(path)} » est prêt. L'ouvrir avec {name} ?",
        )
        dialog.add_response("later", "Plus tard")
        dialog.add_response("open", "Ouvrir")
        dialog.set_response_appearance(
            "open", Adw.ResponseAppearance.SUGGESTED
        )
        dialog.set_default_response("open")
        dialog.set_close_response("later")
        dialog.connect("response", self._on_editor_response)
        dialog.present(self)

    def _on_editor_response(self, _dialog, response: str) -> None:
        path = self._editor_pending_path
        self._editor_pending_path = None
        if response != "open" or not path:
            return
        command = self._selected_editor()
        try:
            editors.launch(command, path)
        except (OSError, ValueError) as exc:
            self._toast(f"Impossible d'ouvrir le lecteur / éditeur : {exc}")

    # ------------------------------------------------------------- outils
    def _set_status(self, title: str, detail: str) -> None:
        self._status_label.set_text(title)
        self._detail_label.set_text(detail)

    def _set_recording_ui(self, recording: bool) -> None:
        self._page.set_sensitive(not recording)
        if recording:
            self._record_button.set_label("Arrêter")
            self._record_button.remove_css_class("suggested-action")
            self._record_button.add_css_class("destructive-action")
            self._live_badge.set_label(
                "● EN DIRECT" if self._streaming_mode() else "● REC"
            )
            self._live_badge.set_visible(True)
            audio = (
                self._mic_row.get_active()
                or self._system_row.get_active()
                or self._phone_audio_row.get_active()
            )
            self._level_row.set_visible(audio)
        else:
            self._live_badge.set_visible(False)
            self._level_row.set_visible(False)
            self._level_bar.set_value(0.0)
            self._record_button.remove_css_class("destructive-action")
            self._record_button.add_css_class("suggested-action")
            self._update_record_button_label()
            self._clear_preview()
            self._update_camera_sensitivity()

    def _update_timer(self) -> bool:
        if not self._recording:
            self._timer_id = 0
            return GLib.SOURCE_REMOVE

        now = time.monotonic()
        elapsed = now - self._start_time

        if self._output_path:
            size = 0
            try:
                size = os.path.getsize(self._output_path)
            except OSError:
                pass
            detail = f"{_format_duration(elapsed)} · {_format_size(size)}"
        else:
            detail = f"{_format_duration(elapsed)} · En direct"
        self._detail_label.set_text(detail)
        return GLib.SOURCE_CONTINUE

    def _make_output_path(self, force_mp4: bool = False) -> str:
        os.makedirs(self._folder, exist_ok=True)
        index = self._format_dropdown.get_selected()
        extension = "mp4" if force_mp4 else recorder.FORMAT_EXTENSIONS[index]
        template = self._filename_row.get_text().strip()
        if not template:
            template = DEFAULT_FILENAME_TEMPLATE
        try:
            stem = time.strftime(template)
        except (ValueError, OSError):
            stem = time.strftime(DEFAULT_FILENAME_TEMPLATE)
        stem = os.path.basename(stem.replace("/", "-").replace("\\", "-"))
        stem = stem.strip().strip(".")
        for known in recorder.FORMAT_EXTENSIONS:
            suffix = f".{known}"
            if stem.lower().endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        if not stem:
            stem = time.strftime(DEFAULT_FILENAME_TEMPLATE)
        path = os.path.join(self._folder, f"{stem}.{extension}")
        counter = 1
        while os.path.exists(path):
            path = os.path.join(
                self._folder, f"{stem}_{counter}.{extension}"
            )
            counter += 1
        return path

    def _close_pipewire_fd(self) -> None:
        if self._pipewire_fd is not None:
            try:
                os.close(self._pipewire_fd)
            except OSError:
                pass
            self._pipewire_fd = None

    def _on_choose_folder(self, _button) -> None:
        dialog = Gtk.FileDialog(title="Choisir le dossier de destination")
        try:
            dialog.set_initial_folder(Gio.File.new_for_path(self._folder))
        except GLib.Error:
            pass
        dialog.select_folder(self, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if folder is None:
            return
        path = folder.get_path()
        if path:
            self._folder = path
            self._folder_row.set_subtitle(path)
            self._update_disk_row()
            self._save_config()

    def _on_reset_folder(self, _button) -> None:
        self._folder = settings.default_folder()
        self._folder_row.set_subtitle(self._folder)
        self._update_disk_row()
        self._save_config()

    def _open_folder(self) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(
                Gio.File.new_for_path(self._folder).get_uri(), None
            )
        except GLib.Error as exc:
            self._toast(f"Impossible d'ouvrir le dossier : {exc.message}")

    def _open_log(self) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(
                Gio.File.new_for_path(log_path()).get_uri(), None
            )
        except GLib.Error as exc:
            self._toast(f"Journal inaccessible : {exc.message}")

    def _notify(self, name: str, path: str) -> None:
        notification = Gio.Notification.new("Capture enregistrée")
        notification.set_body(path or name)
        notification.set_icon(Gio.ThemedIcon.new(APP_ID))
        target = GLib.Variant("s", path)
        notification.add_button_with_target("Ouvrir", "app.open-file", target)
        notification.add_button("Dossier", "app.open-folder")
        notification.set_default_action_and_target("app.open-file", target)
        self.get_application().send_notification("capture-terminee", notification)

    def _notify_error(self, message: str) -> None:
        notification = Gio.Notification.new("Enregistrement interrompu")
        notification.set_body(message)
        notification.set_icon(Gio.ThemedIcon.new(APP_ID))
        notification.add_button("Afficher", "app.show-window")
        notification.set_default_action("app.show-window")
        self.get_application().send_notification("capture-erreur", notification)

    @staticmethod
    def _humanize_error(error: str) -> str:
        """Transforme les erreurs GStreamer courantes en conseils concrets."""
        lowered = error.casefold()
        if "unauthorized" in lowered or "401" in lowered:
            return (
                "La caméra réseau a refusé l'accès (401 Unauthorized). "
                "Ajoute ?token=… à l'adresse, ou scanne le QR affiché dans "
                "Capturo Webcam."
            )
        if "not found" in lowered or "404" in lowered:
            return (
                "La caméra réseau est introuvable (404). Vérifie l'adresse "
                "et que la diffusion est bien lancée sur le téléphone."
            )
        if "connection refused" in lowered or "could not connect" in lowered:
            return (
                "Connexion à la caméra réseau refusée. Vérifie le partage "
                "USB ou le Wi-Fi, puis l'adresse saisie."
            )
        if "rtmp" in lowered or "flvmux" in lowered:
            return (
                f"Diffusion RTMP refusée : {error}. Vérifie l'URL du serveur "
                "et la clé de diffusion."
            )
        return error

    def open_file(self, path: str) -> None:
        """Ouvre une capture avec le lecteur choisi (ou l'application par défaut)."""
        if not path or not os.path.exists(path):
            self._toast("Le fichier n'existe plus.")
            return
        command = self._selected_editor()
        try:
            if command:
                editors.launch(command, path)
            else:
                Gio.AppInfo.launch_default_for_uri(
                    Gio.File.new_for_path(path).get_uri(), None
                )
        except (OSError, ValueError, GLib.Error) as exc:
            self._toast(f"Impossible d'ouvrir la vidéo : {exc}")

    def _toast(self, message: str) -> None:
        self._toast_overlay.add_toast(Adw.Toast.new(message))

    def _on_close_request(self, _window) -> bool:
        if self._key_save_id:
            GLib.source_remove(self._key_save_id)
            self._key_save_id = 0
        if credentials.available():
            # Enregistre la clé, ou l'efface du trousseau si le champ est vide.
            self._store_rtmp_key()
        self._save_config()
        log(
            "fermeture demandée "
            f"(enregistrement={self._recording}, "
            f"finalisation={self._finalizing})"
        )
        if self._countdown_id:
            self._cancel_countdown()
            return False
        if self._finalizing:
            # Une finalisation est déjà en cours : on la laisse aboutir, le
            # garde-fou fermera la fenêtre même si un élément bloque.
            self._close_pending = True
            if not self._quit_watchdog:
                self._quit_watchdog = GLib.timeout_add(12000, self._force_quit)
            return True
        if self._recording:
            self._close_pending = True
            self._stop_recording()
            if not self._quit_watchdog:
                self._quit_watchdog = GLib.timeout_add(12000, self._force_quit)
            return True
        self._close_session(self._portal)
        self._portal = None
        self._close_pipewire_fd()
        self._closing = True
        return False
