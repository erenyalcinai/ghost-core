"""Köprü döngüsü (Faz 2.9, 2026-10-29): main() üçe bölündü — basla() / tick() / poll().
Bu testler poll()'un bayrak + getUpdates + dispatch zincirini ve tick()'in izolasyonunu
gerçek ağ/Claude olmadan kurar (api, baton, time.sleep taklit)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

from tests import common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import telegram_bridge as tb  # noqa: E402


class _Mgr:
    chat_id = 111

    def __init__(self):
        self.kararlar = []
        self.devir_note = None


def _kopru(owner=111, mgr=None, offset=0):
    k = tb.Kopru(owner, {"model": "sonnet"}, mgr, offset)
    k.vps_karar_verildi = True
    k.prev_owner = tb.ROLE
    return k


class PollZinciri(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._p = [
            mock.patch.object(tb, "OFFSET_FILE", os.path.join(self.d, "offset.txt")),
            mock.patch.object(tb, "OWNER_FILE", os.path.join(self.d, "owner.json")),
            mock.patch.object(tb.time, "sleep", lambda s: None),
            mock.patch.object(tb, "baton_set", lambda *a, **k: None),
            mock.patch.object(tb.kokpit, "outbox_yaz", lambda *a, **k: None),
            mock.patch.object(tb.channels, "son_kanal_ayarla", lambda *a, **k: None),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()

    def test_bayrak_baskasindaysa_getupdates_cagrilmaz(self):
        cagri = []
        with mock.patch.object(tb, "baton_get", lambda: {"owner": "baska", "hb": 0}), \
                mock.patch.object(tb, "api", lambda *a, **k: cagri.append(a) or {"result": []}):
            k = _kopru()
            tb.poll(k)
        self.assertEqual(cagri, [])
        self.assertEqual(k.prev_owner, "baska")

    def test_sahip_mesaji_route_text_e_gider_ve_offset_kalici(self):
        gelen = []
        upd = {"update_id": 41, "message": {"chat": {"id": 111}, "from": {"id": 111}, "text": "selam"}}

        def api(method, **kw):
            if method == "getUpdates":
                return {"result": [upd]}
            return {"result": {}}
        mgr = _Mgr()
        with mock.patch.object(tb, "baton_get", lambda: {"owner": tb.ROLE, "hb": 0}), \
                mock.patch.object(tb, "api", api), \
                mock.patch.object(tb, "route_text", lambda text, m, spoken=False: gelen.append((text, m))), \
                mock.patch.object(tb.governance, "is_frozen", lambda: False), \
                mock.patch.object(tb, "_iletilen_kaynak", lambda m: None):
            k = _kopru(mgr=mgr)
            tb.poll(k)
        self.assertEqual(gelen, [("selam", mgr)])
        self.assertEqual(k.offset, 42)
        with open(tb.OFFSET_FILE) as f:
            self.assertEqual(f.read(), "42")

    def test_yabanci_mesaj_yoksayilir_komut_calismaz(self):
        gelen = []
        upd = {"update_id": 7, "message": {"chat": {"id": 999}, "from": {"id": 999}, "text": "/durum"}}
        with mock.patch.object(tb, "baton_get", lambda: {"owner": tb.ROLE, "hb": 0}), \
                mock.patch.object(tb, "api", lambda m, **kw: {"result": [upd]} if m == "getUpdates" else {}), \
                mock.patch.object(tb, "handle_command", lambda t, m: gelen.append(t)), \
                mock.patch.object(tb, "send", lambda *a, **k: gelen.append(("send", a))):
            k = _kopru(mgr=_Mgr())
            tb.poll(k)
        self.assertEqual(gelen, [])          # ne komut ne cevap: sessiz yoksay
        self.assertEqual(k.offset, 8)        # ama offset ilerler (tekrar işlenmez)

    def test_buton_yetkisiz_ise_karar_uygulanmaz(self):
        kararlar, cevaplar = [], []
        upd = {"update_id": 3, "callback_query": {"id": "cb1", "from": {"id": 5}, "data": "ok:abc"}}
        with mock.patch.object(tb, "baton_get", lambda: {"owner": tb.ROLE, "hb": 0}), \
                mock.patch.object(tb, "api", lambda m, **kw: {"result": [upd]} if m == "getUpdates" else {}), \
                mock.patch.object(tb, "answer_cb", lambda cid, text="": cevaplar.append(text)), \
                mock.patch.object(tb, "_karar_uygula", lambda *a, **k: kararlar.append(a)):
            tb.poll(_kopru(mgr=_Mgr()))
        self.assertEqual(cevaplar, ["Yetkisiz"])
        self.assertEqual(kararlar, [])

    def test_poll_hatasi_sayaci_ve_toparlanma(self):
        sayac = {"n": 0}

        def api(method, **kw):
            sayac["n"] += 1
            if sayac["n"] <= 3:
                raise OSError("timeout")
            return {"result": []}
        with mock.patch.object(tb, "baton_get", lambda: {"owner": tb.ROLE, "hb": 0}), \
                mock.patch.object(tb, "api", api):
            k = _kopru(mgr=_Mgr())
            for _ in range(3):
                tb.poll(k)
            self.assertEqual(k.poll_fail_streak, 3)
            tb.poll(k)
            self.assertEqual(k.poll_fail_streak, 0)


class TickIzolasyonu(unittest.TestCase):
    def test_bir_parca_duserse_digerleri_kosar(self):
        kosan = []

        class _Mod:
            def __init__(self, ad, patla=False):
                self.ad, self.patla = ad, patla

            def tick(self):
                if self.patla:
                    raise RuntimeError("boom")
                kosan.append(self.ad)
        mgr = _Mgr()
        with mock.patch.object(tb, "ROLE", "vps"), \
                mock.patch.object(tb, "monitors", _Mod("monitors", patla=True)), \
                mock.patch.object(tb, "schedule", _Mod("schedule")), \
                mock.patch.object(tb, "goals", _Mod("goals")), \
                mock.patch.object(tb, "kurallar", _Mod("kurallar")), \
                mock.patch.object(tb, "freeze_uygula", lambda m: (_ for _ in ()).throw(RuntimeError("x"))), \
                mock.patch.object(tb, "check_requests", lambda m: kosan.append("istek")), \
                mock.patch.object(tb, "check_kokpit", lambda m: kosan.append("kokpit")):
            mgr.gec_sonuclari_teslim = lambda: kosan.append("gec")
            tb.tick(tb.Kopru(111, {}, mgr, 0))
        self.assertEqual(kosan, ["gec", "istek", "kokpit", "schedule", "goals", "kurallar"])


if __name__ == "__main__":
    unittest.main()
