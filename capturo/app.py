"""Point d'entrée de Capturo."""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")

from gi.repository import Adw, Gio, GLib, Gst, Gtk  # noqa: E402

from . import APP_ID, __version__  # noqa: E402
from . import i18n  # noqa: E402
from .diagnostics import log  # noqa: E402
from .window import CapturoWindow  # noqa: E402


class CapturoApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.window = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        i18n.setup()
        Gst.init(None)
        log(f"Capturo {__version__} démarré")

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._on_quit)
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        self.set_accels_for_action("win.record", ["<Primary>r"])
        self.set_accels_for_action("win.cancel-countdown", ["Escape"])

        stop_action = Gio.SimpleAction.new("stop-recording", None)
        stop_action.connect("activate", self._on_stop_recording)
        self.add_action(stop_action)

        show_action = Gio.SimpleAction.new("show-window", None)
        show_action.connect("activate", self._on_show_window)
        self.add_action(show_action)

        open_file_action = Gio.SimpleAction.new(
            "open-file", GLib.VariantType.new("s")
        )
        open_file_action.connect("activate", self._on_open_file)
        self.add_action(open_file_action)

        open_folder_action = Gio.SimpleAction.new("open-folder", None)
        open_folder_action.connect("activate", self._on_open_folder)
        self.add_action(open_folder_action)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

    def do_activate(self) -> None:
        if self.window is None:
            Gtk.Window.set_default_icon_name(APP_ID)
            self.window = CapturoWindow(application=self)
        self.window.present()

    def _on_quit(self, *_args) -> None:
        # Passer par close-request permet de finaliser un enregistrement en
        # cours au lieu de quitter brutalement le pipeline.
        log("action Quitter demandée")
        if self.window is not None:
            self.window.close()
        else:
            self.quit()

    def _on_stop_recording(self, *_args) -> None:
        log("action Arrêter (notification) demandée")
        if self.window is not None:
            self.window._stop_recording()

    def _on_show_window(self, *_args) -> None:
        if self.window is not None:
            self.window._restore_window()
            self.window.present()

    def _on_open_file(self, _action, parameter) -> None:
        if self.window is not None and parameter is not None:
            self.window.open_file(parameter.get_string())

    def _on_open_folder(self, *_args) -> None:
        if self.window is not None:
            self.window._open_folder()

    def _on_about(self, *_args) -> None:
        dialog = Adw.AboutDialog(
            application_name="Capturo",
            application_icon=APP_ID,
            developer_name="Yannick",
            version=__version__,
            comments=(
                "Enregistreur et diffuseur pour GNOME : MP4/H.264, MKV/H.265 "
                "et WebM/VP9, streaming RTMP, aperçu en direct, webcam et "
                "microphone."
            ),
            license_type=Gtk.License.GPL_3_0,
            developers=["Yannick"],
        )
        dialog.present(self.window)


def main(argv: list[str] | None = None) -> int:
    app = CapturoApp()
    return app.run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
