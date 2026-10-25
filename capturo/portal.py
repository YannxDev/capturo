"""Client du portail ScreenCast de xdg-desktop-portal.

Le portail affiche la fenêtre de sélection GNOME (écran ou fenêtre), puis
nous transmet un flux PipeWire que GStreamer peut consommer.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gio, GLib

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"


def _unpack(value):
    if isinstance(value, GLib.Variant):
        return value.unpack()
    return value


class ScreenCastSession:
    """Ouvre une session ScreenCast et fournit le flux PipeWire associé."""

    def __init__(
        self,
        on_ready: Callable[["ScreenCastSession", int, int, tuple | None], None],
        on_error: Callable[[str, bool], None],
    ) -> None:
        self._on_ready = on_ready
        self._on_error = on_error
        self._conn: Gio.DBusConnection | None = None
        self._sender = ""
        self._counter = 0
        self._pending: dict[str, tuple[Callable[[dict], None], int]] = {}
        self._session_handle: str | None = None
        self._closed = False

    # ------------------------------------------------------------ cycle
    def start(self) -> None:
        try:
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as exc:
            self._fail(f"Bus de session inaccessible : {exc.message}")
            return

        self._sender = (
            self._conn.get_unique_name().lstrip(":").replace(".", "_")
        )
        self._create_session()

    def close(self) -> None:
        """Ferme la session côté portail (arrête le partage du flux)."""
        handle, self._session_handle = self._session_handle, None
        if handle and self._conn and not self._closed:
            self._closed = True
            try:
                self._conn.call_sync(
                    BUS_NAME,
                    handle,
                    SESSION_IFACE,
                    "Close",
                    None,
                    None,
                    Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                )
            except GLib.Error:
                pass
        self._drop_all()

    # ---------------------------------------------------------- requêtes
    def _create_session(self) -> None:
        def build(token: str):
            return "(a{sv})", (
                {
                    "handle_token": GLib.Variant("s", token),
                    "session_handle_token": GLib.Variant("s", f"{token}s"),
                },
            )

        self._request("CreateSession", build, self._on_created)

    def _select_sources(self) -> None:
        def build(token: str):
            return "(oa{sv})", (
                self._session_handle,
                {
                    "handle_token": GLib.Variant("s", token),
                    "types": GLib.Variant("u", 1 | 2),  # moniteurs + fenêtres
                    "multiple": GLib.Variant("b", False),
                    "cursor_mode": GLib.Variant("u", 2),  # curseur intégré
                },
            )

        self._request("SelectSources", build, self._on_sources_selected)

    def _start_stream(self) -> None:
        def build(token: str):
            return "(osa{sv})", (
                self._session_handle,
                "",
                {"handle_token": GLib.Variant("s", token)},
            )

        self._request("Start", build, self._on_started)

    def _request(self, method: str, build, callback) -> None:
        token = f"capturo{self._counter}"
        self._counter += 1
        path = (
            f"/org/freedesktop/portal/desktop/request/"
            f"{self._sender}/{token}"
        )
        subscription = self._conn.signal_subscribe(
            BUS_NAME,
            REQUEST_IFACE,
            "Response",
            path,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_response,
            token,
        )
        self._pending[token] = (callback, subscription)

        signature, values = build(token)
        self._conn.call(
            BUS_NAME,
            OBJECT_PATH,
            SCREENCAST_IFACE,
            method,
            GLib.Variant(signature, values),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_call_done,
            token,
        )

    # ---------------------------------------------------------- réponses
    def _on_call_done(self, conn, result, token: str) -> None:
        try:
            conn.call_finish(result)
        except GLib.Error as exc:
            if self._drop(token) is not None:
                self._fail(
                    "Le portail de capture d'écran a refusé la demande : "
                    + exc.message
                )

    def _on_response(
        self, _conn, _sender, _path, _iface, _signal, params, token: str
    ) -> None:
        entry = self._drop(token)
        if entry is None:
            return
        callback, _ = entry

        response, results = params.unpack()
        if response == 1:
            self._fail("Sélection annulée.", cancelled=True)
            return
        if response != 0:
            self._fail("La demande a été refusée par le portail.")
            return

        callback(_unpack(results) or {})

    def _on_created(self, results: dict) -> None:
        handle = results.get("session_handle")
        if not handle:
            self._fail("Le portail n'a pas créé de session.")
            return
        self._session_handle = str(handle)
        self._select_sources()

    def _on_sources_selected(self, _results: dict) -> None:
        self._start_stream()

    def _on_started(self, results: dict) -> None:
        streams = _unpack(results.get("streams")) or []
        if not streams:
            self._fail("Aucun flux vidéo n'a été fourni par le portail.")
            return

        stream = streams[0]
        node_id = int(stream[0])
        props = _unpack(stream[1]) or {}
        size = _unpack(props.get("size"))
        if isinstance(size, (list, tuple)) and len(size) == 2:
            size = (int(size[0]), int(size[1]))
        else:
            size = None

        self._open_remote(node_id, size)

    def _open_remote(self, node_id: int, size) -> None:
        try:
            _reply, fd_list = self._conn.call_with_unix_fd_list_sync(
                BUS_NAME,
                OBJECT_PATH,
                SCREENCAST_IFACE,
                "OpenPipeWireRemote",
                GLib.Variant("(oa{sv})", (self._session_handle, {})),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                None,
            )
            fd = fd_list.get(0)
        except GLib.Error as exc:
            self._fail(
                "Impossible d'ouvrir le flux PipeWire : " + exc.message
            )
            return

        self._on_ready(self, node_id, fd, size)

    # ------------------------------------------------------------ outils
    def _drop(self, token: str):
        entry = self._pending.pop(token, None)
        if entry is not None and self._conn is not None:
            self._conn.signal_unsubscribe(entry[1])
        return entry

    def _drop_all(self) -> None:
        for token in list(self._pending):
            self._drop(token)

    def _fail(self, message: str, cancelled: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        self._drop_all()
        if self._session_handle:
            handle, self._session_handle = self._session_handle, None
            try:
                self._conn.call_sync(
                    BUS_NAME,
                    handle,
                    SESSION_IFACE,
                    "Close",
                    None,
                    None,
                    Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                )
            except GLib.Error:
                pass
        self._on_error(message, cancelled)
