"""`python3 -m ghost.agents liste | mod [tek|departman] | json [ad] | dene "<metin>"` (paket → __main__)."""
import sys

from ghost.agents import _main

sys.exit(_main(sys.argv[1:]))
