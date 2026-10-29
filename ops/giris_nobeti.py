#!/usr/bin/env python3
"""SSH giriş nöbetçisi — VPS'e kim, nereden girdi; yeni yerden giriş → sahibe Telegram.

Kaynak: `journalctl -u ssh` ("Accepted publickey for ghost from 1.2.3.4 ..."). Günlük
okumak için adm grubu gerekir; sistem birimi `ghost-giris.service` (User=ghost,
SupplementaryGroups=adm) her 5 dk koşar. Durum `ops/giris_state.json`:
  {"cursor": <journal imleci>, "bilinen": {"ip": "son görülme"}, "kurulum": ts}

Kural (Sahip, 2026-09-19: "Ghost'u benim dışımda kimse kullanamamalı, süreci bize
zorlaştırmadan"): uyarı yalnız ŞU üç halde — (1) daha önce görülmemiş IP'den giriş,
(2) parola ile giriş (kapalı olmalı; görülürse yapılandırma bozulmuş demektir),
(3) root girişi (Mac anahtarı dışında beklenmez; IP yeni olsun olmasın). İlk koşuda
geçmiş 30 gün "bilinen" olarak tohumlanır ki kurulum gecesi uyarı yağmasın.

Test edilebilir saf parçalar: `satir_ayristir`, `degerlendir`.
"""
import json
import os
import re
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.environ.get("GHOST_OPS") or os.path.join(BASE, "ops")
STATE = os.path.join(OPS, "giris_state.json")
TOHUM_GUN = 30
DESEN = re.compile(r"Accepted (?P<yontem>\S+) for (?P<kul>\S+) from (?P<ip>[0-9a-fA-F.:]+) port"
                   r"(?:.*?(?P<parmak>SHA256:[A-Za-z0-9+/=]+))?")
# Ghost'un KENDİ root anahtarı (Mac'teki ~/.ssh/ghost_vps, "ghost-jarvis"): reboot rayı,
# derleme ağacı rsync'i gibi işler bununla root@VPS'e girer. Bu anahtarla root girişi
# uyarı DEĞİL — 2026-09-26'da bir sabahta 5 uyarı üretti, hepsi Ghost'un kendi işiydi
# (Sahip: "neden sürekli SSH girişi uyarısı geliyor?"). Anahtar YubiKey kapısının ardındaki
# Mac'te durur; IP değişse de (Sahip seyahatte) kimlik anahtardır, ağ değil.
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from ghost import sahip  # noqa: E402

GHOST_ROOT_PARMAKLAR = set(sahip.ssh_parmak_izleri())   # ops/sahip.json "ssh_parmak_izleri"
AUTHORIZED_KEYS = os.environ.get("GHOST_AUTHORIZED_KEYS") or os.path.expanduser("~/.ssh/authorized_keys")


def yetkili_anahtarlar(yol=None):
    """ghost kullanıcısının authorized_keys'indeki anahtarlar → {SHA256 parmak izi: yorum/ad}.
    `ssh-keygen -lf` yalnız parmak izi + yorum basar; anahtar DEĞERİ okunmaz/basılmaz.

    NEDEN (2026-10-29): "yeni yerden giriş" uyarısı IP'ye bakıyordu; Ghost'un kendi Mac↔VPS
    rsync/ssh'ı (ghost_runtime, yorum "ghost-runtime-nonroot") Mac'in IP'si her değiştiğinde
    uyarı üretti — 30 günde 29, bir günde 18 (hafıza: "o benim"). Kimlik anahtardır, ağ değil:
    bizim anahtarla yeni IP = GHOST sınıfı (Nabız'da görünür, Telegram'a gitmez)."""
    yol = yol or AUTHORIZED_KEYS
    out = {}
    try:
        r = subprocess.run(["ssh-keygen", "-lf", yol], capture_output=True, text=True, timeout=10)
    except Exception:
        return out
    for satir in (r.stdout or "").splitlines():
        p = satir.split()
        if len(p) >= 2 and p[1].startswith("SHA256:"):
            out[p[1]] = " ".join(p[2:-1]) if len(p) > 3 else ""
    return out


def satir_ayristir(satir):
    """journal satırı → {'yontem','kul','ip'} ya da None."""
    m = DESEN.search(satir or "")
    return m.groupdict() if m else None


def degerlendir(giris, bilinen):
    """Uyarı gerekçesi (str) ya da None. `bilinen`: {ip: son_gorulme}."""
    if giris["yontem"] == "password":
        return "PAROLA ile giriş (kapalı olmalıydı!)"
    if giris["kul"] == "root":
        if giris.get("parmak") in GHOST_ROOT_PARMAKLAR:
            return None                       # Ghost'un kendi Mac anahtarı: beklenen kullanım
        return "root girişi" + ("" if giris["ip"] in bilinen else " — YENİ IP")
    if giris["ip"] not in bilinen:
        return "yeni yerden giriş"
    return None


def sinifla(giris, neden, yetkili):
    """Saf: uyarı gerekçesi → "sahip" | "ghost" | None (uyarı yok).

    - parola / root (Ghost anahtarı dışı) / tanınmayan anahtar → SAHİP (bu kalır, önemli).
    - ghost kullanıcısı, authorized_keys'teki BİZİM anahtar, yeni IP → GHOST (Mac'in IP'si
      değişti; Sahip seyahatte ya da operatör NAT'ı). `yetkili`: {parmak: ad}.
    Parmak izi journal'da yoksa anahtarı doğrulayamayız → SAHİP (güvenli taraf)."""
    if not neden:
        return None
    if giris.get("yontem") == "password" or giris.get("kul") == "root":
        return "sahip"
    if giris.get("parmak") and giris["parmak"] in (yetkili or {}):
        return "ghost"
    return "sahip"


def _journal(args):
    # -g Accepted: 30 günlük günlükte on binlerce başarısız deneme var; JSON'a çevirip
    # Python'da elemek ilk koşuda 1.2 GB/20 sn tuttu — süzgeç journald tarafında.
    r = subprocess.run(["journalctl", "-u", "ssh", "--no-pager", "-o", "json", "-g", "Accepted"] + args,
                       capture_output=True, text=True, timeout=60)
    # journalctl -g eşleşme bulamayınca 1 döner (hata değil, "yeni satır yok").
    # 2026-09-19: bu yüzden her sessiz 5 dk'da nöbetçi düşüyordu.
    if r.returncode == 1 and not r.stdout.strip() and not (r.stderr or "").strip():
        return []
    if r.returncode != 0:
        raise RuntimeError("journalctl(%d): %s" % (r.returncode, (r.stderr or "").strip()[:200]))
    out = []
    for line in r.stdout.splitlines():
        try:
            j = json.loads(line)
        except ValueError:
            continue
        out.append((j.get("__CURSOR"), j.get("MESSAGE") or ""))
    return out


def _state_oku():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _state_yaz(d):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def _uyar(metin, sinif="sahip", anahtar=None):
    """Alarm kapısı (ghost/alarm.py, 2026-10-29): SAHİP sınıfı Telegram'a (günlük tavan 5, aynı
    anahtar 6 sa soğuma), GHOST sınıfı yalnız Nabız'a. Eskisi: send_telegram.py alt süreci."""
    if os.environ.get("GHOST_TEST"):
        print("[uyarı bastırıldı — test] (%s)" % sinif, metin)
        return
    from ghost import alarm
    alarm.bildir("giris", metin, sinif=sinif, anahtar=anahtar)


def kos():
    st = _state_oku()
    simdi = time.strftime("%F %T")
    if not st.get("cursor"):
        # ilk koşu: geçmişi tohumla, uyarı verme
        satirlar = _journal(["--since", "-%dd" % TOHUM_GUN])
        bilinen = {}
        for _, msg in satirlar:
            g = satir_ayristir(msg)
            if g:
                bilinen[g["ip"]] = simdi
        st = {"cursor": satirlar[-1][0] if satirlar else None, "bilinen": bilinen,
              "kurulum": simdi}
        if not st["cursor"]:
            # hiç satır yoksa imleç alamayız; bir sonraki koşuda tekrar tohumlanır
            print("tohum: satır yok")
            return 0
        _state_yaz(st)
        print("tohumlandı: %d bilinen IP" % len(bilinen))
        return 0
    satirlar = _journal(["--after-cursor", st["cursor"]])
    bilinen = st.setdefault("bilinen", {})
    yetkili = yetkili_anahtarlar()
    uyarilar, kendi = [], []
    for cursor, msg in satirlar:
        g = satir_ayristir(msg)
        if not g:
            continue
        neden = degerlendir(g, bilinen)
        sinif = sinifla(g, neden, yetkili)
        satir = "• %s: %s@%s (%s) — %s" % (simdi, g["kul"], g["ip"], g["yontem"], neden)
        if sinif == "sahip":
            uyarilar.append(satir)
        elif sinif == "ghost":
            kendi.append(satir + " [bizim anahtar: %s]" % (yetkili.get(g.get("parmak")) or "?"))
        bilinen[g["ip"]] = simdi
    if satirlar:
        st["cursor"] = satirlar[-1][0]
    _state_yaz(st)
    if uyarilar:
        _uyar("🚪 VPS'e SSH girişi:\n" + "\n".join(uyarilar[:10])
              + "\nSen değilsen: /freeze, sonra anahtarları yenileriz.",
              sinif="sahip", anahtar="yabanci-giris")
    if kendi:
        _uyar("🚪 Kendi anahtarımızla yeni IP'den giriş:\n" + "\n".join(kendi[:10]),
              sinif="ghost", anahtar="kendi-anahtar-yeni-ip")
    print("%d satır, %d uyarı (Sahip), %d kendi (Ghost)" % (len(satirlar), len(uyarilar), len(kendi)))
    return 0


if __name__ == "__main__":
    sys.exit(kos())
