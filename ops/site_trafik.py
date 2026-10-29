#!/usr/bin/env python3
"""Site trafiği — sahibi ve botu ayıklayıp gerçek dış ziyaretçiyi say.

NEDEN VAR (2026-09-02): PostHog'un ham sayıları yanıltıyordu. 30 günlük 56
görüntülemenin 10'u sahibin kendi telefonu, 11'i veri merkezi tarayıcısıydı;
yani "56 ziyaret" aslında 35 dış görüntülemeydi. Filtreyi PostHog tarafına
koyamıyoruz — proje anahtarı salt-okunur (`project:write` yok, 403). O yüzden
ayıklama okuma tarafında, burada yapılıyor.

    python3 ops/site_trafik.py [gun]        # özet (varsayılan 30)
    python3 ops/site_trafik.py [gun] --kim  # kimlik kimlik döküm

Ortam: POSTHOG_HOST, POSTHOG_API_KEY, POSTHOG_PROJECT_ID (secrets.env).
"""
import json, os, sys, urllib.request

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from ghost import sahip  # noqa: E402  (profil: ops/sahip.json)

SITELER = sahip.trafik_siteleri()   # ops/sahip.json "trafik": true olan alanlar

# sahibin telefonu: iPhone 17 → 402x874 nokta, iOS 26+. iPhone 17 iOS 18
# çalıştıramaz, bu yüzden aynı ekranda iOS 18 gören kayıt başkasıdır.
#
# 2026-09-28 DÜZELTME (Sahip sorusu "gizli sekmeden açıyorum"): Safari 26'nın
# UA'sı iOS sürümünü "18_7" olarak DONDURUYOR (Chrome iOS gerçek 26_x basar);
# gizli sekmede ise parmak izi koruması ekranı 414x896 gibi SAHTE bildiriyor.
# Görünüm (viewport) genişliği ise değişmiyor: 402. Bu yüzden imza artık
# "Safari 26 + görünüm 402" (normal ya da gizli sekme) VEYA "iOS 26 + 402x874"
# (Chrome). Ekran 414x896 + görünüm 402 = gizli sekmedeki iPhone 17; gerçek
# iPhone 11/XR'ın görünümü 414 olurdu. Bir hafta boyunca 402x874 ve 414x896
# Safari kayıtlarının HEPSİ Version/26.6.1 + görünüm 402x714 çıktı: tek telefon.
# Sınır: iPhone 16 Pro/17 sınıfı Safari 26 kullanan BAŞKASI da "sahip" sayılır.
# 2026-09-30 Sahip düzeltmesi: bu imza KİMLİK DEĞİL, cihaz sınıfıdır. TR mobil
# operatörlerin geoip'i de hep Ankara'ya düşer, konum ayırt etmez; PostHog
# $ip saklamıyor. Kutu artık "sen olabilirsin" diye raporlanır, Sahip diye
# değil. Kesin ayrım için siteye sahip işareti (opt-out) gerekir — kurulmadı.
SAHIP_EKRAN = (402.0, 874.0)
SAHIP_IOS = "26"
SAHIP_GORUNUM = 402.0
SAHIP_SAFARI = "26"

# Bot ölçüleri: headless tarayıcıların varsayılan pencereleri. Gerçek bir
# cihazda bu kombinasyonlar pratikte görülmüyor.
BOT_EKRAN = {(800.0, 600.0), (1600.0, 1200.0), (1280.0, 1280.0), (1280.0, 720.0), (2000.0, 2000.0)}
# Veri merkezi şehirleri (ekran ölçüsü ele vermezse ikinci kapı).
BOT_SEHIR = {"Council Bluffs", "Ashburn", "Eatonton", "North Charleston", "Boardman"}


def sorgu(hogql):
    from ghost import vekil     # sır vekili kuruluysa kimliği vekil ekler (Faz 2)
    pid = os.environ["POSTHOG_PROJECT_ID"]
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": hogql}}).encode()
    req = urllib.request.Request(
        vekil.url("posthog", "/api/projects/%s/query/" % pid), data=body,
        headers=dict(vekil.basliklar("posthog"), **{"Content-Type": "application/json"}))
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["results"]


def kimlikler(gun):
    """Her distinct_id için tek satır: site, cihaz izi, kaynak, sayı."""
    q = """SELECT properties.$host, distinct_id,
 any(properties.$os), any(properties.$os_version),
 any(properties.$screen_width), any(properties.$screen_height),
 any(properties.$geoip_city_name), any(properties.$geoip_subdivision_1_name),
 any(properties.$viewport_width), any(properties.$browser), any(properties.$browser_version),
 arrayStringConcat(groupUniqArray(toString(properties.$referring_domain)), ','),
 count(), min(timestamp), max(timestamp)
FROM events WHERE event = '$pageview'
 AND properties.$host IN (%s)
 AND timestamp > now() - INTERVAL %d DAY
GROUP BY properties.$host, distinct_id ORDER BY count() DESC LIMIT 10000""" % (
        ", ".join("'%s'" % s for s in SITELER), gun)
    return [dict(zip(
        ("site", "kimlik", "os", "osv", "gen", "yuk", "sehir", "bolge",
         "vpgen", "tarayici", "tarsur", "kaynak", "olay", "ilk", "son"), r))
        for r in sorgu(q)]


def sinifla(k):
    """'sahip' | 'bot' | 'dis' — hangi kutuya düştüğü."""
    ekran = (k["gen"], k["yuk"])
    if k["os"] == "iOS" and str(k["osv"] or "").startswith(SAHIP_IOS) \
            and ekran == SAHIP_EKRAN:
        return "sahip"  # Chrome iOS: gerçek sürüm + gerçek ekran
    if k["os"] == "iOS" and (k.get("tarayici") or "") == "Mobile Safari" \
            and str(k.get("tarsur") or "").startswith(SAHIP_SAFARI) \
            and k.get("vpgen") == SAHIP_GORUNUM:
        return "sahip"  # Safari 26: UA 18_7 donuk, gizli sekmede ekran sahte
    if ekran in BOT_EKRAN or k["sehir"] in BOT_SEHIR:
        return "bot"
    return "dis"


def main():
    gun = 30
    arg = [a for a in sys.argv[1:] if not a.startswith("-")]
    if arg:
        gun = int(arg[0])
    ayrinti = "--kim" in sys.argv

    kayit = kimlikler(gun)
    for k in kayit:
        k["sinif"] = sinifla(k)

    kutu = {"sahip": [], "bot": [], "dis": []}
    for k in kayit:
        kutu[k["sinif"]].append(k)

    def topla(ad):
        return sum(k["olay"] for k in kutu[ad]), len(kutu[ad])

    print("== son %d gün · %d site ==" % (gun, len(SITELER)))
    for ad, etiket in (("dis", "gerçek dış"), ("sahip", "iPhone Pro sınıfı (sen olabilirsin)"),
                       ("bot", "bot / veri merkezi")):
        o, n = topla(ad)
        print("  %-24s %3d görüntüleme · %2d kimlik" % (etiket, o, n))
    toplam = sum(k["olay"] for k in kayit)
    dis_o = topla("dis")[0]
    print("  %-24s %3d görüntüleme  (dışarı payı %%%d)"
          % ("TOPLAM", toplam, round(100 * dis_o / toplam) if toplam else 0))

    print("  not: 'iPhone Pro sınıfı' kutusu cihaz sınıfıdır, kimlik değil; Sahip varsayılmaz.")
    print("\n-- site site (yalnız gerçek dış) --")
    per = {}
    for k in kutu["dis"]:
        per.setdefault(k["site"], [0, 0])
        per[k["site"]][0] += k["olay"]
        per[k["site"]][1] += 1
    for s in sorted(per, key=lambda s: -per[s][0]):
        print("  %-18s %3d görüntüleme · %2d kimlik" % (s, per[s][0], per[s][1]))
    for s in SITELER:
        if s not in per:
            print("  %-18s   0" % s)

    # İmza çürümesi nöbeti: "sahip" Safari 26 + görünüm 402'ye dayanıyor;
    # aynı sınıf telefonu olan başkası da yakalanır. Türkiye dışından gelen
    # "sahip" kaydı bu çürümenin ilk belirtisidir (Sahip seyahatteyse yanlış
    # alarm olabilir, o yüzden uyarı, hüküm değil).
    yabanci = [k for k in kutu["sahip"] if k["sehir"] and k["sehir"] not in
               sahip.ev_sehirleri()]
    if yabanci:
        print("\n⚠ imza uyarısı: %d 'sahip' kimliği alışılmadık şehirden (%s). "
              "iPhone 17 sınıfı + Safari 26 başkası da olabilir."
              % (len(yabanci), ", ".join(sorted({k["sehir"] for k in yabanci}))))

    if ayrinti:
        print("\n-- kimlik dökümü --")
        for k in sorted(kayit, key=lambda k: (k["sinif"], -k["olay"])):
            print("  [%-5s] %-16s %-4s %-7s%-7s %4.0fx%-4.0f %-14s ev=%-2d %s"
                  % (k["sinif"], k["site"], k["kimlik"][-4:], k["os"] or "-",
                     k["osv"] or "", k["gen"] or 0, k["yuk"] or 0,
                     (k["sehir"] or k["bolge"] or "-")[:14], k["olay"],
                     k["kaynak"][:40]))


if __name__ == "__main__":
    main()
