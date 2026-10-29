#!/usr/bin/env python3
"""Bir video dosyasını sahibe Telegram'dan yollar (sendVideo).

Kullanım: python3 ops/send_video.py <video_yolu> ["opsiyonel açıklama"]
Ghost ürettiği videoları (ör. sosyal medya postları) böyle gösterir.
Faz 2.9: multipart gönderim + kokpit dökümü ghost/notify.medya'da (tek yer)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ghost import notify  # noqa: E402

if __name__ == "__main__":
    path = sys.argv[1]
    caption = sys.argv[2] if len(sys.argv) > 2 else ""
    resp = notify.medya("video", path, caption)
    print("gönderildi" if resp.get("ok") else "hata: %s" % resp)
