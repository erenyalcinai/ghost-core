"""Site trafiği geçmişi (2026-10-26): her ölçüm Nabız'ın gösterdiği sayı + Mac tünel durumuyla
tek satır olarak saklanır; "Mac bağlıyken artıyor" iddiası veriyle sınanabilsin."""
import json
import os
import unittest

from tests import common  # noqa: F401  (ops yolunu geçici dizine çevirir)
from ghost import config, trafik


class TrafikGecmisTest(unittest.TestCase):
    def setUp(self):
        for ad in (trafik.GECMIS, "tunnel_watch_state.json"):
            try:
                os.remove(os.path.join(config.OPS, ad))
            except FileNotFoundError:
                pass

    def _kayit(self, g7, g1):
        return {"at": "2026-10-26 01:00:00", "toplam_g7": g7, "toplam_g1": g1,
                "siteler": {"a": {"sahip7": 3, "bot7": 1}, "b": {"sahip7": 2, "bot7": 4}}}

    def test_satir_tunel_ile_yazilir_ve_okunur(self):
        with open(os.path.join(config.OPS, "tunnel_watch_state.json"), "w", encoding="utf-8") as f:
            json.dump({"durum": "saglikli"}, f)
        s = trafik.gecmis_yaz(self._kayit(47, 2))
        self.assertEqual(s["tunel"], "saglikli")
        self.assertEqual((s["g7"], s["g1"], s["sahip7"], s["bot7"]), (47, 2, 5, 5))
        trafik.gecmis_yaz(self._kayit(48, 3))
        son = trafik.gecmis_oku(10)
        self.assertEqual([r["g7"] for r in son], [47, 48])

    def test_tunel_dosyasi_yoksa_bos_ama_yazar(self):
        s = trafik.gecmis_yaz(self._kayit(1, 0))
        self.assertEqual(s["tunel"], "")
        self.assertEqual(len(trafik.gecmis_oku()), 1)

    def test_gecmis_yoksa_bos_liste(self):
        self.assertEqual(trafik.gecmis_oku(), [])


if __name__ == "__main__":
    unittest.main()
