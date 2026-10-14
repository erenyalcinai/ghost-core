"""Mac özel veri okuyucu (ops/mac_veri_okuyucu.py) ayrıştırıcıları + VPS istemcisi
(ops/mac_veri.py). Mac'e dokunmaz: sentetik protobuf/typedstream ve sahte hızlı hat."""
import base64
import gzip
import json
import time
import unittest
from unittest import mock

from tests import common  # noqa: F401

from ops import mac_veri, mac_veri_okuyucu as mvo  # noqa: E402


def _ld(no, payload):
    """Protobuf length-delimited alan (no, wire type 2)."""
    return _varint((no << 3) | 2) + _varint(len(payload)) + payload


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _not_zdata(metin):
    """Apple Notes ZDATA taklidi: gzip( Document(2){ version(2)=1, Note(3){ text(2) } } )."""
    note = _varint((2 << 3) | 2) + _varint(len(metin.encode())) + metin.encode()
    note += _ld(5, b"\x08\x00")  # attribute_run benzeri gürültü
    doc = (_varint((2 << 3) | 0) + _varint(1)) + _ld(3, note)
    return gzip.compress(_ld(2, doc))


def _typedstream(metin, uzun=False):
    """attributedBody taklidi: ... NSString \\x01\\x94\\x84\\x01\\x2b <len> <utf8> ..."""
    b = metin.encode()
    ln = (b"\x81" + len(b).to_bytes(2, "little")) if uzun else bytes([len(b)])
    return b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString" \
           b"\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + ln + b + b"\x86\x84"


class ProtobufTest(unittest.TestCase):
    def test_walk_turleri(self):
        buf = _varint(8) + _varint(300) + _ld(2, b"abc") + _varint((3 << 3) | 5) + b"\x01\x02\x03\x04"
        alanlar = mvo._pb_walk(buf)
        self.assertEqual(alanlar[0], (1, 0, 300))
        self.assertEqual(alanlar[1], (2, 2, b"abc"))
        self.assertEqual(alanlar[2][:2], (3, 5))

    def test_walk_kesik_veri_patlamaz(self):
        self.assertIsInstance(mvo._pb_walk(_ld(2, b"abc")[:-2]), list)
        self.assertEqual(mvo._pb_walk(b""), [])


class NotGovdesiTest(unittest.TestCase):
    def test_gzip_protobuf_cozulur(self):
        self.assertEqual(mvo.not_govdesi(_not_zdata("Merhaba\nikinci satır")), "Merhaba\nikinci satır")

    def test_turkce_ve_ek_isareti(self):
        self.assertEqual(mvo.not_govdesi(_not_zdata("şöğüç ￼ son")), "şöğüç [ek] son")

    def test_bos_ve_bozuk(self):
        self.assertEqual(mvo.not_govdesi(None), "")
        self.assertEqual(mvo.not_govdesi(b""), "")
        self.assertEqual(mvo.not_govdesi(b"\x1f\x8b bozuk gzip"), "")

    def test_yol_bulunamazsa_en_uzun_parca(self):
        ham = b"\x00\x01" + "bu okunur bir parça".encode() + b"\x00\xff" + b"kisa"
        self.assertEqual(mvo.not_govdesi(gzip.compress(ham)), "bu okunur bir parça")


class MesajMetniTest(unittest.TestCase):
    def test_text_varsa_o(self):
        self.assertEqual(mvo.mesaj_metni("düz", _typedstream("başka")), "düz")

    def test_attributed_kisa(self):
        self.assertEqual(mvo.mesaj_metni(None, _typedstream("selam naber")), "selam naber")

    def test_attributed_uzun_0x81(self):
        m = "ç" * 200
        self.assertEqual(mvo.mesaj_metni("", _typedstream(m, uzun=True)), m)

    def test_nsstring_yoksa_bos(self):
        self.assertEqual(mvo.mesaj_metni(None, b"\x04\x0bstreamtyped"), "")
        self.assertEqual(mvo.mesaj_metni(None, None), "")


class TarihSayiTest(unittest.TestCase):
    def test_core_data_saniye_ve_nanosaniye(self):
        s = 800000000  # 2026 civarı
        self.assertEqual(mvo._tarih(s), mvo._tarih(s * 1_000_000_000))
        self.assertEqual(mvo._tarih(s), time.strftime("%Y-%m-%d %H:%M", time.localtime(s + mvo.CORE_DATA_EPOCH)))

    def test_bos_ve_sifir(self):
        self.assertIsNone(mvo._tarih(None))
        self.assertIsNone(mvo._tarih(0))
        self.assertIsNone(mvo._tarih("x"))

    def test_sayi_siniri(self):
        self.assertEqual(mvo._sayi("500", 1, 200, 20), 200)
        self.assertEqual(mvo._sayi(None, 1, 200, 20), 20)
        self.assertEqual(mvo._sayi("0", 1, 200, 20), 1)


class DosyalarTest(unittest.TestCase):
    """Dosyalar (iCloud Drive) uçlarının saf kısmı — sentetik kök dizinde."""

    def setUp(self):
        import os, tempfile, plistlib
        self.kok = tempfile.mkdtemp()
        cd = os.path.join(self.kok, "com~apple~CloudDocs")
        os.makedirs(os.path.join(cd, "Belgeler", "Alt"))
        os.makedirs(os.path.join(self.kok, "iCloud~md~obsidian", "Notlar"))
        with open(os.path.join(cd, "Belgeler", "plan.md"), "w") as f:
            f.write("# plan\nmerhaba")
        with open(os.path.join(cd, "Belgeler", "resim.png"), "wb") as f:
            f.write(b"\x89PNG\x00\x00ikili")
        with open(os.path.join(cd, "Belgeler", ".Rapor.pdf.icloud"), "wb") as f:
            plistlib.dump({"NSURLFileSizeKey": 123456, "NSURLNameKey": "Rapor.pdf"}, f)
        with open(os.path.join(cd, ".DS_Store"), "wb") as f:
            f.write(b"x")
        os.symlink("/etc", os.path.join(cd, "kacak"))
        os.makedirs(os.path.join(self.kok, "com~apple~mail", "V1"))

    def test_kap_adi(self):
        self.assertEqual(mvo.dosya_kap_adi("com~apple~CloudDocs"), "iCloud Drive")
        self.assertEqual(mvo.dosya_kap_adi("iCloud~md~obsidian"), "obsidian (uygulama)")
        self.assertEqual(mvo.dosya_kap_adi("iCloud~com~apple~Pages"), "Pages (uygulama)")

    def test_yol_koku_asamaz(self):
        import os
        self.assertEqual(mvo.dosya_yolu("", self.kok), os.path.realpath(self.kok))
        self.assertEqual(mvo.dosya_yolu("/com~apple~CloudDocs/", self.kok),
                         os.path.join(os.path.realpath(self.kok), "com~apple~CloudDocs"))
        for kotu in ("../..", "com~apple~CloudDocs/../../etc/passwd", "com~apple~CloudDocs/kacak/passwd"):
            with self.assertRaises(ValueError, msg=kotu):
                mvo.dosya_yolu(kotu, self.kok)
        # 2026-09-28: Mail/Safari kapları artık kapsam içinde (Sahip kaldırdı)
        self.assertTrue(mvo.dosya_yolu("com~apple~mail", self.kok).endswith("com~apple~mail"))

    def test_kok_listesi_kaplar(self):
        o = mvo.dosyalar("", kok=self.kok)
        adlar = [(g["ad"], g.get("kap")) for g in o["girdi"]]
        self.assertEqual(adlar, [("com~apple~CloudDocs", "iCloud Drive"), ("com~apple~mail", "com~apple~mail"),
                                 ("iCloud~md~obsidian", "obsidian (uygulama)")])  # mail kabı da görünür (2026-09-28)
        self.assertFalse(o["kesildi"])

    def test_klasor_listesi_yer_tutucu_ve_gizli(self):
        o = mvo.dosyalar("com~apple~CloudDocs/Belgeler", kok=self.kok)
        g = {x["ad"]: x for x in o["girdi"]}
        self.assertEqual(list(g), ["Alt", "plan.md", "Rapor.pdf", "resim.png"])  # klasör önce, sonra ad (büyük/küçük duyarsız)
        self.assertTrue(g["Rapor.pdf"]["bulutta"]); self.assertEqual(g["Rapor.pdf"]["bayt"], 123456)
        self.assertEqual(g["Rapor.pdf"]["yol"], "com~apple~CloudDocs/Belgeler/Rapor.pdf")
        self.assertFalse(g["plan.md"]["bulutta"]); self.assertTrue(g["Alt"]["klasor"])
        self.assertNotIn(".DS_Store", [x["ad"] for x in mvo.dosyalar("com~apple~CloudDocs", kok=self.kok)["girdi"]])

    def test_derin_arama_ve_tarih_sirasi(self):
        o = mvo.dosyalar("", q="plan", derin=True, kok=self.kok)
        self.assertEqual([g["yol"] for g in o["girdi"]], ["com~apple~CloudDocs/Belgeler/plan.md"])
        o = mvo.dosyalar("", derin=True, sirala="tarih", son=2, kok=self.kok)
        self.assertEqual(len(o["girdi"]), 2); self.assertTrue(o["kesildi"])
        self.assertTrue(all("_t" not in g for g in o["girdi"]))

    def test_dosya_metin_ikili_hatalar(self):
        o = mvo.dosya("com~apple~CloudDocs/Belgeler/plan.md", kok=self.kok)
        self.assertEqual((o["tur"], o["metin"], o["bayt"]), ("metin", "# plan\nmerhaba", 14))
        o = mvo.dosya("com~apple~CloudDocs/Belgeler/resim.png", kok=self.kok)
        self.assertEqual(o["tur"], "ikili"); self.assertEqual(base64.b64decode(o["b64"])[:4], b"\x89PNG")
        self.assertIn("hata", mvo.dosya("com~apple~CloudDocs/Belgeler", kok=self.kok))
        self.assertEqual(mvo.dosya("com~apple~CloudDocs/Belgeler/yok.txt", kok=self.kok)["hata"], "yok")
        self.assertIn("hata", mvo.dosya("", kok=self.kok))
        with mock.patch.object(mvo, "_indir", return_value=False) as ind:
            self.assertIn("inmedi", mvo.dosya("com~apple~CloudDocs/Belgeler/Rapor.pdf", kok=self.kok)["hata"])
            self.assertTrue(ind.called)

    def test_dosya_bosaltilmis_edeadlk(self):
        """macOS dataless dosya: ilk open() EDEADLK verir → _indir → ikinci okuma başarır."""
        gercek_open = open
        sayac = {"n": 0}

        def sahte_open(yol, *a, **k):
            if str(yol).endswith("plan.md") and sayac["n"] == 0:
                sayac["n"] += 1
                raise OSError(11, "Resource deadlock avoided")
            return gercek_open(yol, *a, **k)
        with mock.patch.object(mvo, "open", sahte_open, create=True), \
                mock.patch.object(mvo, "_indir", return_value=True) as ind:
            o = mvo.dosya("com~apple~CloudDocs/Belgeler/plan.md", kok=self.kok)
        self.assertEqual(o["metin"], "# plan\nmerhaba"); self.assertTrue(ind.called)
        with mock.patch.object(mvo, "_bosaltilmis", return_value=True), \
                mock.patch.object(mvo, "_indir", return_value=False):
            self.assertIn("inmedi", mvo.dosya("com~apple~CloudDocs/Belgeler/plan.md", kok=self.kok)["hata"])
        self.assertFalse(mvo._bosaltilmis("/yok/boyle/dosya"))

    def test_tavan(self):
        with mock.patch.object(mvo, "DOSYA_TAVAN", 5):
            o = mvo.dosya("com~apple~CloudDocs/Belgeler/plan.md", kok=self.kok)
            self.assertIn("tavan", o["hata"]); self.assertEqual(o["bayt"], 14)


class WhatsAppTest(unittest.TestCase):
    """ChatStorage.sqlite'ın iOS şemasıyla sentetik kopyası."""

    def setUp(self):
        import os, sqlite3, tempfile
        self.db = os.path.join(tempfile.mkdtemp(), "ChatStorage.sqlite")
        c = sqlite3.connect(self.db)
        c.executescript("""
          CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZPARTNERNAME, ZCONTACTJID, ZSESSIONTYPE,
            ZUNREADCOUNT, ZMESSAGECOUNTER, ZARCHIVED, ZREMOVED, ZHIDDEN, ZLASTMESSAGEDATE, ZLASTMESSAGETEXT);
          CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZMESSAGEDATE, ZISFROMME, ZTEXT, ZMESSAGETYPE,
            ZPUSHNAME, ZFROMJID, ZCHATSESSION, ZMEDIAITEM);
          CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMEDIALOCALPATH, ZTITLE, ZFILESIZE, ZTHUMBNAILLOCALPATH, ZXMPPTHUMBPATH);
          INSERT INTO ZWACHATSESSION VALUES (1,'Ayşe','905551@s.whatsapp.net',0,2,3,0,0,0,800000000,'görüşürüz');
          INSERT INTO ZWACHATSESSION VALUES (2,'Aile 👥','123@g.us',1,0,1,0,0,0,700000000,'foto');
          INSERT INTO ZWACHATSESSION VALUES (3,'Silinmiş','9@s.whatsapp.net',0,0,0,0,1,0,900000000,'x');
          INSERT INTO ZWACHATSESSION VALUES (4,'Durum','status@broadcast',0,0,0,0,0,0,3780,'');
          INSERT INTO ZWACHATSESSION VALUES (5,'Boş','8@s.whatsapp.net',0,0,0,0,0,0,NULL,NULL);
          INSERT INTO ZWACHATSESSION VALUES (6,'Bozuk','7@s.whatsapp.net',0,0,1,0,0,0,180000000000,'');
          INSERT INTO ZWAMESSAGE VALUES (10,799999000,0,'selam',0,NULL,'905551@s.whatsapp.net',1,NULL);
          INSERT INTO ZWAMESSAGE VALUES (11,799999500,1,'görüşürüz',0,NULL,NULL,1,NULL);
          INSERT INTO ZWAMESSAGE VALUES (12,700000000,0,'',1,'Anne','905552@s.whatsapp.net',2,5);
          INSERT INTO ZWAMEDIAITEM VALUES (5,'Message/Media/123@g.us/a/b/foto.jpg','tatil',12345,NULL,'Media/123@g.us/a/b/x.thumb');
        """)
        c.commit(); c.close()
        self.yama = mock.patch.dict(mvo.DEPOLAR, {"whatsapp": self.db}); self.yama.start()

    def tearDown(self):
        self.yama.stop()

    def test_sohbetler_silinmis_haric_sirali(self):
        o = mvo.wa_sohbetler()
        self.assertEqual([(s["id"], s["ad"], s["grup"], s["okunmamis"]) for s in o],
                         [(1, "Ayşe", False, 2), (2, "Aile 👥", True, 0), (6, "Bozuk", False, 0), (5, "Boş", False, 0)])
        self.assertIsNone(o[2]["son"]); self.assertIsNone(o[3]["son"])  # durum yok; ns-bozuk ve NULL tarih sona, tarihsiz
        self.assertEqual(o[0]["son"], mvo._tarih(800000000))
        # önizleme ZWAMESSAGE'dan: metin varsa metin, medyaysa tür; ham sütun BLOB olsa bile çöp basılmaz
        self.assertEqual([s["son_metin"] for s in o[:2]], ["görüşürüz", "[görsel]"])
        self.assertEqual(mvo._wa_son_metin(mvo._ac(self.db), 5, b"\x08\x01"), "")

    def test_mesajlar_sohbete_gore_ve_kim(self):
        o = mvo.wa_mesajlar("Ayş")
        self.assertEqual([(m["id"], m["kim"], m["metin"]) for m in o], [(11, "ben", "görüşürüz"), (10, "Ayşe", "selam")])
        t1 = time.strftime("%Y-%m-%d %H:%M", time.localtime(799999200 + mvo.CORE_DATA_EPOCH))
        self.assertEqual([m["id"] for m in mvo.wa_mesajlar("1", bas=t1)], [11])
        self.assertEqual([m["id"] for m in mvo.wa_mesajlar("1", bit=t1)], [10])
        self.assertIsNone(mvo._cd_sn("bozuk")); self.assertIsNone(mvo._cd_sn(""))
        self.assertEqual(mvo.wa_mesajlar("1")[0]["id"], 11)
        o = mvo.wa_mesajlar("2")
        self.assertEqual((o[0]["kim"], o[0]["tur"], o[0]["medya"], o[0]["baslik"], o[0]["grup"], o[0]["onizleme"]),
                         ("Anne", "görsel", "Message/Media/123@g.us/a/b/foto.jpg", "tatil", True, "Message/Media/123@g.us/a/b/x.thumb"))

    def test_arama_tum_sohbetler(self):
        o = mvo.wa_mesajlar(q="sel")
        self.assertEqual([(m["sohbet"], m["metin"]) for m in o], [("Ayşe", "selam")])
        self.assertEqual(mvo.wa_mesajlar(q="yok"), [])

    def test_istemci_ciktisi(self):
        with mock.patch.object(mac_veri, "iste", side_effect=lambda uc, p=None, **k: (
                mvo.wa_mesajlar(p.get("sohbet", ""), p.get("q", ""), p["son"]) if p.get("sohbet") or p.get("q")
                else mvo.wa_sohbetler(p["son"]))):
            with mock.patch("sys.stdout") as out:
                self.assertEqual(mac_veri.main(["whatsapp"]), 0)
                self.assertEqual(mac_veri.main(["whatsapp", "Ayşe"]), 0)
            metin = "".join(str(c.args[0]) for c in out.write.call_args_list)
        self.assertIn("#1 · Ayşe (2 yeni) · 3 mesaj", metin)
        self.assertIn("Aile 👥 👥", metin)
        self.assertLess(metin.index("Ayşe: selam"), metin.index("ben: görüşürüz"))  # eskiden yeniye


class IstemciTest(unittest.TestCase):
    def test_dosyalar_satiri(self):
        self.assertEqual(mac_veri.dosya_satiri({"ad": "Belgeler", "yol": "x/Belgeler", "klasor": True, "bayt": None,
                                                "degisti": "2026-09-28 10:00", "bulutta": False}), "📁 Belgeler/")
        s = mac_veri.dosya_satiri({"ad": "com~apple~CloudDocs", "yol": "com~apple~CloudDocs", "klasor": True, "bayt": None,
                                   "degisti": "", "bulutta": False, "kap": "iCloud Drive"})
        self.assertEqual(s, "📁 com~apple~CloudDocs  → iCloud Drive/")
        s = mac_veri.dosya_satiri({"ad": "Rapor.pdf", "yol": "a/Rapor.pdf", "klasor": False, "bayt": 123456,
                                   "degisti": "2026-09-28 10:00", "bulutta": True}, tam_yol=True)
        self.assertEqual(s, "120.6K 2026-09-28 10:00 ☁ a/Rapor.pdf")

    def test_dosya_metin_basar_ikili_yazar(self):
        import os, tempfile
        with mock.patch.object(mac_veri, "iste", return_value={"tur": "metin", "metin": "selam", "yol": "a/b.md", "bayt": 5}):
            with mock.patch("sys.stdout") as out:
                self.assertEqual(mac_veri.main(["dosya", "a/b.md"]), 0)
            self.assertIn("selam", "".join(str(c.args[0]) for c in out.write.call_args_list))
        hedef = os.path.join(tempfile.mkdtemp(), "r.png")
        cevap = {"tur": "ikili", "b64": base64.b64encode(b"\x89PNGx").decode(), "yol": "a/r.png", "bayt": 5}
        with mock.patch.object(mac_veri, "iste", return_value=cevap):
            self.assertEqual(mac_veri.main(["dosya", "a/r.png", hedef]), 0)
        self.assertEqual(open(hedef, "rb").read(), b"\x89PNGx")


    def test_iste_url_ve_json(self):
        with mock.patch.object(mac_veri.mac, "komut", return_value={"cikti": json.dumps([{"id": 1}]), "rc": 0}) as k:
            self.assertEqual(mac_veri.iste("notlar", {"son": 5, "q": "", "uzunluk": 0}), [{"id": 1}])
            cmd = k.call_args[0][0]
            self.assertIn("8766/notlar?son=5'", cmd)
            self.assertNotIn("uzunluk", cmd)
            self.assertIn("X-Ghost-Token", cmd)

    def test_iste_bos_cevap_hata(self):
        with mock.patch.object(mac_veri.mac, "komut", return_value={"cikti": "", "rc": 0}):
            self.assertIn("hata", mac_veri.iste("durum"))
        with mock.patch.object(mac_veri.mac, "komut", return_value={"hata": "hat kapalı"}):
            self.assertEqual(mac_veri.iste("durum")["hata"], "hat kapalı")

    def test_foto_diske_yazar(self):
        import base64, os, tempfile
        jpg = b"\xff\xd8\xff\xe0test"
        cevap = {"uuid": "A" * 8 + "-1111-2222-3333-" + "B" * 12, "kaynak": "asil", "bayt": len(jpg),
                 "jpeg_b64": base64.b64encode(jpg).decode()}
        hedef = os.path.join(tempfile.mkdtemp(), "x.jpg")
        with mock.patch.object(mac_veri, "iste", return_value=cevap):
            self.assertEqual(mac_veri.main(["foto", cevap["uuid"], hedef]), 0)
        self.assertEqual(open(hedef, "rb").read(), jpg)


class MacMail(unittest.TestCase):
    """Mac Mail kaynağı (2026-09-28): sentetik V10/MailData/Envelope Index + .emlx dosyaları."""

    def setUp(self):
        import os, sqlite3, tempfile
        self.tmp = tempfile.mkdtemp()
        v10 = os.path.join(self.tmp, "V10")
        os.makedirs(os.path.join(v10, "MailData"))
        c = sqlite3.connect(os.path.join(v10, "MailData", "Envelope Index"))
        c.executescript("""
            CREATE TABLE messages(ROWID INTEGER PRIMARY KEY, message_id TEXT, sender INT, subject INT,
                date_sent INT, date_received INT, mailbox INT, read INT, flagged INT, size INT, deleted INT DEFAULT 0,
                snippet TEXT);
            CREATE TABLE subjects(ROWID INTEGER PRIMARY KEY, subject TEXT);
            CREATE TABLE addresses(ROWID INTEGER PRIMARY KEY, address TEXT, comment TEXT);
            CREATE TABLE mailboxes(ROWID INTEGER PRIMARY KEY, url TEXT);
            INSERT INTO mailboxes VALUES (1,'imap://owner%40example.com@imap.gmail.com/INBOX'),
                                         (2,'imap://owner-mail%40example-mail.com@outlook.office365.com/INBOX'),
                                         (3,'imap://owner%40example.com@imap.gmail.com/%5BGmail%5D/Sent%20Mail');
            INSERT INTO subjects VALUES (1,'Fatura Eylül'),(2,'Van planı'),(3,'Re: Van planı');
            INSERT INTO addresses VALUES (1,'bilgi@examplebank.example','Örnek Bankası'),(2,'ali@example.com','Ali Veli'),
                                         (3,'owner@example.com','Sahip');
            INSERT INTO messages VALUES (1234,'<a@x>',1,1,1700000000,1700000000,2,1,0,1000,0,'Ekstreniz hazır'),
                                        (12345,'<b@x>',2,2,1700100000,1700100000,1,0,1,2000,0,'Yarın Van'),
                                        (12346,'<c@x>',3,3,1700200000,1700200000,3,1,0,500,0,NULL),
                                        (99,'<d@x>',2,2,1700300000,1700300000,1,1,0,10,1,'silinmiş');
        """)
        c.commit(); c.close()
        # emlx: 12345 → Data/2/1/Messages (son 3 basamak atılır, kalan tersten); 1234 → Data/1/Messages
        hesap = os.path.join(v10, "ACC-UUID", "INBOX.mbox", "BOX-UUID", "Data")
        def emlx(yol, ham):
            os.makedirs(os.path.dirname(yol), exist_ok=True)
            with open(yol, "wb") as f:
                f.write(b"%d\n" % len(ham) + ham + b"<?xml version=\"1.0\"?><plist/>\n")
        self.ham = (b"From: Ali Veli <ali@example.com>\r\nTo: owner@example.com\r\n"
                    b"Subject: =?utf-8?q?Van_plan=C4=B1?=\r\nDate: Wed, 15 Nov 2023 10:00:00 +0300\r\n"
                    b"Content-Type: multipart/mixed; boundary=B\r\n\r\n--B\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
                    b"<html><body><p>Yar\xc4\xb1n <b>Van</b>'a gidiyoruz.</p><p>Ali</p></body></html>\r\n"
                    b"--B\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=plan.pdf\r\n\r\nPDF\r\n--B--\r\n")
        emlx(os.path.join(hesap, "2", "1", "Messages", "12345.emlx"), self.ham)
        emlx(os.path.join(hesap, "1", "Messages", "1234.partial.emlx"),
             b"From: bilgi@examplebank.example\r\nSubject: Fatura\r\n\r\nEkstre metni\r\n")

    def test_liste_ve_suzgecler(self):
        o = mvo.mail_liste(kok=self.tmp)
        self.assertEqual([r["id"] for r in o], [12346, 12345, 1234])      # silinmiş 99 yok, yeniden eskiye
        self.assertEqual(o[1]["kimden"], "Ali Veli <ali@example.com>")
        self.assertEqual((o[1]["hesap"], o[1]["kutu"]), ("owner@example.com@imap.gmail.com", "INBOX"))
        self.assertEqual(o[0]["kutu"], "[Gmail]/Sent Mail")
        self.assertFalse(o[1]["okundu"]); self.assertTrue(o[1]["bayrak"])
        self.assertEqual([r["id"] for r in mvo.mail_liste(q="van", kok=self.tmp)], [12346, 12345])
        self.assertEqual([r["id"] for r in mvo.mail_liste(q="ekstre", kok=self.tmp)], [1234])   # özetten
        self.assertEqual([r["id"] for r in mvo.mail_liste(kimden="banka", kok=self.tmp)], [1234])
        self.assertEqual([r["id"] for r in mvo.mail_liste(hesap="example-mail", kok=self.tmp)], [1234])
        self.assertEqual([r["id"] for r in mvo.mail_liste(hesap="owner@example.com", kutu="INBOX", kok=self.tmp)], [12345])
        self.assertEqual(len(mvo.mail_liste(son=1, kok=self.tmp)), 1)
        # 1700100000 = 2023-11-16 02:40 UTC, 1700200000 = 2023-11-17 06:26 UTC → gün sınırı ikisinin arasında
        self.assertEqual([r["id"] for r in mvo.mail_liste(bas="2023-11-17", kok=self.tmp)], [12346])
        self.assertEqual([r["id"] for r in mvo.mail_liste(bit="2023-11-16", kok=self.tmp)], [12345, 1234])

    def test_hesaplar(self):
        o = mvo.mail_hesaplar(kok=self.tmp)
        h = {x["hesap"]: x for x in o["hesaplar"]}
        self.assertEqual(h["owner@example.com@imap.gmail.com"]["toplam"], 3)   # silinmiş de sayılır (kutu sayımı)
        self.assertEqual(h["owner-mail@example-mail.com@outlook.office365.com"]["kutular"], {"INBOX": 1})

    def test_oku_emlx(self):
        o = mvo.mail_oku(12345, kok=self.tmp)
        self.assertEqual(o["konu"], "Van planı")
        self.assertEqual(o["kimden"], "Ali Veli <ali@example.com>")
        self.assertEqual(o["ekler"], ["plan.pdf"])
        self.assertIn("Yarın Van'a gidiyoruz.", o["metin"])      # HTML soyuldu
        self.assertNotIn("<b>", o["metin"]); self.assertFalse(o["kismi"])
        k = mvo.mail_oku(1234, kok=self.tmp)
        self.assertTrue(k["kismi"]); self.assertEqual(k["metin"], "Ekstre metni")
        self.assertIn("hata", mvo.mail_oku(777, kok=self.tmp))
        self.assertIn("hata", mvo.mail_oku("x", kok=self.tmp))

    def test_dizin_yoksa_acik_hata(self):
        import tempfile
        with self.assertRaises(ValueError):
            mvo.mail_liste(kok=tempfile.mkdtemp())


if __name__ == "__main__":
    unittest.main()


class YedekTest(unittest.TestCase):
    """iPhone yedeği kaynağı (2026-09-28): sentetik Manifest.db + sms/rehber/arama/safari veritabanları."""

    def _db(self, yol, sql, satirlar=()):
        import sqlite3
        c = sqlite3.connect(yol)
        c.executescript(sql)
        for s, a in satirlar:
            c.execute(s, a)
        c.commit(); c.close()

    def setUp(self):
        import hashlib, os, plistlib, sqlite3, tempfile
        self.tmp = tempfile.mkdtemp()
        self.udid = "00008150-TEST"
        d = os.path.join(self.tmp, self.udid)
        os.makedirs(d)
        self.dosyalar = {}   # (domain, rel) → tam yol

        def ekle(domain, rel, icerik=None):
            fid = hashlib.sha1(("%s-%s" % (domain, rel)).encode()).hexdigest()
            os.makedirs(os.path.join(d, fid[:2]), exist_ok=True)
            p = os.path.join(d, fid[:2], fid)
            if icerik is not None:
                with open(p, "wb") as f:
                    f.write(icerik)
            self.dosyalar[(domain, rel)] = (fid, p)
            return p

        # veritabanları
        p = ekle("HomeDomain", "Library/SMS/sms.db")
        self._db(p, """CREATE TABLE message(ROWID INTEGER PRIMARY KEY, date INT, is_from_me INT, text TEXT,
                        attributedBody BLOB, cache_has_attachments INT, handle_id INT, service TEXT,
                        item_type INT DEFAULT 0, associated_message_type INT DEFAULT 0);
                       CREATE TABLE handle(ROWID INTEGER PRIMARY KEY, id TEXT);
                       CREATE TABLE chat(ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, display_name TEXT);
                       CREATE TABLE chat_message_join(chat_id INT, message_id INT);""",
                  [("INSERT INTO handle VALUES(1,'+905321112233')", ()),
                   ("INSERT INTO chat VALUES(1,'+905321112233',NULL)", ()),
                   ("INSERT INTO message(ROWID,date,is_from_me,text,handle_id,service) VALUES(1,700000000000000000,0,'selam',1,'SMS')", ()),
                   ("INSERT INTO message(ROWID,date,is_from_me,text,handle_id,service) VALUES(2,700000001000000000,1,'aleyküm',1,'SMS')", ()),
                   ("INSERT INTO chat_message_join VALUES(1,1)", ()), ("INSERT INTO chat_message_join VALUES(1,2)", ())])
        p = ekle("HomeDomain", "Library/AddressBook/AddressBook.sqlitedb")
        self._db(p, """CREATE TABLE ABPerson(ROWID INTEGER PRIMARY KEY, First TEXT, Last TEXT, Organization TEXT);
                       CREATE TABLE ABMultiValue(UID INTEGER PRIMARY KEY, record_id INT, property INT, value TEXT);""",
                  [("INSERT INTO ABPerson VALUES(1,'Ayşe','Yalaz',NULL)", ()),
                   ("INSERT INTO ABMultiValue VALUES(1,1,3,'0532 111 22 33')", ()),
                   ("INSERT INTO ABMultiValue VALUES(2,1,4,'Ayse@Example.com')", ()),
                   ("INSERT INTO ABPerson VALUES(2,NULL,NULL,'Yalco')", ()),
                   ("INSERT INTO ABMultiValue VALUES(3,2,3,'+90 555 000 00 00')", ())])
        p = ekle("HomeDomain", "Library/CallHistoryDB/CallHistory.storedata")
        self._db(p, """CREATE TABLE ZCALLRECORD(Z_PK INTEGER PRIMARY KEY, ZADDRESS TEXT, ZDATE REAL, ZDURATION REAL,
                        ZORIGINATED INT, ZANSWERED INT, ZCALLTYPE INT, ZSERVICE_PROVIDER TEXT);""",
                  [("INSERT INTO ZCALLRECORD VALUES(1,'905321112233',700000000,61.5,1,1,1,'com.apple.Telephony')", ()),
                   ("INSERT INTO ZCALLRECORD VALUES(2,'905550000000',700000100,0,0,0,1,'net.whatsapp.WhatsApp')", ())])
        p = ekle("HomeDomain", "Library/Safari/History.db")
        self._db(p, """CREATE TABLE history_items(id INTEGER PRIMARY KEY, url TEXT, visit_count INT);
                       CREATE TABLE history_visits(id INTEGER PRIMARY KEY, history_item INT, title TEXT, visit_time REAL);""",
                  [("INSERT INTO history_items VALUES(1,'https://example.com/',3)", ()),
                   ("INSERT INTO history_visits VALUES(1,1,'Sahip',700000000)", ())])
        ekle("AppDomainGroup-group.net.whatsapp.WhatsApp.shared", "Message/Media/x/a.txt", b"medya")
        ekle("HomeDomain", "Library/SMS/sms.db-wal")   # Manifest'te var, diskte yok → atlanmalı

        # Manifest + plist'ler
        m = sqlite3.connect(os.path.join(d, "Manifest.db"))
        m.execute("PRAGMA journal_mode=WAL")   # gerçek Manifest WAL kipinde gelir, -wal/-shm yok (2026-09-28)
        m.execute("CREATE TABLE Files(fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, flags INT, file BLOB)")
        for (dom, rel), (fid, _) in self.dosyalar.items():
            m.execute("INSERT INTO Files VALUES(?,?,?,1,NULL)", (fid, dom, rel))
        m.execute("INSERT INTO Files VALUES('dir1','HomeDomain','Library',2,NULL)")
        m.commit(); m.close()
        for ek in ("-wal", "-shm"):   # cihazdan yalnız ana dosya gelir
            if os.path.exists(os.path.join(d, "Manifest.db" + ek)):
                os.unlink(os.path.join(d, "Manifest.db" + ek))
        import datetime
        with open(os.path.join(d, "Status.plist"), "wb") as f:
            plistlib.dump({"SnapshotState": "finished", "Date": datetime.datetime(2026, 9, 28, 8, 0),
                           "IsFullBackup": True, "Version": "3.3"}, f)
        with open(os.path.join(d, "Info.plist"), "wb") as f:
            plistlib.dump({"Device Name": "Sahip", "Product Version": "26.0", "Product Type": "iPhone18,3"}, f)
        self._eski = mvo.YEDEK_KOK
        mvo.YEDEK_KOK = self.tmp

    def tearDown(self):
        import shutil
        mvo.YEDEK_KOK = self._eski
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dizin_ve_dosya_cozumu(self):
        import os
        self.assertEqual(os.path.basename(mvo.yedek_dizini()), self.udid)
        self.assertTrue(mvo.yedek_dosya("HomeDomain", "Library/SMS/sms.db").endswith(
            self.dosyalar[("HomeDomain", "Library/SMS/sms.db")][0]))
        self.assertIsNone(mvo.yedek_dosya("HomeDomain", "yok"))
        self.assertIsNone(mvo.yedek_dosya("HomeDomain", "Library/SMS/sms.db-wal"))   # diskte yok
        self.assertIsNone(mvo.yedek_dizini(os.path.join(self.tmp, "hic")))

    def test_durum(self):
        d = mvo.yedek_durum()
        self.assertTrue(d["var"] and d["bitti"])
        self.assertEqual(d["device_name"], "Sahip")
        import calendar, datetime
        beklenen = time.strftime("%Y-%m-%d %H:%M", time.localtime(calendar.timegm(datetime.datetime(2026, 9, 28, 8, 0).timetuple())))
        self.assertEqual(d["date"], beklenen)   # plist UTC → yerel
        self.assertEqual(d["dosya_sayisi"], 6)
        self.assertTrue(d["alanlar"]["mesajlar"] and d["alanlar"]["rehber"] and not d["alanlar"]["whatsapp"])
        mvo.YEDEK_KOK = self.tmp + "/hic"
        self.assertFalse(mvo.yedek_durum()["var"])

    def test_tel_anahtar_ve_rehber(self):
        self.assertEqual(mvo._tel_anahtar("+90 532 111 22 33"), "5321112233")
        self.assertEqual(mvo._tel_anahtar("0532 111 22 33"), "5321112233")
        self.assertEqual(mvo._tel_anahtar("Ayse@Example.com"), "ayse@example.com")
        self.assertEqual(mvo._tel_anahtar("112"), "112")
        h = mvo.rehber_haritasi()
        self.assertEqual(h["5321112233"], "Ayşe Yalaz")
        self.assertEqual(h["ayse@example.com"], "Ayşe Yalaz")
        self.assertEqual(h["5550000000"], "Yalco")
        r = mvo.rehber(q="yal")
        self.assertEqual([x["ad"] for x in r], ["Yalco", "Ayşe Yalaz"])
        self.assertEqual(r[1]["tel"], ["0532 111 22 33"]); self.assertEqual(r[1]["eposta"], ["Ayse@Example.com"])
        self.assertEqual(mvo.rehber(q="hiçkimse"), [])

    def test_mesajlar_yedekten_adli(self):
        o = mvo.mesajlar(son=10, kaynak="yedek")
        self.assertEqual([m["kimden"] for m in o], ["ben", "Ayşe Yalaz"])
        self.assertEqual(o[0]["sohbet"], "Ayşe Yalaz")
        self.assertEqual(o[0]["tarih"], mvo._tarih(700000001000000000))
        # ada göre süzme rehber üzerinden çalışır; numaraya göre süzme SQL'de
        self.assertEqual(len(mvo.mesajlar(kisi="ayşe", kaynak="yedek")), 2)
        self.assertEqual(len(mvo.mesajlar(kisi="yalco", kaynak="yedek")), 0)
        self.assertEqual(len(mvo.mesajlar(kisi="5321112233", kaynak="yedek")), 2)
        self.assertEqual([m["metin"] for m in mvo.mesajlar(q="selam", kaynak="yedek")], ["selam"])

    def test_aramalar(self):
        o = mvo.aramalar(son=10)
        self.assertEqual([a["kim"] for a in o], ["Yalco", "Ayşe Yalaz"])
        self.assertEqual(o[0]["tur"], "whatsapp"); self.assertFalse(o[0]["cevaplandi"])
        self.assertEqual(o[1]["tur"], "telefon"); self.assertEqual(o[1]["sure_sn"], 61); self.assertTrue(o[1]["benden"])
        self.assertEqual([a["kim"] for a in mvo.aramalar(kisi="ayşe")], ["Ayşe Yalaz"])
        self.assertEqual([a["kim"] for a in mvo.aramalar(kisi="0555 000 00 00")], ["Yalco"])

    def test_safari(self):
        o = mvo.safari(son=5)
        self.assertEqual(o[0]["url"], "https://example.com/"); self.assertEqual(o[0]["baslik"], "Sahip")
        self.assertEqual(mvo.safari(q="google"), [])
        self.assertEqual(len(mvo.safari(q="example")), 1)

    def test_liste_ve_dosya_oku(self):
        o = mvo.yedek_liste()
        self.assertIn("HomeDomain", o["domainler"])
        self.assertEqual(len(o["girdi"]), 6)   # klasör (flags=2) girmez
        self.assertEqual(mvo.yedek_liste(domain="whatsapp")["girdi"],
                         ["AppDomainGroup-group.net.whatsapp.WhatsApp.shared/Message/Media/x/a.txt"])
        self.assertEqual(len(mvo.yedek_liste(q="sms.db")["girdi"]), 2)
        d = mvo.yedek_dosya_oku("AppDomainGroup-group.net.whatsapp.WhatsApp.shared/Message/Media/x/a.txt")
        self.assertEqual((d["tur"], d["metin"]), ("metin", "medya"))
        self.assertIn("hata", mvo.yedek_dosya_oku("HomeDomain/yok"))
        self.assertIn("hata", mvo.yedek_dosya_oku("tekparça"))

    def test_yedek_db_onbellek_ve_wal(self):
        import os, sqlite3
        # wal kipindeki kaynak: checkpoint edilmemiş satır da kopyada görünmeli
        p = self.dosyalar[("HomeDomain", "Library/Safari/History.db")][1]
        c = sqlite3.connect(p); c.execute("PRAGMA journal_mode=WAL")
        c.execute("INSERT INTO history_items VALUES(2,'https://app-b.example.com/',1)")
        c.execute("INSERT INTO history_visits VALUES(2,2,'App B',700000500)"); c.commit()
        # wal dosyasını Manifest'e ekle
        import hashlib
        fid = hashlib.sha1(b"HomeDomain-Library/Safari/History.db-wal").hexdigest()
        d = os.path.join(self.tmp, self.udid)
        os.makedirs(os.path.join(d, fid[:2]), exist_ok=True)
        import shutil; shutil.copyfile(p + "-wal", os.path.join(d, fid[:2], fid))
        m = sqlite3.connect(os.path.join(d, "Manifest.db"))
        m.execute("INSERT INTO Files VALUES(?,?,?,1,NULL)", (fid, "HomeDomain", "Library/Safari/History.db-wal")); m.commit(); m.close()
        c.close()
        os.utime(p, (time.time() + 5, time.time() + 5))   # kaynak yeni → kopya tazelenir
        hedef = mvo.yedek_db("safari")
        self.assertTrue(hedef.startswith(os.path.join(self.tmp, "_db")))
        self.assertEqual([r["url"] for r in mvo.safari(son=5)], ["https://app-b.example.com/", "https://example.com/"])
        self.assertFalse(os.path.exists(hedef + "-wal"))   # checkpoint sonrası wal kalmaz
        # yedek dizini "_db" ile karışmaz
        self.assertEqual(os.path.basename(mvo.yedek_dizini()), self.udid)
        with self.assertRaises(ValueError):
            mvo.yedek_db("whatsapp")

    def test_mac_kopyasina_dusme(self):
        """aramalar yedekte yoksa Mac'in iCloud kopyasından (MAC_YEDEK_YOL) okunur; o da yoksa açık hata."""
        import os, sqlite3
        # yedekteki arama dosyasını sil → yedekte yok
        fid, p = self.dosyalar[("HomeDomain", "Library/CallHistoryDB/CallHistory.storedata")]
        os.unlink(p)
        eski = dict(mvo.MAC_YEDEK_YOL)
        try:
            mvo.MAC_YEDEK_YOL["aramalar"] = os.path.join(self.tmp, "yok.storedata")
            with self.assertRaises(ValueError) as cm:
                mvo.aramalar(son=3)
            self.assertIn("Mac kopyası da yok", str(cm.exception))
            mac_db = os.path.join(self.tmp, "mac_CallHistory.storedata")
            self._db(mac_db, """CREATE TABLE ZCALLRECORD(Z_PK INTEGER PRIMARY KEY, ZADDRESS TEXT, ZDATE REAL, ZDURATION REAL,
                                 ZORIGINATED INT, ZANSWERED INT, ZCALLTYPE INT, ZSERVICE_PROVIDER TEXT);""",
                     [("INSERT INTO ZCALLRECORD VALUES(9,'905321112233',700000900,5,0,1,1,'com.apple.Telephony')", ())])
            mvo.MAC_YEDEK_YOL["aramalar"] = mac_db
            o = mvo.aramalar(son=3)
            self.assertEqual([a["id"] for a in o], [9]); self.assertEqual(o[0]["kim"], "Ayşe Yalaz")
            self.assertEqual(mvo.yedek_durum()["alanlar"]["aramalar"], "mac")
            self.assertTrue(os.path.exists(os.path.join(self.tmp, "_db", "aramalar_mac.sqlite")))
        finally:
            mvo.MAC_YEDEK_YOL.clear(); mvo.MAC_YEDEK_YOL.update(eski)

    def test_kapsam_disi_kap_kalkti(self):
        import os
        kok = os.path.join(self.tmp, "icloud"); os.makedirs(os.path.join(kok, "com~apple~mail"))
        self.assertTrue(mvo.dosya_yolu("com~apple~mail", kok).endswith("com~apple~mail"))
        self.assertEqual(mvo.KAPSAM_DISI_KAP, ())


class YedekIstemciTest(unittest.TestCase):
    def _sahte(self, cevap):
        return mock.patch.object(mac_veri.mac, "komut", return_value={"cikti": json.dumps(cevap)})

    def test_yedek_durum_satiri(self):
        with self._sahte({"var": True, "device_name": "Sahip", "product_version": "26.0", "udid": "U", "date": "2026-09-28 08:00",
                          "bitti": False, "snapshotstate": "new", "dosya_sayisi": 5,
                          "alanlar": {"mesajlar": True, "whatsapp": False}}), \
                mock.patch("sys.stdout") as so:
            self.assertEqual(mac_veri.main(["yedek"]), 0)
        m = "".join(str(c.args[0]) for c in so.write.call_args_list)
        self.assertIn("SÜRÜYOR (new)", m); self.assertIn("whatsapp ✗", m); self.assertNotIn("mesajlar ✗", m)

    def test_aramalar_ve_kaynak_parametresi(self):
        with self._sahte([{"tarih": "2026-09-28 10:00", "kim": "Ayşe", "numara": "+90532", "benden": True,
                           "cevaplandi": True, "sure_sn": 61, "tur": "telefon"}]) as k, mock.patch("sys.stdout") as so:
            self.assertEqual(mac_veri.main(["aramalar", "--kisi", "ayşe"]), 0)
            self.assertIn("aramalar?", k.call_args.args[0]); self.assertIn("kisi=ay", k.call_args.args[0])
        m = "".join(str(c.args[0]) for c in so.write.call_args_list)
        self.assertIn("→ Ayşe (+90532) · 61 sn · telefon", m)
        with self._sahte([]) as k, mock.patch("sys.stdout"):
            mac_veri.main(["whatsapp", "Annem", "--kaynak", "yedek"])
            self.assertIn("kaynak=yedek", k.call_args.args[0])
            mac_veri.main(["mesajlar", "--kaynak", "yedek"])
            self.assertIn("kaynak=yedek", k.call_args.args[0])
            mac_veri.main(["mesajlar"])
            self.assertNotIn("kaynak", k.call_args.args[0])
