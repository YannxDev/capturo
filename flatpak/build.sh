#!/usr/bin/env bash
# Construit un Flatpak autonome sans flatpak-builder ni SDK.
#
# Le manifeste flatpak/io.github.yannick.Capturo.json reste la référence
# pour Flathub. Ce script produit le même contenu avec uniquement flatpak,
# ce qui évite de télécharger le SDK GNOME (plus d'1 Go).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

APP_ID="io.github.yannick.Capturo"
RUNTIME="org.gnome.Platform//49"
BRANCH="stable"
VERSION=$(python3 -c 'import sys; sys.path.insert(0, "."); from capturo import __version__; print(__version__)')

BUILD="$ROOT/build-flatpak"
APPDIR="$BUILD/appdir"
REPO="$ROOT/dist/flatpak-repo"
BUNDLE="$ROOT/dist/capturo-${VERSION}.flatpak"

command -v flatpak >/dev/null || {
    echo "flatpak est requis." >&2
    exit 1
}
if ! flatpak info "$RUNTIME" >/dev/null 2>&1; then
    echo "Runtime absent : $RUNTIME" >&2
    echo "Installe-le avec : flatpak install flathub $RUNTIME" >&2
    exit 1
fi

echo "→ préparation de $APPDIR"
rm -rf "$BUILD"
mkdir -p \
    "$APPDIR/files/lib/capturo" \
    "$APPDIR/files/bin" \
    "$APPDIR/files/share/applications" \
    "$APPDIR/files/share/icons/hicolor/scalable/apps" \
    "$APPDIR/files/share/metainfo" \
    "$APPDIR/files/share/locale"

install -m 644 capturo/*.py "$APPDIR/files/lib/capturo/"
install -m 755 data/capturo "$APPDIR/files/bin/capturo"
install -m 644 data/"$APP_ID".desktop "$APPDIR/files/share/applications/"
install -m 644 data/"$APP_ID".svg \
    "$APPDIR/files/share/icons/hicolor/scalable/apps/"
install -m 644 data/"$APP_ID".metainfo.xml "$APPDIR/files/share/metainfo/"

if compgen -G "po/*.po" >/dev/null; then
    if ! command -v msgfmt >/dev/null; then
        echo "msgfmt absent : traductions ignorées (installe gettext)." >&2
    else
        for lang in po/*.po; do
            code=$(basename "$lang" .po)
            install -d "$APPDIR/files/share/locale/$code/LC_MESSAGES"
            msgfmt "$lang" -o "$APPDIR/files/share/locale/$code/LC_MESSAGES/capturo.mo"
        done
    fi
fi

cat > "$APPDIR/metadata" <<EOF
[Application]
name=$APP_ID
runtime=org.gnome.Platform/x86_64/49
sdk=org.gnome.Platform/x86_64/49
command=capturo
EOF

echo "→ finalisation des permissions"
flatpak build-finish \
    --command=capturo \
    --share=network --share=ipc \
    --socket=wayland --socket=fallback-x11 --socket=pulseaudio \
    --device=dri \
    --filesystem=xdg-videos \
    --filesystem=xdg-config/capturo:create \
    --filesystem=xdg-cache/capturo:create \
    --talk-name=org.freedesktop.secrets \
    --talk-name=org.freedesktop.portal.Desktop \
    "$APPDIR" >/dev/null

echo "→ export du dépôt"
rm -rf "$REPO"
flatpak build-export --arch=x86_64 "$REPO" "$APPDIR" "$BRANCH"
flatpak build-update-repo "$REPO" >/dev/null

echo "→ création du bundle"
flatpak build-bundle "$REPO" "$BUNDLE" "$APP_ID" "$BRANCH"

ls -lh "$BUNDLE"
echo
echo "Installation : flatpak install --user $BUNDLE"
echo "Lancement    : flatpak run $APP_ID"
