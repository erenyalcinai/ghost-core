"""ghost/api.py ROTALAR (Faz 3.1 8a): tablo ↔ işleyici ↔ docs ↔ Swift istemci drift'i tek yerde yakalanır."""
import glob
import os
import re
import unittest

from tests import common  # noqa: F401

from ghost import api, config

DOCS = os.path.join(config.BASE, "docs", "kokpit.md")
BASLA, BIT = "<!-- ROTALAR:BASLA -->", "<!-- ROTALAR:BIT -->"


class TestRotaTablosu(unittest.TestCase):
    def test_her_satirin_isleyicisi_var_ve_tekil(self):
        gorulen = set()
        for r in api.ROTALAR:
            self.assertEqual(len(r), 6, r)
            yontem, yol, es, kimlik, isl, aciklama = r
            self.assertIn(yontem, ("GET", "POST"))
            self.assertIn(es, ("tam", "onek"))
            self.assertIn(kimlik, ("acik", "cihaz", "ses"))
            self.assertTrue(callable(getattr(api.H, "_" + isl, None)), "işleyici yok: %s" % isl)
            self.assertNotIn((yontem, yol), gorulen, "yinelenen rota")
            gorulen.add((yontem, yol))
            self.assertTrue(aciklama.strip(), yol)
            if es == "onek":
                self.assertTrue(api._onek(yol).endswith("/"), "önek '/' ile bitmeli: %s" % yol)
            else:
                self.assertNotIn("<", yol)

    def test_tam_eslesme_onekten_once(self):
        """'/v1/masa' tam satırı '/v1/masa/<id>' önekine düşmemeli; önek kendi tam satırını gölgelememeli."""
        self.assertEqual(api.rota_bul("GET", "/v1/masa")[4], "g_masa")
        self.assertEqual(api.rota_bul("GET", "/v1/masa/12")[4], "g_masa_kart")
        self.assertEqual(api.rota_bul("POST", "/v1/masa")[4], "p_masa")
        self.assertEqual(api.rota_bul("POST", "/v1/masa/dosya")[4], "p_masa")
        self.assertEqual(api.rota_bul("GET", "/v1/sahne")[4], "g_sahne")
        self.assertEqual(api.rota_bul("GET", "/v1/sahne/gecmis")[4], "g_sahne_gecmis")
        self.assertIsNone(api.rota_bul("GET", "/v1/masalar"))
        self.assertIsNone(api.rota_bul("GET", "/v1/yok"))
        self.assertIsNone(api.rota_bul("DELETE", "/v1/ping"))
        for r in api.ROTALAR:   # her satır kendi örnek yoluyla kendine düşer
            ornek = r[1] if r[2] == "tam" else api._onek(r[1]) + "1"
            self.assertIs(api.rota_bul(r[0], ornek), r, r[1])

    def test_kimliksiz_uclar_sinirli(self):
        acik = sorted(r[1] for r in api.ROTALAR if r[3] == "acik")
        self.assertEqual(acik, ["/v1/acik/nabiz", "/v1/eslesme", "/v1/ping"],
                         "kimliksiz uç eklemek bilinçli bir karardır — listeyi güncelle")
        self.assertEqual([r[1] for r in api.ROTALAR if r[3] == "ses"], ["/v1/ses/arac/<ad>"])

    def test_ham_govde_uclari_tabloda(self):
        for yol in api.HAM_GOVDE:
            self.assertIsNotNone(api.rota_bul("POST", yol), yol)

    def test_swift_istemcinin_her_yolu_tabloda(self):
        """kokpit/ios'taki her "/v1/…" dizesi bir rotaya düşmeli (uygulama ↔ sunucu drift'i)."""
        yollar = set()
        for f in glob.glob(os.path.join(config.BASE, "kokpit", "ios", "**", "*.swift"), recursive=True):
            with open(f, encoding="utf-8", errors="replace") as fh:
                yollar.update(re.findall(r'"(/v1/[A-Za-z0-9/._-]*)', fh.read()))
        self.assertGreater(len(yollar), 20, "Swift kaynağı bulunamadı mı?")
        for y in sorted(yollar):
            ornek = y + "1" if y.endswith("/") else y
            bulunan = {m for m in ("GET", "POST") if api.rota_bul(m, ornek)}
            self.assertTrue(bulunan, "Swift'te var, tabloda yok: %s" % y)

    @unittest.skipUnless(os.path.exists(DOCS), "docs/kokpit.md yok (açık çekirdek ağacı)")
    def test_docs_tablosu_tablodan_uretilmis(self):
        with open(DOCS, encoding="utf-8") as f:
            d = f.read()
        self.assertIn(BASLA, d)
        self.assertIn(BIT, d)
        blok = d.split(BASLA, 1)[1].split(BIT, 1)[0].strip("\n")
        self.assertEqual(blok, api.rotalar_markdown().strip("\n"),
                         "docs/kokpit.md uç tablosu bayat: `python3 -m ghost.api rotalar` çıktısını işaretler arasına koy")

    def test_markdown_bicimi(self):
        md = api.rotalar_markdown()
        self.assertTrue(md.startswith("| Yöntem | Yol | Kimlik | Ne yapar |\n|---|---|---|---|\n"))
        self.assertEqual(md.count("\n"), len(api.ROTALAR) + 2)


if __name__ == "__main__":
    unittest.main()
