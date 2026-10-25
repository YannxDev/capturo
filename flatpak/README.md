# Flatpak

Manifeste de test pour Capturo. Il réutilise le runtime GNOME 49 et les
extensions GStreamer de Freedesktop pour l'encodage H.264/H.265, l'audio et
le portail ScreenCast.

## Construire

```bash
sudo flatpak install flathub org.gnome.Platform//49 org.gnome.Sdk//49
flatpak-builder --user --install --force-clean build-flatpak \
  flatpak/io.github.yannick.Capturo.json
flatpak run io.github.yannick.Capturo
```

Si `flatpak-builder` n'est pas installé :

```bash
sudo apt install flatpak-builder
```

## Construire sans SDK

Le SDK GNOME pèse plus d'un gigaoctet. Pour produire rapidement un bundle
avec le seul runtime Platform déjà installé :

```bash
./flatpak/build.sh
flatpak install --user dist/capturo-1.6.2.flatpak
flatpak run io.github.yannick.Capturo
```

Le script installe les fichiers dans un répertoire d'application, applique
les permissions, puis utilise `flatpak build-export` et `flatpak
build-bundle`. Le manifeste JSON reste la référence pour Flathub.

## Extensions GStreamer

Le runtime GNOME fournit déjà les plugins de base. Pour les encodeurs
VA-API/NVENC, les formats MKV/WebM et certains décodeurs, installe les
extensions correspondant à la version du SDK (ici 25.08) :

```bash
flatpak install flathub org.freedesktop.Platform.GStreamer.gst-plugins-bad//25.08
flatpak install flathub org.freedesktop.Platform.GStreamer.gst-plugins-ugly//25.08
flatpak install flathub org.freedesktop.Platform.GStreamer.gst-plugins-libav//25.08
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
