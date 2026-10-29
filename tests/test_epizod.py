"""ghost/epizod.py — döküm okuma, özet kaydı, tick, arama (Faz 1.3, 2026-10-29)."""
import json
import os
import tempfile
import time
import unittest

from tests import common  # noqa: F401

from ghost import epizod


def _dokum(yol, turlar, sidechain_tool=True):
    with open(yol, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "queue-operation"}) + "\n")
        for i, (rol, metin) in enumerate(turlar):
            if rol == "user":
                o = {"type": "user", "timestamp": "2026-10-29T0%d:00:00Z" % (i % 9), "isSidechain": False,
                     "message": {"content": metin}}
            else:
                o = {"type": "assistant", "timestamp": "2026-10-29T0%d:00:01Z" % (i % 9),
                     "message": {"content": [{"type": "text", "text": metin}, {"type": "tool_use", "name": "Bash", "input": {}}]}}
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
        if sidechain_tool:
            f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "çöp"}]}}) + "\n")
    eski = time.time() - 3600
    os.utime(yol, (eski, eski))


KOSUCU = lambda istem: '{"ozet": "Sahip AppA fiyatını sordu, Ghost D1 sayısını verdi.", "kararlar": ["AppA fiyatı değişmez"], "acik_sorular": ["TestFlight ne zaman?"], "varliklar": ["AppA", "D1"], "vadeler": [{"ad": "deneme", "tarih": "2026-11-05"}, {"ad": "bozuk", "tarih": "yarın"}]}'


class TestEpizod(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="dokum-")
        epizod.DOKUM_DIZIN = self.d
        c, _ = epizod._conn()
        c.execute("DELETE FROM epizod")
        c.commit()
        c.close()
        self.sid = "aaaaaaaa-1111-2222-3333-444444444444"
        self.yol = os.path.join(self.d, self.sid + ".jsonl")
        _dokum(self.yol, [("user", "[SAHİBİN BAĞLAMI (telefon): konum Van] appa fiyatı ne"),
                          ("assistant", "D1'de 0 ödeyen var."), ("user", "tamam\n\n[Ghost kalıcı hafızasından ilgili kayıtlar — x:\n- y]"),
                          ("assistant", "Not aldım."), ("user", "testflight ne zaman"), ("assistant", "Beklemede.")] + [("user", "x" * 900)])

    def test_dokum_oku_temizler(self):
        d = epizod.dokum_oku(self.yol)
        self.assertEqual(d["kullanici"], 4)
        self.assertEqual(d["ghost"], 3)
        self.assertEqual(d["turlar"][0], ("Sahip", "appa fiyatı ne"))
        self.assertEqual(d["turlar"][2], ("Sahip", "tamam"))
        self.assertEqual(d["tur"], "sohbet")
        m = epizod.dokum_metni(d)
        self.assertIn("Sahip: appa fiyatı ne", m)
        self.assertNotIn("çöp", m)
        self.assertIn("…", m)            # 900 karakterlik tur kesildi

    def test_ozetle_ve_ara(self):
        i = epizod.ozetle(self.yol, kosucu=KOSUCU)
        self.assertIsNotNone(i)
        self.assertEqual(epizod.ozetle(self.yol, kosucu=lambda s: "ASLA"), i)   # ikinci kez model çağrılmaz
        e = epizod.oku(i)
        self.assertEqual(e["kullanici_turu"], 4)
        self.assertEqual(json.loads(e["vadeler"]), [{"ad": "deneme", "tarih": "2026-11-05"}])  # bozuk tarih düştü
        self.assertEqual(epizod.ara("appa fiyat")[0]["id"], i)
        self.assertEqual(epizod.ara("selam"), [])

    def test_tick_bekleyen_ve_kucuk(self):
        kucuk = os.path.join(self.d, "bbbbbbbb-0000-0000-0000-000000000000.jsonl")
        _dokum(kucuk, [("user", "merhaba " * 300), ("assistant", "selam")])
        taze = os.path.join(self.d, "cccccccc-0000-0000-0000-000000000000.jsonl")
        _dokum(taze, [("user", "a" * 700), ("assistant", "b"), ("user", "c"), ("assistant", "d"), ("user", "e")])
        os.utime(taze, None)                                   # az önce değişti → henüz bitmedi
        bekleyen = epizod.bekleyen_dokumler()
        self.assertEqual({os.path.basename(p)[:8] for p in bekleyen}, {"aaaaaaaa", "bbbbbbbb"})
        ids, atl = epizod.tick(kosucu=KOSUCU)
        self.assertEqual((len(ids), atl), (1, 1))
        self.assertEqual(epizod.bekleyen_dokumler(), [])      # ikisi de işaretlendi
        self.assertEqual(len(epizod.liste()), 1)              # küçük listede görünmez

    def test_bozuk_cikti_kayit_yazmaz(self):
        with self.assertRaises(ValueError):
            epizod.ozetle(self.yol, kosucu=lambda s: "özet yok")
        self.assertFalse(epizod.var_mi(self.sid))

    def test_ozerk_oturum_is_sayilir(self):
        yol = os.path.join(self.d, "dddddddd-0000-0000-0000-000000000000.jsonl")
        _dokum(yol, [("user", "AUTONOMOUS_GUARD: nöbetçi turu"), ("assistant", "ok"), ("user", "x"), ("assistant", "y"), ("user", "z")])
        self.assertEqual(epizod.dokum_oku(yol)["tur"], "is")


if __name__ == "__main__":
    unittest.main()
