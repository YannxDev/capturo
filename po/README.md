# Traductions de Capturo

Capturo utilise gettext. Le français est la langue source ; les autres
langues se trouvent dans `po/<code>.po`.

## Ajouter une langue

```bash
msginit --locale=de --input=po/capturo.pot --output=po/de.po
# traduire de.po puis :
msgfmt po/de.po -o locale/de/LC_MESSAGES/capturo.mo
```

Le paquet Debian installe automatiquement les `.mo` présents dans `locale/`
ou générés depuis `po/` par `build.sh`. En Flatpak, le manifeste compile
les `po/*.po` dans `/app/share/locale`.

## Extraire les chaînes

Les chaînes traduisibles sont marquées avec `_()` :

```python
from .i18n import _

label = _("Enregistrer")
```

Pour régénérer le modèle :

```bash
xgettext --language=Python --keyword=_ --from-code=UTF-8 \
  -o po/capturo.pot capturo/*.py
```

Sans fichier `.mo`, Capturo affiche les chaînes françaises d'origine.
