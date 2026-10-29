"""Vekil üst bağlantısı IPv4 önce + kısa kurulum zaman aşımı (409 avı, 2026-10-29).

Kök neden: api.telegram.org IPv6 SYN'leri ara ara kara deliğe düşüyordu; vekil AAAA'yı önce deniyor ve 300 sn
bekliyordu → köprü vazgeçip yeni getUpdates açıyor → yetim üst istek 409 Conflict + BrokenPipe üretiyordu.
Bu testler ağ olmadan `_baglan`ın sıralamasını ve `_https`'in bağlantıyı ondan aldığını sabitler."""
from tests import common  # noqa: F401
import importlib.util
import os
import socket
import unittest
from unittest import mock

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_YOL = os.environ.get("VEKIL_SERVISI_YOL") or os.path.join(_BASE, "ops", "vekil", "vekil_servisi.py")
_spec = importlib.util.spec_from_file_location("vekil_servisi_ipv4", _YOL)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("149.154.166.110", 443))
V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:67c:4e8:f004::9", 443, 0, 0))


class SahteSoket:
    acilan = []
    dusuk = {socket.AF_INET6}          # bu aileler bağlanamaz

    def __init__(self, aile, tur, proto):
        self.aile, self.kapandi, self.zaman = aile, False, None
        SahteSoket.acilan.append(aile)

    def settimeout(self, z):
        self.zaman = z

    def connect(self, sa):
        if self.aile in SahteSoket.dusuk:
            raise socket.timeout("kara delik")

    def close(self):
        self.kapandi = True


class Baglan(unittest.TestCase):
    def setUp(self):
        SahteSoket.acilan.clear()
        SahteSoket.dusuk = {socket.AF_INET6}

    def test_v4_varsa_v6_hic_denenmez(self):
        s = srv._baglan("api.telegram.org", 443, gai=lambda *a: [V6, V4], soket=SahteSoket)
        self.assertEqual(s.aile, socket.AF_INET)
        self.assertEqual(SahteSoket.acilan, [socket.AF_INET])          # AAAA önce gelse de v4 ilk ve tek
        self.assertEqual(s.zaman, srv.BAGLANTI_ZAMAN_ASIMI)
        self.assertLess(srv.BAGLANTI_ZAMAN_ASIMI, 15)                  # köprünün 14 sn'sinden önce vazgeçmeli

    def test_v4_dusukse_v6ya_duser(self):
        SahteSoket.dusuk = {socket.AF_INET}
        s = srv._baglan("x", 443, gai=lambda *a: [V6, V4], soket=SahteSoket)
        self.assertEqual(s.aile, socket.AF_INET6)
        self.assertEqual(SahteSoket.acilan, [socket.AF_INET, socket.AF_INET6])

    def test_hepsi_dusukse_son_hata_yukselir_soketler_kapanir(self):
        SahteSoket.dusuk = {socket.AF_INET, socket.AF_INET6}
        with self.assertRaises(OSError):
            srv._baglan("x", 443, gai=lambda *a: [V6, V4], soket=SahteSoket)
        self.assertEqual(len(SahteSoket.acilan), 2)

    def test_adres_yoksa_oserror(self):
        with self.assertRaises(OSError):
            srv._baglan("x", 443, gai=lambda *a: [], soket=SahteSoket)

    def test_https_baglantiyi_baglan_kurar(self):
        with mock.patch.object(srv.http.client, "HTTPSConnection") as HC, \
                mock.patch.object(srv, "_baglan", return_value="HAM") as b, \
                mock.patch.object(srv.ssl, "create_default_context") as cdc:
            sarili = mock.Mock()
            cdc.return_value.wrap_socket.return_value = sarili
            r = HC.return_value.getresponse.return_value
            r.status, r.getheaders.return_value, r.read.return_value = 200, [], b"{}"
            r.getheader.side_effect = {"Content-Type": "application/json", "Content-Length": "2"}.get
            durum, bas, veri = srv.Vekil._https("api.telegram.org", "POST", "/x", {}, b"")
        b.assert_called_once_with("api.telegram.org", 443)
        cdc.return_value.wrap_socket.assert_called_once_with("HAM", server_hostname="api.telegram.org")
        self.assertIs(HC.return_value.sock, sarili)                      # connect() çağrılmaz, soket bizden
        sarili.settimeout.assert_called_with(srv.ZAMAN_ASIMI)
        self.assertEqual((durum, veri), (200, b"{}"))

    def test_tls_ac_de_baglan_kullanir(self):
        with mock.patch.object(srv, "_baglan", return_value="HAM") as b, \
                mock.patch.object(srv.ssl, "create_default_context") as cdc:
            cdc.return_value.wrap_socket.return_value = mock.Mock()
            srv._tls_ac("imap.gmail.com", 993)
        b.assert_called_once_with("imap.gmail.com", 993, 60)


if __name__ == "__main__":
    unittest.main()
