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

## Limites du bac à sable

Le runtime Flatpak ne monte pas les applications de l'hôte :

- `/usr/bin` et `/usr/share/applications` ne sont pas visibles ;
- les exports Flatpak du système (`/var/lib/flatpak/exports/share`) et de
  l'utilisateur ne sont pas montés ;
- `/run/host/share/applications` est absent.

Conséquence : la détection des lecteurs et éditeurs vidéo renvoie une liste
vide, et **Parcourir…** ne permet pas de les sélectionner.

### Remèdes

1. **Version Debian (recommandé)** : elle s'exécute hors sandbox et détecte
   les applications natives, Flatpak et Snap de l'hôte.

   ```bash
   sudo apt install ./capturo_1.6.2_all.deb
   ```

2. **Rester en Flatpak sans choix d'éditeur** : Capturo enregistre
   normalement, mais l'ouverture automatique dans un éditeur reste
   indisponible.

3. **Élargir les permissions (non recommandé)** : un manifeste modifié avec
   `--filesystem=host` et `--talk-name=org.freedesktop.Flatpak` permettrait
   de lister et lancer les applications de l'hôte via
   `flatpak-spawn --host`. Cela donne au bac à sable un accès étendu au
   système et n'est pas le comportement par défaut.
