"""Masa — ortak kalıcı içerik alanı (2026-10-04): depo, izin kuralları, yayın anlık görüntüsü,
Sahip→Ghost akışı ve API uçları."""
import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests import common  # noqa: F401

from ghost import api, config, kokpit, masa  # noqa: E402


def _temizle():
    import sqlite3
    c = sqlite3.connect(config.DB_PATH)
    c.executescript("DROP TABLE IF EXISTS masa_kart; DROP TABLE IF EXISTS masa_meta;")
    c.commit()
    c.close()


class Depo(unittest.TestCase):
    def setUp(self):
        _temizle()

    def test_hazirla_kok_klasorler_sabit_ve_tekrar_zararsiz(self):
        r = masa.hazirla()
        self.assertEqual(r["acilan"], list(masa.KOK_KLASORLER))
        self.assertEqual(masa.hazirla()["acilan"], [])
        kok = masa.cocuklar(None)
        self.assertTrue(all(k["sabit"] and k["tur"] == "klasor" for k in kok))
        self.assertNotIn("dosya", kok[0])       # sunucu yolu dışarı çıkmaz

    def test_yayinla_anlik_goruntu_ve_guncelleme(self):
        p = os.path.join(config.OPS, "rapor.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# AppA inceleme\n\n| a | b |\n|--|--|\n| 1 | 2 |\n")
        kid, ne = masa.yayinla(p, "AppA")
        self.assertEqual(ne, "yeni")
        k = masa.oku(kid)
        self.assertEqual((k["tur"], k["ad"], k["yazar"]), ("rapor", "AppA inceleme", "ghost"))
        self.assertIn("rapor.md", k["kaynak"])
        with open(p, "a", encoding="utf-8") as f:
            f.write("\nek satır\n")
        kid2, ne2 = masa.yayinla(p, "AppA")
        self.assertEqual((kid2, ne2), (kid, "guncel"))               # çift kopya yok
        self.assertIn("ek satır", masa.oku(kid)["icerik"])
        # Sahip kaydı başka klasöre taşıdıysa yayın onu orada yeniler
        masa.kart_ekle("klasor", "Arşiv", None, yazar="sahip")
        ars = masa.klasor_bul("Arşiv")
        masa.tasi(kid, ars, "sahip")
        kid3, _ = masa.yayinla(p, "AppA")
        self.assertEqual(kid3, kid)
        self.assertEqual(masa.oku(kid)["ebeveyn"], ars)
        # kaynak dosya değişmedi: Masa yayın katmanı, çalışma ağacı değil
        with open(p, encoding="utf-8") as f:
            self.assertTrue(f.read().endswith("ek satır\n"))

    def test_dosya_ekle_kopya_ve_sil_dosyayi_kaldirir(self):
        v = masa.klasor_bul("AppA", olustur=True)
        kid = masa.dosya_ekle("foto.jpg", b"\xff\xd8\xff\xe0abc", v, yazar="sahip")
        k = masa.oku(kid)
        self.assertEqual((k["tur"], k["mime"], k["boyut"]), ("gorsel", "image/jpeg", 7))
        yol, mime, ad = masa.dosya_yol(kid)
        self.assertTrue(os.path.isfile(yol))
        self.assertEqual(masa.sil(kid, "sahip"), "silindi")
        self.assertFalse(os.path.exists(yol))
        self.assertIsNone(masa.oku(kid))

    def test_tasi_dongu_ve_hedef_klasor_sarti(self):
        a = masa.kart_ekle("klasor", "A", None)
        b = masa.kart_ekle("klasor", "B", a)
        n = masa.not_ekle(a, "merhaba", "ghost")
        with self.assertRaises(masa.MasaHata):
            masa.tasi(a, b, "ghost")                  # kendi içine
        with self.assertRaises(masa.MasaHata):
            masa.tasi(b, n, "ghost")                  # hedef not, klasör değil
        masa.tasi(n, b, "ghost")
        self.assertEqual([x["id"] for x in masa.yol(n)], [a, b, n])

    def test_seq_her_degisiklikte_artar_ve_bekle(self):
        s0 = masa.seq()
        masa.kart_ekle("klasor", "X", None)
        self.assertEqual(masa.seq(), s0 + 1)
        t = time.time()
        self.assertEqual(masa.bekle(s0 + 1, 0.6), s0 + 1)
        self.assertGreaterEqual(time.time() - t, 0.5)
        self.assertEqual(masa.bekle(s0, 5), s0 + 1)   # değişmiş: hemen döner

    def test_agac_klasor_ozetleri(self):
        a = masa.kart_ekle("klasor", "A", None)
        masa.not_ekle(a, "n1", "ghost")
        masa.not_ekle(a, "n2", "sahip")
        ag = masa.agac()
        kl = next(k for k in ag if k["id"] == a)
        self.assertEqual(kl["cocuk_sayisi"], 2)
        self.assertTrue(kl["son_icerik"])
        self.assertEqual(next(k for k in ag if k["ad"] == "n1")["onizleme"], "n1")


class Izinler(unittest.TestCase):
    """Yapı bozulmaz: Sahip Ghost'un yayınını düzenleyemez/silemez (dolaba kalkar), kök sabit;
    Ghost sahibin notuna istenmeden dokunmaz."""
    def setUp(self):
        _temizle()
        masa.hazirla()
        self.v = masa.klasor_bul("AppA")
        self.g = masa.not_ekle(self.v, "Ghost raporu", "ghost")
        self.e = masa.not_ekle(self.v, "Sahip notu", "sahip")

    def test_sahip_ghost_kaydini_duzenleyemez_silemez(self):
        with self.assertRaises(masa.MasaHata) as cm:
            masa.duzenle(self.g, "sahip", icerik="değiştirdim")
        self.assertTrue(cm.exception.yetki)
        self.assertEqual(masa.sil(self.g, "sahip"), "dolap")          # silme → dolap
        self.assertEqual(masa.oku(self.g)["durum"], "dolap")
        self.assertEqual(masa.oku(self.g)["icerik"], "Ghost raporu")
        masa.geri(self.g, "sahip")
        self.assertEqual(masa.oku(self.g)["durum"], "masa")

    def test_sahip_kendi_kaydini_duzenler_siler_her_seyi_adlandirir_tasir(self):
        masa.duzenle(self.e, "sahip", icerik="güncel", ad="Yeni ad")
        self.assertEqual((masa.oku(self.e)["icerik"], masa.oku(self.e)["ad"]), ("güncel", "Yeni ad"))
        masa.duzenle(self.g, "sahip", ad="Ghost raporu (okudum)")       # adlandırma serbest
        d = masa.klasor_bul("App B")
        masa.tasi(self.g, d, "sahip")
        self.assertEqual(masa.oku(self.g)["ebeveyn"], d)
        self.assertEqual(masa.sil(self.e, "sahip"), "silindi")

    def test_kok_klasor_sabit(self):
        for eylem in (lambda: masa.dolap(self.v, "sahip"), lambda: masa.sil(self.v, "sahip"),
                      lambda: masa.tasi(self.v, masa.klasor_bul("Ghost"), "sahip")):
            with self.assertRaises(masa.MasaHata):
                eylem()
        masa.duzenle(self.v, "sahip", ad="AppA (appa)")
        self.assertEqual(masa.oku(self.v)["ad"], "AppA (appa)")

    def test_ghost_eren_kaydina_istenmeden_dokunmaz(self):
        with self.assertRaises(masa.MasaHata):
            masa.duzenle(self.e, "ghost", icerik="x")
        with self.assertRaises(masa.MasaHata):
            masa.sil(self.e, "ghost")
        self.assertEqual(masa.sil(self.e, "ghost", zorla=True), "silindi")

    def test_sahip_klasor_silme_ghost_kaydi_varsa_reddedilir(self):
        k = masa.kart_ekle("klasor", "Karışık", None, yazar="sahip")
        masa.not_ekle(k, "ghost'un", "ghost")
        with self.assertRaises(masa.MasaHata):
            masa.sil(k, "sahip")
        self.assertIsNotNone(masa.oku(k))


class SahipAkisi(unittest.TestCase):
    def setUp(self):
        _temizle()
        masa.hazirla()

    def test_yeni_ve_baglam_satiri(self):
        self.assertEqual(masa.baglam_satiri(), "")
        v = masa.klasor_bul("AppA")
        masa.not_ekle(v, "Şunu düşündüm", "sahip")
        masa.not_ekle(v, "ghost notu", "ghost")                    # Ghost'un kendi: listeye girmez
        y = masa.yeni()
        self.assertEqual([k["ad"] for k in y], ["Şunu düşündüm"])
        self.assertEqual(y[0]["yol"], "AppA")
        self.assertIn("1 değişikliği", masa.baglam_satiri())
        self.assertIn("ghost.masa yeni", masa.baglam_satiri())
        masa.yeni(okundu=True)
        self.assertEqual(masa.yeni(), [])
        self.assertEqual(masa.baglam_satiri(), "")
        masa.duzenle(y[0]["id"], "sahip", icerik="düzelttim")       # Sahip dokundu → yeniden görünmez
        self.assertEqual(len(masa.yeni()), 1)

    def test_ilet_sohbete_eren_mesaji_olarak_duser(self):
        v = masa.klasor_bul("AppA")
        n = masa.not_ekle(v, "Bunu değerlendir", "sahip")
        m = masa.ilet(n, "cihazX")
        self.assertIn("[Masa › AppA › Bunu değerlendir]", m)
        inbox = kokpit.inbox_al()
        self.assertTrue(any("Bunu değerlendir" in x["metin"] for x in inbox))
        self.assertEqual(masa.yeni(), [])                           # iletilen görülmüş sayılır


def _istek(base, method, path, key, did, body=None, ham=None, ct=None):
    data = ham if ham is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": ct or "application/json"}
    ts = str(int(time.time()))
    nonce = os.urandom(8).hex()
    h.update({"X-Ghost-Device": did, "X-Ghost-Ts": ts, "X-Ghost-Nonce": nonce,
              "X-Ghost-Sig": kokpit.imza(key, ts, nonce, method, path, data or b"")})
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ic = r.read()
            return r.status, (json.loads(ic.decode()) if r.headers.get("Content-Type", "").startswith("application/json") else ic)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _temizle()
        masa.hazirla()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), api.H)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        cls.did, cls.key = kokpit.cihaz_ekle("masa-test")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _g(self, path):
        return _istek(self.base, "GET", path, self.key, self.did)

    def _p(self, path, body=None, **kw):
        return _istek(self.base, "POST", path, self.key, self.did, body, **kw)

    def test_uctan_uca_eren_akisi(self):
        st, j = self._g("/v1/masa")
        self.assertEqual(st, 200)
        v = next(k for k in j["kartlar"] if k["ad"] == "AppA")
        seq0 = j["seq"]
        st, j = self._p("/v1/masa", {"tur": "not", "ad": "Fikir", "ebeveyn": v["id"], "icerik": "paywall rengi"})
        self.assertEqual(st, 200)
        nid = j["id"]
        self.assertGreater(j["seq"], seq0)
        st, j = self._p("/v1/masa/dosya?ad=ekran.png&ebeveyn=%d" % v["id"],
                        ham=b"\x89PNG\r\n\x1a\nxx", ct="image/png")
        self.assertEqual(st, 200)
        did_ = j["id"]
        st, j = self._g("/v1/masa/%d" % v["id"])
        self.assertEqual(st, 200)
        self.assertEqual([c["ad"] for c in j["cocuklar"]], ["Fikir", "ekran.png"])
        self.assertEqual(j["yol"], [{"id": v["id"], "ad": "AppA"}])
        st, ic = self._g("/v1/masa/%d/dosya" % did_)
        self.assertEqual((st, ic[:4]), (200, b"\x89PNG"))
        # düzenle, taşı, dolap, geri, sil
        self.assertEqual(self._p("/v1/masa/%d" % nid, {"eylem": "duzenle", "icerik": "paywall rengi: koyu"})[0], 200)
        self.assertEqual(self._g("/v1/masa/%d" % nid)[1]["icerik"], "paywall rengi: koyu")
        d = next(k for k in self._g("/v1/masa")[1]["kartlar"] if k["ad"] == "App B")
        self.assertEqual(self._p("/v1/masa/%d" % nid, {"eylem": "tasi", "ebeveyn": d["id"]})[0], 200)
        self.assertEqual(self._p("/v1/masa/%d" % nid, {"eylem": "dolap"})[0], 200)
        st, j = self._g("/v1/masa?dolap=1")
        self.assertIn(nid, [k["id"] for k in j["kartlar"]])          # (başka test de dolaba koymuş olabilir)
        self.assertGreaterEqual(self._g("/v1/masa")[1]["dolap_sayisi"], 1)
        self.assertEqual(self._p("/v1/masa/%d" % nid, {"eylem": "geri"})[0], 200)
        st, j = self._p("/v1/masa/%d" % nid, {"eylem": "sil"})
        self.assertEqual((st, j["sonuc"]), (200, "silindi"))
        # ghost tarafı: sahibin yeni kayıtları (dosya) listede
        self.assertIn("ekran.png", [k["ad"] for k in masa.yeni()])

    def test_izin_hatasi_403_ve_sabit(self):
        v = next(k for k in self._g("/v1/masa")[1]["kartlar"] if k["ad"] == "AppA")
        g = masa.not_ekle(v["id"], "ghost yayını", "ghost")
        st, j = self._p("/v1/masa/%d" % g, {"eylem": "duzenle", "icerik": "x"})
        self.assertEqual(st, 403)
        st, j = self._p("/v1/masa/%d" % g, {"eylem": "sil"})
        self.assertEqual((st, j["sonuc"]), (200, "dolap"))
        st, _ = self._p("/v1/masa/%d" % v["id"], {"eylem": "sil"})
        self.assertEqual(st, 403)
        st, _ = self._p("/v1/masa", {"tur": "rapor", "ad": "x", "icerik": "y"})   # uygulama rapor yayınlamaz
        self.assertEqual(st, 400)
        st, _ = self._p("/v1/masa/999", {"eylem": "dolap"})
        self.assertEqual(st, 400)
        self.assertEqual(self._g("/v1/masa/999")[0], 404)
        self.assertEqual(self._g("/v1/masa/abc")[0], 404)

    def test_sahne_kaydi_bilesen_olarak_gelir(self):
        kid = masa.sahne_kaydet([{"tur": "tablo", "sutunlar": ["a"], "satirlar": [["1"]]}], "Tablo", "Raporlar")
        st, j = self._g("/v1/masa/%d" % kid)
        self.assertEqual(st, 200)
        self.assertEqual(j["bilesenler"][0]["tur"], "tablo")
        self.assertIsNone(j["icerik"])

    def test_uzun_yoklama_degisince_doner(self):
        seq0 = self._g("/v1/masa")[1]["seq"]
        sonuc = {}

        def arka():
            sonuc["r"] = self._g("/v1/masa?since=%d&bekle=5" % seq0)
        t = threading.Thread(target=arka)
        t.start()
        time.sleep(0.3)
        masa.kart_ekle("klasor", "Yeni", None, yazar="sahip")
        t.join(6)
        self.assertEqual(sonuc["r"][1]["seq"], seq0 + 1)


if __name__ == "__main__":
    unittest.main()
