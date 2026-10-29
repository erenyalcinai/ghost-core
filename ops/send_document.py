#!/usr/bin/env python3
"""Bir dosyayı sahibe Telegram'dan yollar (sendDocument).

Kullanım: python3 ops/send_document.py <dosya_yolu> ["opsiyonel açıklama"]
Ekran görüntüsüne sığmayan çıktıları (HTML, PDF, log) böyle iletir.
Faz 2.9: multipart gönderim + kokpit dökümü ghost/notify.medya'da (tek yer)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ghost import notify  # noqa: E402

if __name__ == "__main__":
    path = sys.argv[1]
    caption = sys.argv[2] if len(sys.argv) > 2 else ""
    resp = notify.medya("document", path, caption)
    print("gönderildi" if resp.get("ok") else "hata: %s" % resp)
