"""Bekleyen karar sayısı açık sahne onay kartlarını da sayar (Kokpit Watch hazırlığı, 2026-11-24).
Kural `health.kart_sec` (saf işlev) + başındaki not; buradaki her test onun bir maddesidir. Yanlış "bekliyor"
(kapanmış kartın sayılması) eksik sayımdan kötüdür: emin olunamayan durumda kart SAYILMAZ.

Kural testleri dökümü taklit eden satır demetleriyle koşar (gerçek döküme sahibinin ağzından satır yazılmaz);
uçtan uca testler kartı gerçek `sahne.onay_komut` ile açar."""
import time
import unittest
from unittest import mock

from tests import common  # noqa: F401  (yolları geçici dizine alır, GHOST_TEST=1)

from ghost import health, kokpit, onay, sahne  # noqa: E402

SIMDI = time.mktime(time.strptime("2026-11-24 12:00:00", "%Y-%m-%d %H:%M:%S"))
SAHIP = "sahip"
A, B = "onay-" + "a" * 16, "onay-" + "b" * 16


def _ts(dakika_once):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(SIMDI - dakika_once * 60))


def kart(rid, eid, dakika_once=5, baslik="Siteyi yayınla"):
    """Onay kartının döküm satırı (sahne.onay'ın yazdığı biçim)."""
    dugme = '[[["Onayla", "[Sahne eylem: %s] onayla"], ["Vazgeç", "[Sahne eylem: %s] vazgeç"]]]' % (eid, eid)
    return (rid, _ts(dakika_once), "ghost", baslik + health._KART_EK, dugme)


def yazdi(rid, metin, dakika_once=1):
    return (rid, _ts(dakika_once), SAHIP, metin, None)


def cevap(rid, eid, ne="onayla"):
    return yazdi(rid, "[Sahne eylem: %s] %s" % (eid, ne))


def say(satirlar, sahnede=(), **kw):
    return health.kart_sec(satirlar, set(sahnede), SIMDI, **kw)


class Kural(unittest.TestCase):
    def test_acik_kart_sayilir_baslik_en_yeninin(self):
        self.assertEqual(say([]), {"n": 0, "baslik": "", "ts": "", "kartlar": []})
        d = say([kart(1, A, baslik="Köprü yaması uygulansın")], [A])
        self.assertEqual((d["n"], d["baslik"]), (1, "Köprü yaması uygulansın"))      # döküm eki başlıktan düşer
        d = say([kart(1, A, 9, "eski"), kart(2, B, 3, "Mail gönderimi — ornek@example.com")], [B])
        self.assertEqual([k["id"] for k in d["kartlar"]], [B, A])                     # Sahip henüz yazmadı: ikisi de bekliyor
        self.assertEqual((d["baslik"], d["ts"]), ("Mail gönderimi — ornek@example.com", _ts(3)))

    def test_dugme_cevabi_kapatir_onay_da_vazgec_de(self):
        self.assertEqual(say([kart(1, A), cevap(2, A)], [])["n"], 0)
        self.assertEqual(say([kart(1, A), cevap(2, A, "vazgeç")], [])["n"], 0)
        d = say([kart(1, A), kart(2, B), cevap(3, A)], [B])                           # A cevaplandı, B Sahne'de düğmeli
        self.assertEqual([k["id"] for k in d["kartlar"]], [B])
        self.assertEqual(say([kart(1, A), cevap(2, A)], [A])["n"], 0)                 # Sahne bayat kalsa da cevap kesindir

    def test_duz_metin_cevap_sahneden_dusen_karti_kapatir(self):
        # düz "evet"/"vazgeç" Sahne'deki düğmeleri düşürür (sahne.cozumle_serbest) → kart Sahne'de değil + Sahip yazmış
        self.assertEqual(say([kart(1, A), yazdi(2, "evet")], [])["n"], 0)
        self.assertEqual(say([kart(1, A), yazdi(2, "vazgeç, önce şunu düzelt")], [])["n"], 0)

    def test_tartisma_mesaji_sahnedeki_karti_yarim_saat_kapatmaz(self):
        d = say([kart(1, A), yazdi(2, "bence önce yedek alalım")], [A])               # düğmeler hâlâ Sahne'de, kart 5 dk'lık
        self.assertEqual(d["n"], 1)
        self.assertEqual(say([kart(1, A, 29), yazdi(2, "bence önce yedek alalım")], [A])["n"], 1)

    def test_sahnede_kalan_terk_edilmis_kart_yarim_saatten_sonra_sayilmaz(self):
        """Bağımsız inceleme bulgu 3: "boşver, başka yoldan yapalım" hayır kalıbına uymaz, düğme Sahne'de kalır;
        süre sınırı yokken saat yüzü 24 saat "1 bekliyor" gösterirdi."""
        satirlar = [kart(1, A, 31), yazdi(2, "boşver, başka yoldan yapalım", 28)]
        self.assertEqual(say(satirlar, [A])["n"], 0)
        self.assertEqual(say([kart(1, A, 600)], [A])["n"], 1)                         # Sahip hiç yazmadıysa yaş sınırı 24 saat
        self.assertEqual(say([kart(1, A, 600), cevap(2, B)], [A])["n"], 0)            # başka karta düğme cevabı da "Sahip yazdı"dır

    def test_terk_edilen_kart_sayilmaz(self):
        """Sahip karttan sonra başka şey yazdı ve kart Sahne'de değil: bekliyor DENMEZ (ölçüm: düğmesiz kalan 23 kartın
        hepsi böyle terk edilmişti; sayılsaydı her biri 24 saat boyunca dürterdi — 30 günde 552 saat)."""
        self.assertEqual(say([kart(1, A), yazdi(2, "konuyu değiştir, ikinci paragrafı at")], [])["n"], 0)

    def test_ustune_cizilmis_ama_eren_gormemis_kart_sayilir(self):
        satirlar = [yazdi(1, "iyi geceler", 600), kart(2, A, 300, "gece açılan kart"),
                    (3, _ts(200), "ghost", "İş #1 bitti", None)]
        self.assertEqual(say(satirlar, [])["n"], 1)                                   # Sahne'de değil ama Sahip o günden beri yazmadı

    def test_yirmi_dort_saati_gecen_ve_yasi_okunamayan_kart_sayilmaz(self):
        self.assertEqual(say([kart(1, A, 24 * 60 - 1)], [A])["n"], 1)
        self.assertEqual(say([kart(1, A, 24 * 60 + 1)], [A])["n"], 0)
        self.assertEqual(say([kart(1, A, -5)], [A])["n"], 0)                          # gelecekten damga
        self.assertEqual(say([(1, "bozuk damga", "ghost", "x", kart(1, A)[4])], [A])["n"], 0)
        self.assertEqual(say([(1, None, "ghost", "x", kart(1, A)[4])], [A])["n"], 0)

    def test_onay_kaydi_kapali_diyorsa_sayilmaz_ve_yalniz_adaya_sorulur(self):
        sorulan = []

        def kapali(eid):
            sorulan.append(eid)
            return eid == A
        d = say([kart(1, A), kart(2, B), kart(3, "onay-" + "c" * 16, 25 * 60)], [A, B], kapali=kapali)
        self.assertEqual([k["id"] for k in d["kartlar"]], [B])
        self.assertEqual(sorted(sorulan), [A, B])                                     # süresi geçmiş kart için servis yorulmaz

    def test_soru_dugmesi_kopru_karari_ve_baska_kimlik_kart_degil(self):
        satirlar = [(1, _ts(2), "ghost", "Hangisi?", '[[["a", "a"], ["b", "b"]]]'),
                    (2, _ts(2), "ghost", "iş onayı", '[[["Onayla", "/onay 12"], ["Vazgeç", "/red 12"]]]'),
                    (3, _ts(2), "ghost", "soru", '[[["Evet", "[Sahne eylem: soru-1234] evet"]]]'),
                    (4, _ts(2), "ghost", "kısa kimlik", '[[["Onayla", "[Sahne eylem: onay-abc] onayla"]]]')]
        self.assertEqual(say(satirlar, ["soru-1234", "onay-abc"])["n"], 0)

    def test_sahip_metninde_gecen_dugme_metni_kart_acmaz(self):
        # sahibin satırındaki düğme metni kart sayılmaz (kart yalnız Ghost'un düğmeli satırıdır)
        self.assertEqual(say([(1, _ts(2), SAHIP, "bak şu ne", kart(1, A)[4])], [A])["n"], 0)


class UctanUca(unittest.TestCase):
    """Gerçek kart (sahne.onay_komut → döküm satırı + Sahne düğmesi + yerel onay kaydı) ile okuma yolu."""

    def setUp(self):
        self._sifirla()
        self.addCleanup(self._sifirla)

    @staticmethod
    def _sifirla():
        c = kokpit._db()
        c.execute("DELETE FROM kokpit_outbox")
        c.commit()
        c.close()
        sahne.temizle()
        health._kart_onbellek.update(ts=0.0, deger=None)

    def test_kart_acilir_sayilir_onbellek_ve_hata(self):
        self.assertEqual(health.onay_kartlari(taze=True)["n"], 0)
        eid = sahne.onay_komut("Köprü yaması", "git apply dossier/x.patch")
        d = health.onay_kartlari(taze=True)
        self.assertEqual((d["n"], d["baslik"], d["kartlar"][0]["id"]), (1, "Köprü yaması", eid))
        self.assertEqual(health._sahnedeki_dugmeler(), {eid})
        sahne.temizle()                                                                # Sahne'den düştü ama Sahip yazmadı
        self.assertEqual(health.onay_kartlari(taze=True)["n"], 1)
        with mock.patch.object(kokpit, "_db", mock.Mock(side_effect=RuntimeError("db kilitli"))):
            self.assertEqual(health.onay_kartlari()["n"], 1)                           # 5 sn önbellek: veritabanına gidilmez
            self.assertEqual(health.onay_kartlari(taze=True)["n"], 0)                  # okunamayan = kart yok (asla fırlatmaz)
        self.assertEqual(health.onay_kartlari(taze=True, simdi=time.time() + health.ONAY_KART_OMUR_SN + 60)["n"], 0)

    def test_yerel_onay_kaydi_onaylanmis_diyorsa_sayilmaz(self):
        eid = sahne.onay_komut("Deploy", "./deploy.sh --yayin")
        self.assertFalse(health._kart_kapali(eid))
        with mock.patch.object(sahne, "_onaylar", lambda: {eid: {"ts": time.time(), "kanal": "telegram"}}):
            self.assertTrue(health._kart_kapali(eid))
            self.assertEqual(health.onay_kartlari(taze=True)["n"], 0)

    def test_servis_kipinde_kart_durumlari(self):
        """Onay servisi kuruluyken (üretim): tanımadığı, onaylanmış dediği ya da cevap vermediği kart sayılmaz."""
        eid = sahne.onay_komut("Deploy", "./deploy.sh --yayin")
        istenen = []

        def kos(cevap_):
            with mock.patch.object(onay, "servis_modu", lambda: True), mock.patch.object(onay, "_cagir", cevap_):
                return health.onay_kartlari(taze=True)["n"]

        def bekliyor(op, **kw):
            istenen.append((op, kw.get("eid")))
            return {"ok": True, "sha": "x" * 64, "tur": "kapi", "gecerli": False}
        self.assertEqual(kos(bekliyor), 1)
        self.assertEqual(istenen, [("onizleme", eid)])                                 # yalnız okuma işlemi, yalnız aday kart
        self.assertEqual(kos(lambda op, **kw: {"ok": True, "sha": "x", "gecerli": True}), 0)     # onaylanmış, koşmayı bekliyor
        self.assertEqual(kos(lambda op, **kw: {"ok": True, "sha": None, "gecerli": False}), 0)   # servis kartı tanımıyor
        self.assertEqual(kos(mock.Mock(side_effect=onay.ServisHatasi("soket yok"))), 0)          # servis düşük: eksik say

    def test_nabiz_alanlari(self):
        sahne.onay_komut("Köprü yaması", "git apply dossier/x.patch")
        bekleyen_is = {"7": {"text": "site raporu", "kind": "is", "ts": "2026-11-24 10:00:00"}}
        asil = health._json
        with mock.patch.object(health, "_json", lambda ad, v=None: bekleyen_is if ad.startswith("pending") else asil(ad, v)):
            i = health._isler()
        self.assertEqual((i["bekleyen_onay"], i["bekleyen_karar"], i["bekleyen_kart"]), (2, 1, 1))
        with mock.patch.object(kokpit, "bekleyen_kararlar", lambda: [{"id": "7"}]):
            b = health._bekleyen()
        self.assertEqual([k["id"] for k in b["kararlar"]], ["7"])                     # liste yalnız köprü kararları (uygulama öyle çizer)
        self.assertEqual((b["onay_kartlari"]["n"], b["onay_kartlari"]["baslik"]), (1, "Köprü yaması"))
        self.assertEqual(set(b["onay_kartlari"]), {"n", "baslik", "ts"})               # kart kimliği nabızda taşınmaz
        self.assertEqual(b["toplam"], 2)
        with mock.patch.object(kokpit, "bekleyen_kararlar", mock.Mock(side_effect=RuntimeError("x"))):
            self.assertEqual(health._bekleyen()["toplam"], 0)


if __name__ == "__main__":
    unittest.main()
