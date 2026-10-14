#!/usr/bin/env python3
"""Mac özel veri istemcisi (VPS'ten) — Notlar, Mesajlar, Fotoğraflar, Dosyalar (iCloud Drive).

Mac'teki `ai.jarvis.veri` okuyucusuna (127.0.0.1:8766, Tam Disk Erişimi'ne sahip TEK süreç)
hızlı hat üzerinden `curl` ile ulaşır; tünelde ayrı port YOK, token Mac'te durur.
Kaynak: `ops/mac_veri_okuyucu.py`. (Safari/Mail kısıtı 2026-09-28'de Sahip kararıyla kalktı.)

Kullanım:
  python3 ops/mac_veri.py durum
  python3 ops/mac_veri.py notlar [--son 20] [--q metin] [--uzunluk 400] [--json]
  python3 ops/mac_veri.py not <id>                      # tek not, tam gövde
  python3 ops/mac_veri.py mesajlar [--son 30] [--kisi X] [--q metin] [--json]
  python3 ops/mac_veri.py fotograflar [--son 20] [--gun 7] [--json]
  python3 ops/mac_veri.py foto <uuid> <hedef.jpg> [--boy kucuk|orta|asil]
  python3 ops/mac_veri.py dosyalar [yol] [--q ad] [--son 100] [--derin] [--sirala tarih] [--json]
                                   # iCloud Drive; yol köke göre ("" = kaplar, "com~apple~CloudDocs/Belgeler")
  python3 ops/mac_veri.py dosya <yol> [hedef]         # metinse basar; ikiliyse hedefe (yoksa /tmp) yazar
  python3 ops/mac_veri.py whatsapp [--son 50]                        # sohbet listesi
  python3 ops/mac_veri.py whatsapp <sohbet adı|id> [--son 30] [--q]   # bir sohbetin mesajları
  python3 ops/mac_veri.py whatsapp --q metin                          # tüm sohbetlerde ara
  (WhatsApp medyası: dosya --kok whatsapp 'Message/Media/...' <hedef>)

iPhone YEDEĞİ (2026-09-28, Sahip "finderı bağla"; ~/.ghost/yedek, idevicebackup2, şifresiz, anlık görüntü):
  python3 ops/mac_veri.py yedek                                  # durum: cihaz, tarih, bitti mi, alanlar
  python3 ops/mac_veri.py yedek --liste [--domain X] [--q ad]    # Manifest'te dosya ara
  python3 ops/mac_veri.py mesajlar --kaynak yedek [...]          # SMS/iMessage tam geçmiş (rehber adlı)
  python3 ops/mac_veri.py whatsapp --kaynak yedek [...]          # WhatsApp tam geçmiş
  python3 ops/mac_veri.py aramalar [--son 30] [--kisi ad|numara]
  python3 ops/mac_veri.py rehber [--q ad] [--son 50]
  python3 ops/mac_veri.py safari [--son 30] [--q metin]          # Safari geçmişi
  python3 ops/mac_veri.py mail [--son 30] [--q] [--kimden] [--hesap gmail] [--kutu INBOX] [--bas/--bit tarih]
                                                                  # Mac Mail (Gmail+Hotmail+..., canlı, Mail.app hesapları)
  python3 ops/mac_veri.py mail <id> [--uzunluk 4000]              # tek mail, düz metin gövde + ekler
  python3 ops/mac_veri.py mail --hesaplar                          # hesaplar ve kutular
  python3 ops/mac_veri.py dosya --kok yedek '<domain>/<relativePath>' [hedef]
  (yedek WhatsApp medyası: dosya --kok yedek 'AppDomainGroup-group.net.whatsapp.WhatsApp.shared/Message/Media/...')
"""
import argparse
import base64
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mac  # noqa: E402  (ops/mac.py — hızlı hat istemcisi)

CURL = ("curl -s -m %d -H \"X-Ghost-Token: $(cat $HOME/.ghost_maclink_token)\" "
        "'http://127.0.0.1:8766/%s'")


def iste(uc, params=None, zaman=90):
    """Okuyucu ucunu çağır, JSON döndür. Hata → dict {'hata': ...}."""
    q = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v not in (None, "", 0)})
    yol = uc + ("?" + q if q else "")
    r = mac.komut(CURL % (zaman, yol), zaman=zaman + 10)
    if not isinstance(r, dict):
        return {"hata": "hızlı hat cevap vermedi: %r" % (r,)}
    if r.get("hata"):
        return {"hata": r["hata"]}
    metin = r.get("cikti") or ""
    if not metin:
        return {"hata": "boş cevap (okuyucu ajan kapalı mı? launchctl print gui/501/ai.jarvis.veri)"}
    try:
        return json.loads(metin)
    except ValueError:
        return {"hata": "JSON değil: " + metin[:200]}


def _boyut(b):
    if b is None:
        return "      "
    for birim in ("B", "K", "M", "G"):
        if b < 1024 or birim == "G":
            return ("%5.1f%s" % (b, birim)) if birim != "B" else "%5d%s" % (b, birim)
        b /= 1024.0


def dosya_satiri(g, tam_yol=False):
    """Listeleme satırı: klasör 📁, buluttaki dosya ☁, yerel dosya boyutuyla."""
    ad = g["yol"] if tam_yol else g["ad"]
    if g.get("kap") and g["kap"] != g["ad"]:
        ad += "  → " + g["kap"]
    if g["klasor"]:
        return "📁 %s/" % ad
    return "%s %s %s%s" % (_boyut(g["bayt"]), g["degisti"], "☁ " if g["bulutta"] else "", ad)


def _kisalt(s, n):
    s = (s or "").replace("\n", " ⏎ ")
    return s if len(s) <= n else s[:n] + "…"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("durum")
    p = sub.add_parser("notlar"); p.add_argument("--son", type=int, default=20); p.add_argument("--q", default="")
    p.add_argument("--uzunluk", type=int, default=400); p.add_argument("--json", action="store_true")
    p = sub.add_parser("not"); p.add_argument("id", type=int)
    p = sub.add_parser("mesajlar"); p.add_argument("--son", type=int, default=30); p.add_argument("--kisi", default="")
    p.add_argument("--q", default=""); p.add_argument("--json", action="store_true")
    p.add_argument("--kaynak", default="", choices=("", "yedek"))
    p = sub.add_parser("yedek"); p.add_argument("--liste", action="store_true"); p.add_argument("--domain", default="")
    p.add_argument("--q", default=""); p.add_argument("--son", type=int, default=100); p.add_argument("--json", action="store_true")
    p = sub.add_parser("aramalar"); p.add_argument("--son", type=int, default=30); p.add_argument("--kisi", default="")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("rehber"); p.add_argument("--q", default=""); p.add_argument("--son", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("safari"); p.add_argument("--son", type=int, default=30); p.add_argument("--q", default=""); p.add_argument("--json", action="store_true")
    p = sub.add_parser("mail"); p.add_argument("id", nargs="?"); p.add_argument("--son", type=int, default=30)
    p.add_argument("--q", default=""); p.add_argument("--kimden", default=""); p.add_argument("--hesap", default="")
    p.add_argument("--kutu", default=""); p.add_argument("--bas", default=""); p.add_argument("--bit", default="")
    p.add_argument("--uzunluk", type=int, default=4000); p.add_argument("--hesaplar", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("fotograflar"); p.add_argument("--son", type=int, default=20); p.add_argument("--gun", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("foto"); p.add_argument("uuid"); p.add_argument("hedef"); p.add_argument("--boy", default="orta")
    p = sub.add_parser("dosyalar"); p.add_argument("yol", nargs="?", default=""); p.add_argument("--q", default="")
    p.add_argument("--son", type=int, default=100); p.add_argument("--derin", action="store_true")
    p.add_argument("--sirala", default="ad", choices=("ad", "tarih")); p.add_argument("--json", action="store_true")
    p.add_argument("--kok", default="icloud", choices=("icloud", "whatsapp"))
    p = sub.add_parser("dosya"); p.add_argument("yol"); p.add_argument("hedef", nargs="?")
    p.add_argument("--kok", default="icloud", choices=("icloud", "whatsapp", "yedek"))
    p = sub.add_parser("sema"); p.add_argument("depo")
    p = sub.add_parser("whatsapp"); p.add_argument("sohbet", nargs="?", default=""); p.add_argument("--q", default="")
    p.add_argument("--son", type=int, default=0); p.add_argument("--json", action="store_true")
    p.add_argument("--bas", default=""); p.add_argument("--bit", default="")   # tarih aralığı, yerel
    p.add_argument("--kaynak", default="", choices=("", "yedek"))
    a = ap.parse_args(argv)

    if a.cmd == "durum":
        o = iste("durum")
        print(json.dumps(o, ensure_ascii=False)); return 0 if "hata" not in o else 1
    if a.cmd == "notlar":
        o = iste("notlar", {"son": a.son, "q": a.q, "uzunluk": a.uzunluk})
        if isinstance(o, dict):
            print("HATA:", o.get("hata")); return 1
        if a.json:
            print(json.dumps(o, ensure_ascii=False, indent=1)); return 0
        for n in o:
            print("#%d · %s · %s%s\n   %s" % (n["id"], n["degisti"], _kisalt(n["baslik"], 60),
                                              " 🔒" if n["kilitli"] else "", _kisalt(n["govde"], 160)))
        return 0
    if a.cmd == "not":
        o = iste("notlar", {"id": a.id, "son": 1})
        if isinstance(o, dict) or not o:
            print("HATA:", o.get("hata") if isinstance(o, dict) else "not yok"); return 1
        n = o[0]
        print("# %s\n(%s · değişti %s · klasör %s)\n\n%s" % (n["baslik"], n["id"], n["degisti"], n["klasor"], n["govde"]))
        return 0
    if a.cmd == "mesajlar":
        o = iste("mesajlar", {"son": a.son, "kisi": a.kisi, "q": a.q, "kaynak": a.kaynak}, zaman=180)
        if isinstance(o, dict):
            print("HATA:", o.get("hata")); return 1
        if a.json:
            print(json.dumps(o, ensure_ascii=False, indent=1)); return 0
        if not o:
            print("(mesaj yok — Mac'te Mesajlar iCloud senkronu kapalı; tam geçmiş için --kaynak yedek)"); return 0
        for m in o:
            print("%s · %s → %s%s: %s" % (m["tarih"], m["kimden"], m["sohbet"] or "",
                                          " 📎" if m["ekli"] else "", _kisalt(m["metin"], 200)))
        return 0
    if a.cmd == "fotograflar":
        o = iste("fotograflar", {"son": a.son, "gun": a.gun})
        if isinstance(o, dict):
            print("HATA:", o.get("hata")); return 1
        if a.json:
            print(json.dumps(o, ensure_ascii=False, indent=1)); return 0
        for f in o:
            print("%s · %s · %s %sx%s%s%s · %s" % (
                f["tarih"], f["tur"], f["dosya"], f["boyut"][0], f["boyut"][1],
                " · %s,%s" % tuple(f["konum"]) if f["konum"] else "",
                " ★" if f["favori"] else "", f["uuid"]))
        return 0
    if a.cmd == "foto":
        o = iste("foto", {"uuid": a.uuid, "boy": a.boy}, zaman=120)
        if "jpeg_b64" not in o:
            print("HATA:", o.get("hata")); return 1
        with open(a.hedef, "wb") as f:
            f.write(base64.b64decode(o["jpeg_b64"]))
        print("%s ← %s (%s, %d bayt)" % (a.hedef, a.uuid, o["kaynak"], o["bayt"])); return 0
    if a.cmd == "whatsapp":
        if a.sohbet or a.q or a.bas or a.bit:
            o = iste("whatsapp", {"sohbet": a.sohbet, "q": a.q, "son": a.son or 30, "bas": a.bas, "bit": a.bit,
                                  "kaynak": a.kaynak}, zaman=180)
        else:
            o = iste("whatsapp", {"son": a.son or 50, "kaynak": a.kaynak}, zaman=180)
        if isinstance(o, dict):
            print("HATA:", o.get("hata")); return 1
        if a.json:
            print(json.dumps(o, ensure_ascii=False, indent=1)); return 0
        if not (a.sohbet or a.q or a.bas or a.bit):
            for sh in o:
                print("#%d · %s%s%s · %d mesaj · son %s: %s" % (
                    sh["id"], sh["ad"] or sh["jid"], " 👥" if sh["grup"] else "",
                    " (%d yeni)" % sh["okunmamis"] if sh["okunmamis"] else "", sh["mesaj"], sh["son"],
                    _kisalt(sh["son_metin"], 80)))
            return 0
        for m in reversed(o):   # eskiden yeniye oku
            ek = "" if m["tur"] == "metin" else "[%s%s] " % (m["tur"], ": " + m["baslik"] if m["baslik"] else "")
            onek = ("%s › " % m["sohbet"]) if (a.q or a.bas or m["grup"]) and not a.sohbet else ""
            print("%s · %s%s: %s%s" % (m["tarih"], onek, m["kim"], ek, _kisalt(m["metin"], 300)))
        if not o:
            print("(mesaj yok)")
        return 0
    if a.cmd == "yedek":
        if a.liste:
            o = iste("yedek", {"liste": 1, "domain": a.domain, "q": a.q, "son": a.son}, zaman=120)
            if "girdi" not in o:
                print("HATA:", o.get("hata")); return 1
            if a.json:
                print(json.dumps(o, ensure_ascii=False, indent=1)); return 0
            if o.get("domainler"):
                print("DOMAINLER (%d): %s\n" % (len(o["domainler"]), ", ".join(o["domainler"][:60])))
            print("\n".join(o["girdi"]) or "(yok)"); return 0
        o = iste("yedek", zaman=120)
        if a.json or "var" not in o:
            print(json.dumps(o, ensure_ascii=False, indent=1)); return 0 if "hata" not in o else 1
        if not o["var"]:
            print("yedek YOK (%s)" % o["kok"]); return 1
        print("%s · iOS %s · %s · yedek %s · %s · %s dosya" % (
            o.get("device_name"), o.get("product_version"), o.get("udid"), o.get("date"),
            "BİTTİ" if o["bitti"] else "SÜRÜYOR (%s)" % o.get("snapshotstate"), o.get("dosya_sayisi")))
        print("alanlar: " + ", ".join("%s%s" % (k, "" if v is True else (" (Mac iCloud kopyası)" if v == "mac" else " ✗"))
                                      for k, v in o["alanlar"].items()))
        return 0
    if a.cmd == "aramalar":
        o = iste("aramalar", {"son": a.son, "kisi": a.kisi}, zaman=180)
        if isinstance(o, dict):
            print("HATA:", o.get("hata")); return 1
        if a.json:
            print(json.dumps(o, ensure_ascii=False, indent=1)); return 0
        for r in o:
            print("%s · %s %s%s · %s · %s" % (r["tarih"], "→" if r["benden"] else "←", r["kim"],
                                              "" if r["kim"] == r["numara"] else " (%s)" % r["numara"],
                                              "%d sn" % r["sure_sn"] if r["cevaplandi"] else "cevapsız", r["tur"]))
        if not o:
            print("(arama yok)")
        return 0
    if a.cmd == "rehber":
        o = iste("rehber", {"q": a.q, "son": a.son}, zaman=180)
        if isinstance(o, dict):
            print("HATA:", o.get("hata")); return 1
        if a.json:
            print(json.dumps(o, ensure_ascii=False, indent=1)); return 0
        for r in o:
            print("%s%s · %s" % (r["ad"], " (%s)" % r["kurum"] if r["kurum"] and r["kurum"] != r["ad"] else "",
                                 " · ".join(r["tel"] + r["eposta"]) or "-"))
        if not o:
            print("(kişi yok)")
        return 0
    if a.cmd == "safari":
        o = iste("safari", {"son": a.son, "q": a.q}, zaman=180)
        if isinstance(o, dict):
            print("HATA:", o.get("hata")); return 1
        if a.json:
            print(json.dumps(o, ensure_ascii=False, indent=1)); return 0
        for r in o:
            print("%s · %s · %s" % (r["tarih"], _kisalt(r["baslik"], 70), _kisalt(r["url"], 100)))
        if not o:
            print("(geçmiş yok)")
        return 0
    if a.cmd == "mail":
        if a.id:
            o = iste("mail", {"id": a.id, "uzunluk": a.uzunluk}, zaman=120)
            if o.get("hata"):
                print("HATA:", o["hata"]); return 1
            if a.json:
                print(json.dumps(o, ensure_ascii=False, indent=1)); return 0
            print("Kimden: %s\nKime: %s%s\nTarih: %s\nKonu: %s%s%s\n" % (
                o["kimden"], o["kime"], ("\nCc: " + o["cc"]) if o.get("cc") else "", o["tarih"], o["konu"],
                ("\nEkler: " + ", ".join(o["ekler"])) if o.get("ekler") else "",
                "\n(kısmi indirme — gövde eksik olabilir)" if o.get("kismi") else ""))
            print(o["metin"] or "(boş gövde)"); return 0
        if a.hesaplar:
            o = iste("mail", {"hesaplar": 1}, zaman=180)
            if o.get("hata"):
                print("HATA:", o["hata"]); return 1
            if a.json:
                print(json.dumps(o, ensure_ascii=False, indent=1)); return 0
            for h in o["hesaplar"]:
                print("%s · %d mesaj" % (h["hesap"], h["toplam"]))
                for k, n in sorted(h["kutular"].items(), key=lambda x: -x[1])[:12]:
                    print("   %-30s %d" % (k, n))
            for t in o.get("tanimlar") or []:
                print("tanım:", t)
            return 0
        o = iste("mail", {"son": a.son, "q": a.q, "kimden": a.kimden, "hesap": a.hesap, "kutu": a.kutu,
                          "bas": a.bas, "bit": a.bit}, zaman=180)
        if isinstance(o, dict):
            print("HATA:", o.get("hata")); return 1
        if a.json:
            print(json.dumps(o, ensure_ascii=False, indent=1)); return 0
        for r in o:
            print("#%-7s %s · %s%s · %s · %s" % (r["id"], r["tarih"], "" if r["okundu"] in (True, None) else "● ",
                                              _kisalt(r["kimden"], 32), _kisalt(r["konu"], 60),
                                              _kisalt((r["hesap"].split("@")[0] + "/" + r["kutu"]), 28)))
        if not o:
            print("(mail yok)")
        return 0
    if a.cmd == "sema":
        r = mac.komut(CURL % (60, "sema?" + a.depo), zaman=70)
        print((r or {}).get("cikti") or r); return 0
    if a.cmd == "dosyalar":
        o = iste("dosyalar", {"yol": a.yol, "q": a.q, "son": a.son, "derin": 1 if a.derin else 0,
                              "sirala": a.sirala if a.sirala != "ad" else "",
                              "kok": a.kok if a.kok != "icloud" else ""}, zaman=60)
        if "girdi" not in o:
            print("HATA:", o.get("hata")); return 1
        if a.json:
            print(json.dumps(o, ensure_ascii=False, indent=1)); return 0
        print("📂 /%s%s" % (o["yol"], "  (kesildi — --son artır ya da --q ile daralt)" if o["kesildi"] else ""))
        for g in o["girdi"]:
            print(dosya_satiri(g, a.derin or bool(a.q)))
        if not o["girdi"]:
            print("(boş)")
        return 0
    if a.cmd == "dosya":
        o = iste("dosya", {"yol": a.yol, "kok": a.kok if a.kok != "icloud" else ""}, zaman=120)
        if "tur" not in o:
            print("HATA:", o.get("hata")); return 1
        if o["tur"] == "metin" and not a.hedef:
            print(o["metin"]); return 0
        hedef = a.hedef or os.path.join("/tmp", "ghost_dosya_" + os.path.basename(o["yol"]))
        with open(hedef, "wb") as f:
            f.write(o["metin"].encode("utf-8") if o["tur"] == "metin" else base64.b64decode(o["b64"]))
        print("%s ← %s (%s, %d bayt)" % (hedef, o["yol"], o["tur"], o["bayt"])); return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
