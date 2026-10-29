"""ghost/durum.py + ghost/semalar.py — durum tablosu, atomik güncelleme, json göçü, şema sürümü (Faz 1.9, 2026-10-29)."""
import json
import os
import tempfile
import threading
import unittest

from tests import common  # noqa: F401

from ghost import durum, semalar


class TestDurum(unittest.TestCase):
    def setUp(self):
        c = semalar.baglan()
        c.execute("DELETE FROM durum")
        c.commit()
        c.close()

    def test_surum_ve_tablo(self):
        c = semalar.baglan()
        self.assertEqual(semalar.surum(c), semalar.SURUM)
        self.assertEqual(semalar.goc_uygula(c), [])          # ikinci kez boş
        c.close()

    def test_oku_yaz_sil_liste(self):
        self.assertIsNone(durum.oku("yok"))
        self.assertEqual(durum.oku("yok", {"a": 1}), {"a": 1})
        durum.yaz("metrik.sayac", {"2026-10-29": {"x": 2}})
        self.assertEqual(durum.oku("metrik.sayac")["2026-10-29"]["x"], 2)
        durum.yaz("metrik.sayac", [1, 2])
        self.assertEqual(durum.oku("metrik.sayac"), [1, 2])
        self.assertEqual([r["ad"] for r in durum.liste("metrik.")], ["metrik.sayac"])
        self.assertIsNotNone(durum.ts("metrik.sayac"))
        self.assertTrue(durum.sil("metrik.sayac"))
        self.assertFalse(durum.sil("metrik.sayac"))

    def test_guncelle_atomik(self):
        durum.yaz("sayac", 0)
        def art(_):
            for _i in range(50):
                durum.guncelle("sayac", lambda v: (v or 0) + 1, 0)
        ts = [threading.Thread(target=art, args=(i,)) for i in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(durum.oku("sayac"), 200)

    def test_tasi_json_dosyasi(self):
        d = tempfile.mkdtemp(prefix="durum-")
        yol = os.path.join(d, "eski.json")
        json.dump({"gun": "2026-10-29", "n": 3}, open(yol, "w"))
        self.assertEqual(durum.tasi("eski", yol)["n"], 3)
        self.assertFalse(os.path.exists(yol))
        self.assertTrue(os.path.exists(yol + ".tasindi"))
        durum.yaz("eski", {"n": 9})
        self.assertEqual(durum.tasi("eski", yol)["n"], 9)        # tabloda varsa dosyaya bakılmaz
        self.assertEqual(durum.tasi("hic", os.path.join(d, "yok.json"), {"bos": True}), {"bos": True})

    def test_cli(self):
        self.assertEqual(durum._main(["yaz", "cli.test", '{"a": 1}']), 0)
        self.assertEqual(durum._main(["oku", "cli.test"]), 0)
        self.assertEqual(durum._main(["liste", "cli."]), 0)
        self.assertEqual(durum._main(["sil", "cli.test"]), 0)


if __name__ == "__main__":
    unittest.main()
