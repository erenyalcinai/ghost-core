"""YubiKey kapısı (ops/mac_anahtar.py) + üç ajanın kancası. Mac'e dokunmaz:
ioreg çıktısı sentetik, hızlı hat ucu geçici portta yerel olarak kalkar."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from tests import common  # noqa: F401

from ops import mac_anahtar as ma, mac_agent, mac_worker  # noqa: E402

IOREG = '''+-o Root  <class IORegistryEntry, id 0x100000100, retain 30>
  +-o AppleT8112USBXHCI@00000000  <class AppleT8112USBXHCI, id 0x1000002f5, registered, matched, active, busy 0 (0 ms), retain 82>
    {
      "IOClass" = "AppleT8112USBXHCI"
    }
    +-o USB2.0 Hub@00100000  <class IOUSBHostDevice, id 0x10000a1c0, registered, matched, active, busy 0 (25 ms), retain 30>
    | {
    |   "USB Vendor Name" = "GenesysLogic"
    |   "idVendor" = 1507
    |   "USB Serial Number" = "ABC"
    | }
    +-o YubiKey OTP+FIDO+CCID@00200000  <class IOUSBHostDevice, id 0x10000a1c9, registered, matched, active, busy 0 (49 ms), retain 27>
      {
        "USB Vendor Name" = "Yubico"
        "idVendor" = 4176
        "USB Serial Number" = "31415926"
        "USB Product Name" = "YubiKey OTP+FIDO+CCID"
      }
'''
IOREG_SERISIZ = IOREG.replace('"USB Serial Number" = "31415926"\n', '')


class Ayristirici(unittest.TestCase):
    def test_yubico_serisi_bulunur_digerleri_atlanir(self):
        self.assertEqual(ma.ayristir(IOREG), ["31415926"])

    def test_seri_gizliyse_soru_isareti(self):
        self.assertEqual(ma.ayristir(IOREG_SERISIZ), ["?"])

    def test_bos_cikti(self):
        self.assertEqual(ma.ayristir(""), [])


class Kapi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.seri = os.path.join(self.tmp, "anahtar_seri")
        self.p1 = mock.patch.object(ma, "SERI_DOSYA", self.seri)
        self.p2 = mock.patch.object(ma, "_ioreg", return_value=IOREG)
        self.p1.start(); self.p2.start()
        ma._ONBELLEK.update(t=0.0, v=None)

    def tearDown(self):
        self.p1.stop(); self.p2.stop()
        ma._ONBELLEK.update(t=0.0, v=None)

    def test_dosya_yoksa_devre_disi_ve_acik(self):
        d = ma.durum(taze=True)
        self.assertEqual(d["kapi"], "devre_disi")
        self.assertTrue(ma.acik(taze=True))

    def test_bagla_takili_seriyi_secer_ve_acik(self):
        d = ma.bagla()
        self.assertEqual((d["kapi"], d["gerekli"]), ("acik", "31415926"))
        self.assertEqual(oct(os.stat(self.seri).st_mode & 0o777), "0o600")

    def test_bagla_takili_olmayan_seri_reddedilir(self):
        self.assertIn("hata", ma.bagla("99999999"))
        self.assertFalse(os.path.exists(self.seri))

    def test_anahtar_cikinca_kilitli_takinca_acik(self):
        ma.bagla("31415926")
        with mock.patch.object(ma, "_ioreg", return_value=""):
            self.assertEqual(ma.durum(taze=True)["kapi"], "kilitli")
            self.assertFalse(ma.acik(taze=True))
        self.assertEqual(ma.durum(taze=True)["kapi"], "acik")

    def test_yildiz_herhangi_yubico(self):
        ma.bagla("*")
        self.assertTrue(ma.acik(taze=True))
        with mock.patch.object(ma, "_ioreg", return_value=""):
            self.assertFalse(ma.acik(taze=True))

    def test_coz_devre_disi_yapar(self):
        ma.bagla("31415926")
        self.assertEqual(ma.coz()["kapi"], "devre_disi")


class Isci(unittest.TestCase):
    """Kilitliyken poll_once VPS'e hiç dokunmaz (iş dosyaları yerinde kalır)."""
    def setUp(self):
        self.iz = os.path.join(tempfile.mkdtemp(), "kilit")
        self.p = mock.patch.object(mac_worker, "KILIT_IZ", self.iz)
        self.p.start()
        self.logs = []
        self.pl = mock.patch.object(mac_worker, "log", self.logs.append)
        self.pl.start()

    def tearDown(self):
        self.p.stop(); self.pl.stop()

    def test_kilitliyken_ssh_yok_ve_tek_log(self):
        with mock.patch.object(mac_worker.mac_anahtar, "acik", return_value=False), \
             mock.patch.object(mac_worker, "ssh", side_effect=AssertionError("ssh çağrılmamalı")):
            self.assertFalse(mac_worker.poll_once())
            self.assertFalse(mac_worker.poll_once())
        self.assertEqual(len([l for l in self.logs if "KİLİTLİ" in l]), 1)
        self.assertTrue(os.path.exists(self.iz))

    def test_acilinca_iz_silinir(self):
        open(self.iz, "w").close()
        with mock.patch.object(mac_worker.mac_anahtar, "acik", return_value=True), \
             mock.patch.object(mac_worker, "ssh", return_value=mock.Mock(stdout="", returncode=0)):
            mac_worker.poll_once()
        self.assertFalse(os.path.exists(self.iz))
        self.assertTrue(any("AÇILDI" in l for l in self.logs))


class HizliHat(unittest.TestCase):
    """mac_agent: kilitliyken POST 423, /durum yine cevap verir ve kapı halini taşır."""
    @classmethod
    def setUpClass(cls):
        cls.tok = os.path.join(tempfile.mkdtemp(), "tok")
        open(cls.tok, "w").write("sirr\n")
        cls.pt = mock.patch.object(mac_agent, "TOKEN_DOSYA", cls.tok); cls.pt.start()
        cls.pl = mock.patch.object(mac_agent, "log", lambda m: None); cls.pl.start()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), mac_agent.Uc)
        cls.uc = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.pt.stop(); cls.pl.stop()

    def _istek(self, yol, veri=None):
        req = urllib.request.Request(self.uc + yol, data=json.dumps(veri).encode() if veri else None,
                                     method="POST" if veri else "GET",
                                     headers={"X-Ghost-Token": "sirr", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_kilitliyken_komut_423_durum_200(self):
        kilit = {"kapi": "kilitli", "gerekli": "31415926", "takili": []}
        with mock.patch.object(mac_agent.mac_anahtar, "acik", return_value=False), \
             mock.patch.object(mac_agent.mac_anahtar, "durum", return_value=kilit), \
             mock.patch.object(mac_agent.subprocess, "run", side_effect=AssertionError("komut koşmamalı")):
            kod, c = self._istek("/calistir", {"cmd": "id"})
            self.assertEqual(kod, 423)
            self.assertEqual(c["kapi"], "kilitli")
            kod, d = self._istek("/durum")
            self.assertEqual((kod, d["anahtar"]["kapi"]), (200, "kilitli"))

    def test_acikken_komut_kosar(self):
        with mock.patch.object(mac_agent.mac_anahtar, "acik", return_value=True), \
             mock.patch.object(mac_agent.mac_anahtar, "durum", return_value={"kapi": "devre_disi"}):
            # cwd açık: ajanın varsayılanı ~/jarvis, CI koşucusunda o dizin YOK
            # (2026-09-23'ten beri kırmızı CI'nin tek sebebi buydu).
            kod, c = self._istek("/calistir", {"cmd": "echo selam", "cwd": tempfile.gettempdir()})
        self.assertEqual((kod, c["cikti"]), (200, "selam"))


if __name__ == "__main__":
    unittest.main()


class Rele(unittest.TestCase):
    """wda_relay: kapı kilitliyken telefon yolu (WDA + MJPEG) iş yapmaz."""
    def _handler(self, cls):
        from ops import wda_relay
        h = cls.__new__(cls)
        h.request = mock.Mock()
        return h, wda_relay

    def test_wda_kilitliyken_423_yazar_ve_hedef_aranmaz(self):
        h, wr = self._handler(wr_cls("Role"))
        with mock.patch.object(wr.mac_anahtar, "acik", return_value=False), \
             mock.patch.object(wr, "hedef_bul", side_effect=AssertionError("hedef aranmamalı")):
            h.handle()
        gonderilen = b"".join(c.args[0] for c in h.request.sendall.call_args_list)
        self.assertIn(b"423 Locked", gonderilen)
        self.assertIn("kilitli".encode(), gonderilen)
        h.request.close.assert_called()

    def test_wda_acikken_hedef_aranir(self):
        h, wr = self._handler(wr_cls("Role"))
        with mock.patch.object(wr.mac_anahtar, "acik", return_value=True), \
             mock.patch.object(wr, "hedef_bul", return_value=None) as hb:
            h.handle()
        hb.assert_called()  # kapı açık → normal akış hedef aramaya gitti

    def test_mjpeg_kilitliyken_kapanir(self):
        h, wr = self._handler(wr_cls("MjpegRole"))
        with mock.patch.object(wr.mac_anahtar, "acik", return_value=False), \
             mock.patch.object(wr, "hedef_bul", side_effect=AssertionError("hedef aranmamalı")):
            h.handle()
        h.request.close.assert_called()
        h.request.recv.assert_not_called()  # istek bile okunmadı


def wr_cls(name):
    from ops import wda_relay
    return getattr(wda_relay, name)
