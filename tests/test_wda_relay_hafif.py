"""Mac rölesinin hafif MJPEG kipi (2026-09-06): kare ayrıştırma, hız sınırı, çıktı çerçevesi.
sips Linux'ta yok → _kucult orijinali döndürür; çerçeve mantığı yine sınanır."""
import os
import re
import sys
import time
import unittest

os.environ.setdefault("GHOST_TEST", "1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import wda_relay  # noqa: E402

JPEG = b"\xff\xd8" + b"x" * 500 + b"\xff\xd9"


def akis(kareler):
    out = b""
    for k in kareler:
        out += b"--BoundaryString\r\nContent-type: image/jpg\r\nContent-Length: %d\r\n\r\n" % len(k) + k + b"\r\n"
    return out


class Parcalayici(unittest.TestCase):
    def test_kareler_parca_parca_gelse_de(self):
        veri = b"HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=--BoundaryString\r\n\r\n" + akis([JPEG, JPEG + b"ek"])
        parcalar = [veri[i:i + 97] for i in range(0, len(veri), 97)] + [b""]
        oku = lambda: parcalar.pop(0)
        self.assertEqual(list(wda_relay._kareler(oku)), [JPEG, JPEG + b"ek"])


class Sahte:
    def __init__(self, veri):
        self.veri, self.yazilan = veri, b""
    def recv(self, n):
        p, self.veri = self.veri[:n], self.veri[n:]
        return p
    def sendall(self, b):
        self.yazilan += b


class Yayin(unittest.TestCase):
    def test_hiz_siniri_ve_cerceve(self):
        eski = wda_relay.HAFIF_FPS
        wda_relay.HAFIF_FPS = float("inf")      # bu testte kare düşürme yok
        try:
            uc = Sahte(akis([JPEG] * 3))
            ist = Sahte(b"")
            n = wda_relay._hafif_yayin(uc, ist)
        finally:
            wda_relay.HAFIF_FPS = eski
        self.assertEqual(n, 3)
        self.assertTrue(ist.yazilan.startswith(b"HTTP/1.0 200 OK\r\nContent-Type: multipart/x-mixed-replace"))
        self.assertEqual(len(re.findall(rb"Content-Length: (\d+)\r\n\r\n", ist.yazilan)), 3)
        # çıktı yine ayrıştırılabilir olmalı (Kokpit canli.telefon_kare aynı regex'i kullanır)
        govde = ist.yazilan.split(b"\r\n\r\n", 1)[1]
        parcalar = [govde, b""]
        self.assertEqual(list(wda_relay._kareler(lambda: parcalar.pop(0)))[0][:2], b"\xff\xd8")

    def test_fps_siniri_kare_dusurur(self):
        eski = wda_relay.HAFIF_FPS
        wda_relay.HAFIF_FPS = 2.0
        try:
            uc = Sahte(akis([JPEG] * 6))       # hepsi aynı anda gelir → ilkinden sonra hepsi düşer
            ist = Sahte(b"")
            n = wda_relay._hafif_yayin(uc, ist)
        finally:
            wda_relay.HAFIF_FPS = eski
        self.assertEqual(n, 1)

    def test_kucult_sips_yoksa_orijinal(self):
        self.assertEqual(wda_relay._kucult(JPEG), JPEG)


if __name__ == "__main__":
    unittest.main()
