#!/usr/bin/env bash
#
# Construit le paquet Debian de Capturo dans dist/.
# Usage : ./build.sh
#
set -euo pipefail

APP_ID="io.github.yannick.Capturo"
PKG="capturo"
VERSION="1.6.2"
ARCH="all"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$SRC_DIR/build"
STAGE="$BUILD_DIR/${PKG}_${VERSION}_${ARCH}"
DIST="$SRC_DIR/dist"

echo "==> Nettoyage"
rm -rf "$BUILD_DIR"
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/usr/bin" \
         "$STAGE/usr/lib/$PKG" \
         "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps" \
         "$STAGE/usr/share/metainfo" \
         "$STAGE/usr/share/doc/$PKG" \
         "$STAGE/usr/share/locale"

echo "==> Copie de l'application"
install -m 644 "$SRC_DIR/capturo/"*.py "$STAGE/usr/lib/$PKG/"
install -m 755 "$SRC_DIR/data/$PKG" "$STAGE/usr/bin/$PKG"

echo "==> Ressources (menu, icône, AppStream)"
install -m 644 "$SRC_DIR/data/$APP_ID.desktop" \
    "$STAGE/usr/share/applications/$APP_ID.desktop"
install -m 644 "$SRC_DIR/data/$APP_ID.svg" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"
install -m 644 "$SRC_DIR/data/$APP_ID.metainfo.xml" \
    "$STAGE/usr/share/metainfo/$APP_ID.metainfo.xml"

echo "==> Documentation"
install -m 644 "$SRC_DIR/packaging/copyright" "$STAGE/usr/share/doc/$PKG/copyright"
gzip -9 -n -c "$SRC_DIR/packaging/changelog" \
    > "$STAGE/usr/share/doc/$PKG/changelog.gz"

echo "==> Traductions"
shopt -s nullglob
for po_file in "$SRC_DIR"/po/*.po; do
    language="$(basename "$po_file" .po)"
    install -d "$STAGE/usr/share/locale/$language/LC_MESSAGES"
    msgfmt "$po_file" -o "$STAGE/usr/share/locale/$language/LC_MESSAGES/$PKG.mo"
done
shopt -u nullglob

echo "==> Scripts de maintenance"
install -m 755 "$SRC_DIR/packaging/postinst" "$STAGE/DEBIAN/postinst"
install -m 755 "$SRC_DIR/packaging/postrm" "$STAGE/DEBIAN/postrm"

echo "==> Fichier control"
find "$STAGE" -type d -exec chmod 755 {} +
SIZE=$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)
sed -e "s/@VERSION@/$VERSION/" -e "s/@SIZE@/$SIZE/" \
    "$SRC_DIR/packaging/control" > "$STAGE/DEBIAN/control"

echo "==> Construction du paquet"
mkdir -p "$DIST"
dpkg-deb --root-owner-group -Zxz --build "$STAGE" \
    "$DIST/${PKG}_${VERSION}_${ARCH}.deb"

echo
echo "Paquet créé : $DIST/${PKG}_${VERSION}_${ARCH}.deb"
echo
dpkg-deb --info "$DIST/${PKG}_${VERSION}_${ARCH}.deb"
echo
echo "Installation   : sudo apt install $DIST/${PKG}_${VERSION}_${ARCH}.deb"
echo "Désinstallation: sudo apt remove $PKG"
