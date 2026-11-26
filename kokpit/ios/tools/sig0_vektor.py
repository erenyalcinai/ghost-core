#!/usr/bin/env python3
"""Kokpit Watch — imza test vektörleri (kanonik dize, X-Ghost-Sig, X-Ghost-Sig0).

İki iş yapar:
  1. KANIT: aşağıdaki başvuru hesabının (kanonik / sig / sig0) SUNUCUNUN KENDİ doğrulamasıyla aynı olduğunu gösterir —
     gerçek onay servisi kodu (ops/onay/onay_servisi.py, op_kokpit) süreç içinde, geçici dizinde açılır, bir "cep"
     cihazı eşlenir ve her vektör biçimi taze saatle servise doğrulatılır ("ok" + tür cep). Yanlış/eksik sig0 reddi
     de sınanır. Ağa çıkmaz, gerçek servise ve ghost.db'ye dokunmaz.
  2. VEKTÖR: sabit anahtar/saat/nonce ile hesaplanan beklenen değerleri `sig0_vektorler.json`a yazar. Aynı dosyayı
     Mac'te `tools/sig0_test.swift` okur ve Swift hesabının (GhostImza) bire bir aynı sonucu verdiğini sınar.

Koşum (depo kökünden):  python3 kokpit/ios/tools/sig0_vektor.py [--yaz]
  --yaz verilmezse dosyaya yazmaz; mevcut dosyayla karşılaştırır (drift denetimi).
"""
import base64
import hashlib
import hmac
import importlib.util
import json
import os
import sys
import tempfile
import time

KOK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, KOK)
import tests.common  # noqa: E402,F401  (yolları geçici dizine alır, GHOST_TEST=1: ağ yok)
from ghost import kokpit  # noqa: E402

CIKTI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sig0_vektorler.json")


# ---------------------------------------------------------------- başvuru hesabı (Swift GhostImza'nın karşılığı)
def kanonik(ts, nonce, method, yol_sorgu, govde):
    return "%s\n%s\n%s\n%s\n%s" % (ts, nonce, method.upper(), yol_sorgu, hashlib.sha256(govde).hexdigest())


def sig(key_hex, kan):
    return hmac.new(bytes.fromhex(key_hex), kan.encode("utf-8"), hashlib.sha256).hexdigest()


def sig0(key0_hex, kan, sig_hex):
    return hmac.new(bytes.fromhex(key0_hex), (kan + "\n" + sig_hex).encode("utf-8"), hashlib.sha256).hexdigest()


# Biçimler: saat uygulamasının gerçekten attığı istekler + köşe durumlar (Türkçe gövde, yüzde kodlu sorgu, boş gövde).
BICIMLER = [
    {"ad": "nabiz-get", "method": "GET", "yol": "/v1/nabiz", "govde": b""},
    {"ad": "outbox-uzun-yoklama", "method": "GET",
     "yol": "/v1/outbox?since=-1&limit=10&bekle=0&durum=0%7C", "govde": b""},
    {"ad": "outbox-durum-turkce", "method": "GET",
     "yol": "/v1/outbox?since=4821&limit=20&bekle=20&durum=1%7C%C3%A7al%C4%B1%C5%9F%C4%B1yor&gts=1795465505.690",
     "govde": b""},
    {"ad": "mesaj-post-turkce", "method": "POST", "yol": "/v1/mesaj",
     "govde": '{"metin":"deploy bitti mi, şu işe bakar mısın? ığüşöç İĞÜŞÖÇ"}'.encode("utf-8")},
    {"ad": "mesaj-post-tamam", "method": "POST", "yol": "/v1/mesaj", "govde": b'{"metin":"tamam"}'},
    {"ad": "apns-post", "method": "POST", "yol": "/v1/apns",
     "govde": ('{"env":"development","token":"%s"}' % ("ab" * 32)).encode("utf-8")},
    {"ad": "kucuk-harf-yontem", "method": "post", "yol": "/v1/mesaj", "govde": b'{"metin":"x"}'},
    {"ad": "emoji-govde", "method": "POST", "yol": "/v1/mesaj",
     "govde": '{"metin":"👍 olur\\nikinci satır"}'.encode("utf-8")},
]

SABIT = [  # (key, key0, ts, nonce)
    ("00" * 32, "ff" * 32, "1795465505", "00112233445566778899aabbccddeeff"),
    ("ff" * 32, "00" * 32, "1795465505", "ffeeddccbbaa99887766554433221100"),
    (hashlib.sha256(b"kokpit-watch-vektor-key").hexdigest(), hashlib.sha256(b"kokpit-watch-vektor-key0").hexdigest(),
     "1800000000", "a1b2c3d4e5f60718"),
]


def vektorler():
    out = []
    for key, key0, ts, nonce in SABIT:
        for b in BICIMLER:
            kan = kanonik(ts, nonce, b["method"], b["yol"], b["govde"])
            s = sig(key, kan)
            out.append({"ad": b["ad"], "key": key, "key0": key0, "ts": ts, "nonce": nonce, "method": b["method"],
                        "yol": b["yol"], "govde_hex": b["govde"].hex(), "kanonik_hex": kan.encode("utf-8").hex(),
                        "sig": s, "sig0": sig0(key0, kan, s)})
    return out


# ---------------------------------------------------------------- kanıt: sunucunun kendi doğrulaması
def sunucu_kaniti():
    yol = os.path.join(KOK, "ops", "onay", "onay_servisi.py")
    spec = importlib.util.spec_from_file_location("onay_servisi", yol)
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "ayar.json"), "w") as f:
        json.dump({"sahip_id": 5550001, "telegram_token": "x", "izinli_uid": [os.getuid()]}, f)
    giden = []
    kopru_pid = 777
    o = srv.Onay(d, kopru_pid=lambda: kopru_pid, telegram=lambda m: giden.append(m) or True)
    ben, kopru = (4242, os.getuid(), os.getgid()), (kopru_pid, os.getuid(), os.getgid())
    r = o.isle({"op": "kod_uret", "ad": "Apple Watch", "tur": "cep"}, kopru)
    assert r.get("ok"), r
    kod = [w for w in giden[-1].split() if w.isdigit() and len(w) == 6][0]
    c = o.isle({"op": "esle", "kod": kod, "ad": "Apple Watch"}, ben)["cihaz"]
    assert c["tur"] == "cep" and len(c["key0"]) == 64, "eşleşme cep türü + key0 vermedi"

    def dogrulat(b, s0=None, atla0=False):
        ts, nonce = str(int(time.time())), os.urandom(16).hex()
        yontem = b["method"].upper()
        kan = kanonik(ts, nonce, yontem, b["yol"], b["govde"])
        s = sig(c["key"], kan)
        assert s == kokpit.imza(c["key"], ts, nonce, yontem, b["yol"], b["govde"]), "sig ≠ kokpit.imza"
        h = {"device": c["id"], "ts": ts, "nonce": nonce, "sig": s}
        if not atla0:
            h["sig0"] = s0 or sig0(c["key0"], kan, s)
        return o.isle({"op": "kokpit", "h": h, "method": yontem, "path": b["yol"],
                       "body_b64": base64.b64encode(b["govde"]).decode()}, ben)

    for b in BICIMLER:
        y = dogrulat(b)
        assert y.get("cihaz") and y["cihaz"].get("tur") == "cep" and y["neden"] == "ok", (b["ad"], y)
    # Düz "tamam": cep cihazında onay kaydı YAZILMAZ, metin önekli imzalanır.
    y = dogrulat(BICIMLER[4])
    assert y.get("onay") is None and y["cihaz"]["inbox_metin"].startswith("[Cep cihazı: Apple Watch; onay sayılmaz] "), y
    # sig0 eksik / yanlış → imza reddi (asıl imza doğru olsa da).
    assert dogrulat(BICIMLER[0], atla0=True)["neden"] == "imza"
    assert dogrulat(BICIMLER[0], s0="0" * 64)["neden"] == "imza"
    return len(BICIMLER)


def main(argv):
    n = sunucu_kaniti()
    v = vektorler()
    metin = json.dumps({"aciklama": "Kokpit imza vektörleri — üreten: tools/sig0_vektor.py; okuyan: tools/sig0_test.swift",
                        "vektorler": v}, ensure_ascii=False, indent=1) + "\n"
    if "--yaz" in argv:
        with open(CIKTI, "w", encoding="utf-8") as f:
            f.write(metin)
        print("sunucu kanıtı: %d biçim onay servisinde doğrulandı · %d vektör yazıldı → %s" % (n, len(v), CIKTI))
        return 0
    try:
        with open(CIKTI, encoding="utf-8") as f:
            ayni = f.read() == metin
    except OSError:
        ayni = False
    print("sunucu kanıtı: %d biçim onay servisinde doğrulandı · %d vektör · dosya %s"
          % (n, len(v), "GÜNCEL" if ayni else "FARKLI/YOK (--yaz ile üret)"))
    return 0 if ayni else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
