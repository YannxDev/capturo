"""Permet de lancer l'application avec « python3 -m capturo »."""

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
