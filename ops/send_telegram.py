#!/usr/bin/env python3
"""stdin'den okuduğu metni sahibe Telegram'dan yollar (4000'lik parçalarla).
Faz 2.9: gönderim ghost/notify.py'de (kokpit dökümü + alarm push'u orada)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ghost import notify  # noqa: E402

if __name__ == "__main__":
    text = sys.stdin.read().strip() or "(boş)"
    if not notify.telegram(text):
        print("gönderilemedi (Telegram ve ntfy)", file=sys.stderr)
        raise SystemExit(1)
