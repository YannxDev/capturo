# Flatpak

Manifeste de test pour Capturo. Il réutilise le runtime GNOME et les
extensions GStreamer de Freedesktop pour l'encodage H.264/H.265, l'audio et
le portail ScreenCast.

## Construire

```bash
sudo flatpak install flathub org.gnome.Platform//48 org.gnome.Sdk//48
flatpak-builder --user --install --force-clean build-flatpak \
  flatpak/io.github.yannick.Capturo.json
flatpak run io.github.yannick.Capturo
```

Si `flatpak-builder` n'est pas installé :

```bash
sudo apt install flatpak-builder
```

## Extensions GStreamer

Le runtime GNOME fournit déjà les plugins de base. Pour les encodeurs
VA-API/NVENC, les formats MKV/WebM et certains décodeurs, installe les
extensions correspondant à la version du SDK (ici 24.08) :

```bash
flatpak install flathub org.freedesktop.Platform.GStreamer.gst-plugins-bad//24.08
flatpak install flathub org.freedesktop.Platform.GStreamer.gst-plugins-ugly//24.08
flatpak install flathub org.freedesktop.Platform.GStreamer.gst-plugins-libav//24.08
```

Capturo reste fonctionnel sans ces extensions : il utilise alors le repli
logiciel et les formats disponibles dans le runtime.

## Accès

Le manifeste autorise :

- le portail ScreenCast (capture Wayland/X11) ;
- PulseAudio (micro et son du système) ;
- le réseau (RTMP et caméras réseau) ;
- `~/Vidéos` en lecture/écriture ;
- `~/.config/capturo` et `~/.cache/capturo` ;
- le trousseau via `org.freedesktop.secrets`.

La clé RTMP est stockée dans le trousseau Flatpak quand il est disponible,
sinon dans `config.json` comme sur une installation classique.
