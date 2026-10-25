#!/usr/bin/env python3
"""Test GStreamer sans écran : enregistre 2 s avec videotestsrc.

Utilisé par la CI GitHub Actions. Aucun portail, aucune caméra et aucune
sortie audio réelle ne sont nécessaires.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

Gst.init(None)

from capturo import recorder  # noqa: E402


def main() -> int:
    output = os.path.join(tempfile.gettempdir(), "capturo-ci.mp4")
    try:
        os.unlink(output)
    except OSError:
        pass

    description = recorder.build_pipeline(
        fd=0,
        node_id=0,
        source_size=(640, 360),
        output_path=output,
        format_id="mp4",
        quality_index=2,
        fps=24,
        resolution="Native",
        mic=True,
        system_audio=False,
        mic_source="audiotestsrc is-live=true wave=sine",
        mic_volume=80,
        system_volume=100,
        audio_delay_ms=40,
        encoder_mode=recorder.ENCODER_SOFTWARE,
        fragmented_mp4=True,
        preview=False,
    )
    description = description.replace(
        "pipewiresrc fd=0 path=0 do-timestamp=true",
        "videotestsrc is-live=true pattern=smpte",
    )

    loop = GLib.MainLoop()
    levels: list[float] = []
    result: dict[str, object] = {"error": "timeout"}

    def finished(error: str | None) -> None:
        result["error"] = error
        loop.quit()

    def on_level(peak: float) -> None:
        levels.append(peak)

    instance = recorder.Recorder(finished, on_level)
    instance.start(description)
    started = time.monotonic()
    GLib.timeout_add(1800, lambda: (instance.stop(), False)[-1])
    GLib.timeout_add(15000, lambda: (loop.quit(), False)[-1])
    loop.run()

    if result["error"] is not None:
        print(f"échec du pipeline : {result['error']}", file=sys.stderr)
        return 1
    if time.monotonic() - started > 14:
        print("délai de finalisation dépassé", file=sys.stderr)
        return 1
    ok, message = recorder.verify_file(output)
    if not ok:
        print(f"fichier invalide : {message}", file=sys.stderr)
        return 1
    if not levels:
        print("aucun message de niveau audio reçu", file=sys.stderr)
        return 1
    print(
        f"OK · {os.path.getsize(output)} octets · "
        f"{len(levels)} mesures audio · {output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
