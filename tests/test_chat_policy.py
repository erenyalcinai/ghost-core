"""Sohbet oturumu tazeleme politikası testleri.

Eski kural "30 turda bir tazele"ydi ve iki şeyi birden yanlış yapıyordu: kısa
muhabbeti sebepsiz kesiyor, ağır tool kullanan sohbeti ise geç kesiyordu. Bu
testler yeni ölçünün (gerçek bağlam doluluğu + atalet) o iki hatayı da
yapmadığını sabitler.
"""
from tests import common  # noqa: F401
import json
import unittest

from ghost import chat_policy as cp

W = 200000


class CtxTokensTest(unittest.TestCase):
    def test_uc_bilesen_toplanir(self):
        # Cache'ten okunan gövde UCUZ ama BAĞLAMDA — sayılmalı.
        u = {"input_tokens": 10, "cache_read_input_tokens": 18100,
             "cache_creation_input_tokens": 10897, "output_tokens": 66}
        self.assertEqual(cp.ctx_tokens(u), 29007)

    def test_output_sayilmaz(self):
        # output = üretilen yanıt, taşınan bağlam değil.
        self.assertEqual(cp.ctx_tokens({"input_tokens": 100,
                                        "output_tokens": 5000}), 100)

    def test_bos_none_ve_bozuk(self):
        for v in (None, {}, {"input_tokens": None},
                  {"input_tokens": "abc"}, {"cache_read_input_tokens": -5}):
            self.assertEqual(cp.ctx_tokens(v), 0)


class CtxFromTranscriptTest(unittest.TestCase):
    """Gerçek doluluk ölçüsü. Canlı bug (2026-08-18): nihai JSON'daki `usage`
    turun TÜM iterasyonlarının toplamıydı → köprü 671.199 kaydetti, gerçek
    bağlam 74.486'ydı; oturum boş yere tazelenecekti."""

    def _sat(self, tipi, ctx):
        return json.dumps({"type": tipi, "message": {"usage": {
            "input_tokens": 6, "cache_read_input_tokens": ctx - 6,
            "cache_creation_input_tokens": 0, "output_tokens": 900}}})

    def test_son_assistant_satiri_kazanir(self):
        # Bağlam turlar boyunca büyür → güncel doluluk SON satırdadır.
        tail = "\n".join([self._sat("assistant", 20000),
                          self._sat("user", 999999),
                          self._sat("assistant", 74486)])
        self.assertEqual(cp.ctx_from_transcript(tail), 74486)

    def test_toplam_degil_son_deger(self):
        tail = "\n".join(self._sat("assistant", 30000) for _ in range(10))
        self.assertEqual(cp.ctx_from_transcript(tail), 30000)   # 300.000 DEĞİL

    def test_yarim_ilk_satir_atlanir(self):
        # Dosyanın sonundan okuduğumuz için ilk satır kesik gelebilir.
        tail = '{"type":"assist' + "\n" + self._sat("assistant", 51000)
        self.assertEqual(cp.ctx_from_transcript(tail), 51000)

    def test_bos_ve_bozuk(self):
        for v in (None, "", "abc", "{}", '{"type":"assistant"}'):
            self.assertEqual(cp.ctx_from_transcript(v), 0)


class CtxWindowTest(unittest.TestCase):
    def test_cli_penceresi_kullanilir(self):
        mu = {"claude-haiku-4-5": {"contextWindow": 200000}}
        self.assertEqual(cp.ctx_window(mu), 200000)

    def test_birden_fazla_modelde_en_kucuk_baglayici(self):
        mu = {"a": {"contextWindow": 200000}, "b": {"contextWindow": 64000}}
        self.assertEqual(cp.ctx_window(mu), 64000)

    def test_bilinmiyorsa_varsayilan(self):
        for v in (None, {}, {"a": {}}, {"a": {"contextWindow": 0}}):
            self.assertEqual(cp.ctx_window(v), cp.DEFAULT_WINDOW)

    def test_olcum_yoksa_model_adindan(self):
        # CLI modelUsage'ı boş döndürüyor (2026-08-30 canlı ölçüm) —
        # pencere model adından çözülmeli, 200k varsayılanına düşmemeli.
        self.assertEqual(cp.ctx_window({}, model="fable"), 1_000_000)
        self.assertEqual(cp.ctx_window(None, model="claude-opus-5"), 1_000_000)
        self.assertEqual(cp.ctx_window({}, model="sonnet"), 1_000_000)
        self.assertEqual(cp.ctx_window({}, model="claude-haiku-4-5"), 200_000)

    def test_olcum_varsa_model_adini_ezmez(self):
        # Gerçek CLI ölçümü her zaman önceliklidir.
        mu = {"claude-fable-5": {"contextWindow": 500000}}
        self.assertEqual(cp.ctx_window(mu, model="fable"), 500000)

    def test_window_for_model_bilinmeyen_varsayilan(self):
        for m in (None, "", "llama3.2:3b", "gpt-5"):
            self.assertEqual(cp.window_for_model(m), cp.DEFAULT_WINDOW)


class DecideTest(unittest.TestCase):
    """KAPI MANTIĞI — hepsi `auto=True` ile çağrılır.

    Otomatik tazeleme artık VARSAYILAN KAPALI (bkz. ManuelPolitikaTest); ama
    kapıların ölçüsü doğru kalmalı, çünkü aynı mantık `advice` uyarısını üretir
    ve `JARVIS_CHAT_AUTO_ROTATE=1` ile eylem tarafı geri açılabilir."""

    def d(self, *a, **kw):
        kw.setdefault("auto", True)
        return cp.decide(*a, **kw)

    def test_kisa_muhabbet_30_turda_TAZELENMEZ(self):
        # ESKİ DAVRANIŞIN REGRESYONU: 30 tur "tamam/olur" oturumu öldürüyordu.
        d = self.d(ctx_used=12000, window=W, idle_seconds=60, turns=30)
        self.assertFalse(d["rotate"])

    def test_agir_tur_esigi_asinca_tazelenir(self):
        # Tek bir repo taraması bağlamı şişirebilir; tur sayısı 3 olsa bile.
        d = self.d(ctx_used=130000, window=W, idle_seconds=60, turns=3)
        self.assertTrue(d["rotate"])
        self.assertIn("dolu", d["reason"])

    def test_mutlak_tavan_genis_pencerede_baglayici(self):
        # Canlı ölçüm: pencere 1.000.000 çıktı → %60 oranı 600.000 demek olurdu.
        # Mutlak tavan ekonomiyi korur, oran taşmayı korur.
        d = self.d(ctx_used=310000, window=1000000, turns=5)
        self.assertTrue(d["rotate"])
        self.assertIn("mutlak tavan", d["reason"])

    def test_genis_pencerede_tavan_altinda_tazelemez(self):
        self.assertFalse(self.d(74486, 1000000, turns=4)["rotate"])

    def test_dar_pencerede_oran_once_baglar(self):
        # 200k pencerede 130k = %65 → mutlak tavana (300k) hiç gelmeden tazeler.
        d = self.d(130000, 200000, turns=5)
        self.assertIn("dolu", d["reason"])

    def test_esik_tam_sinirda_tazeler(self):
        self.assertTrue(self.d(120000, W, turns=1)["rotate"])
        self.assertFalse(self.d(119999, W, turns=1)["rotate"])

    def test_atalet_soguk_cache_VE_agir_baglam(self):
        # Cache ömrü abonelikte 1 saat; sonrası tam fiyattan yeniden işlenir.
        # Ağır bağlamı öyle sürüklemek pahalı → tazele.
        d = self.d(ctx_used=90000, window=W, idle_seconds=2 * 3600, turns=2)
        self.assertTrue(d["rotate"])
        self.assertIn("cache soğudu", d["reason"])

    def test_soguk_ama_KUCUK_baglam_tazelemez(self):
        # Küçük bağlamı soğuk da olsa taşımak ucuz — sırf ara verdi diye
        # sürekliliği kırma. (Eski düz "8 saat" kuralı bunu kırıyordu.)
        d = self.d(ctx_used=9000, window=W, idle_seconds=3 * 3600, turns=2)
        self.assertFalse(d["rotate"])

    def test_cok_uzun_sessizlik_kucuk_baglamda_da_tazeler(self):
        d = self.d(ctx_used=9000, window=W, idle_seconds=30 * 3600, turns=2)
        self.assertTrue(d["rotate"])

    def test_sicak_cache_agir_baglam_tazelemez(self):
        # 20 dakika ara: cache sıcak, devam etmek ucuz.
        d = self.d(ctx_used=90000, window=W, idle_seconds=1200, turns=2)
        self.assertFalse(d["rotate"])

    def test_emniyet_freni_olcum_yoksa(self):
        # usage okunamıyorsa (yedek motor/eski CLI) ctx=0 kalır → tur tavanı.
        self.assertTrue(self.d(0, W, idle_seconds=0, turns=250)["rotate"])
        self.assertFalse(self.d(0, W, idle_seconds=0, turns=249)["rotate"])

    def test_doluluk_ataletten_once_gelir(self):
        d = self.d(150000, W, idle_seconds=99 * 3600, turns=1)
        self.assertIn("dolu", d["reason"])

    def test_bozuk_pencere_varsayilana_duser(self):
        for w in (0, None, -5):
            self.assertTrue(self.d(150000, w, turns=1)["rotate"])

    def test_sifir_baglam_tazelemez(self):
        # Yeni oturumun ilk mesajı asla tazeleme tetiklememeli.
        self.assertFalse(self.d(0, W, idle_seconds=0, turns=0)["rotate"])

    def test_gerekce_metni_bos_degil(self):
        self.assertTrue(self.d(150000, W, turns=1)["reason"])
        self.assertEqual(self.d(1000, W, turns=1)["reason"], "")



class ManuelPolitikaTest(unittest.TestCase):
    """KARAR 2026-08-18 (Sahip): "mevcut iş varsa sıfırlamamalı veya sıfırlama
    tamamen benim komutuma bağlı olmalı." → otomatik tazeleme İPTAL.

    Kapılar tam da işin derinleştiği anda ateşliyordu (bağlamı şişiren şey zaten
    ağır iş yapmaktır) ve "şu an iş sürüyor mu"nun güvenilir sinyali yok. Bu
    testler eylemin komuta bağlandığını, ölçünün ise kaybolmadığını sabitler."""

    def test_varsayilan_HICBIR_kosulda_tazelemez(self):
        for kw in ({"ctx_used": 999999, "window": W},                  # tavan
                   {"ctx_used": 150000, "window": W},                  # doluluk
                   {"ctx_used": 90000, "window": W,
                    "idle_seconds": 5 * 3600},                         # atalet
                   {"ctx_used": 9000, "window": W,
                    "idle_seconds": 99 * 3600},                        # bayat
                   {"ctx_used": 0, "window": W, "turns": 9999}):       # emniyet
            self.assertFalse(cp.decide(**kw)["rotate"], kw)

    def test_kapi_dolunca_reason_bos_advice_dolu(self):
        # Eylem yok ama sinyal var: köprü bunu yanıt altına uyarı olarak koyar.
        d = cp.decide(150000, W, turns=1)
        self.assertFalse(d["rotate"])
        self.assertEqual(d["reason"], "")
        self.assertIn("dolu", d["advice"])

    def test_kapi_dolmadiysa_advice_de_bos(self):
        # Sakin oturumda uyarı çıkmamalı — bunaltmasın.
        self.assertEqual(cp.decide(12000, W, idle_seconds=60, turns=5)["advice"], "")

    def test_auto_acilinca_eski_davranis_geri_gelir(self):
        # JARVIS_CHAT_AUTO_ROTATE=1 kaçış kapısı: eylem tarafı yeniden çalışır.
        d = cp.decide(150000, W, turns=1, auto=True)
        self.assertTrue(d["rotate"])
        self.assertEqual(d["reason"], d["advice"])

    def test_advice_auto_degerinden_bagimsiz(self):
        # Ölçü tektir: uyarı metni açık/kapalı aynı çıkmalı.
        for kw in ({"ctx_used": 310000, "window": 1000000},
                   {"ctx_used": 90000, "window": W, "idle_seconds": 2 * 3600}):
            self.assertEqual(cp.decide(**kw)["advice"],
                             cp.decide(auto=True, **kw)["advice"])


class FillPercentTest(unittest.TestCase):
    def test_yuzde(self):
        self.assertEqual(cp.fill_percent(100000, W), 50)
        self.assertEqual(cp.fill_percent(0, W), 0)
        self.assertEqual(cp.fill_percent(None, W), 0)
