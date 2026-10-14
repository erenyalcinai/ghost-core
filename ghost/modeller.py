"""Model takma adı → EN GÜNCEL tam kimlik (Sahip kararı 2026-10-01).

Sorun: "opus" takma adını Claude CLI kendi içinde çözer ve o eşleme CLI'nin
DERLENDİĞİ güne aittir. Köprü 2.1.258 koşarken Opus 5.5 çıktı; "opus" hâlâ
claude-opus-5'e gidiyordu ve CLI tanımadığı tam kimliği (claude-opus-5-5) de
reddediyordu (`unrecognized_model`, 0 token). Yani iki bağımlılık var:
  1. API hangi modelleri sunuyor (canlı: GET /v1/models, OAuth belirteci yeter);
  2. CLI hangi kimlikleri tanıyor (ikilinin içindeki dizeler).
Takma ad = o ailede HER İKİ listede de olan en yüksek sürüm. API daha yenisini
sunuyor ama CLI bilmiyorsa `uyari()` söyler → selfcheck CLI'yi günlük günceller.

SAF çekirdek (surum/aile/en_yeni/coz) ağa ve diske dokunmaz; kabuk
(api_modeller/cli_modeller) önbelleklidir (ops/modeller.json). Testte
(GHOST_TEST) ağa çıkılmaz, ikili okunmaz.

Kullanım: `python3 -m ghost.modeller coz opus` → claude-opus-5-5 ·
`python3 -m ghost.modeller durum` → tablo. Koddan: `modeller.tam_model("opus")`.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from ghost import config

TAKMA_ADLAR = ("fable", "opus", "sonnet", "haiku")
_ID_RE = re.compile(rb"claude-(?:fable|opus|sonnet|haiku|mythos)-[0-9]+(?:-[0-9]+)*")
_SURUM_RE = re.compile(r"^claude-(fable|opus|sonnet|haiku|mythos)((?:-[0-9]+)+)$")
API_URL = "https://api.anthropic.com/v1/models"
API_TTL = 6 * 3600          # canlı liste bu kadar taze sayılır
API_HATA_TTL = 15 * 60      # ağ hatasında eski liste bu kadar daha kullanılır


def _onbellek_yolu():
    return os.path.join(config.OPS, "modeller.json")


# ---------------------------------------------------------------- SAF çekirdek
def aile(model_id):
    """'claude-opus-5-5' → 'opus'; eşleşmezse None. SAF."""
    m = _SURUM_RE.match(model_id or "")
    return m.group(1) if m else None


def surum(model_id):
    """Sürüm demeti: 'claude-opus-5-5' → (5, 5); 'claude-haiku-4-5-20251001' →
    (4, 5, 20251001). Tarihli kimlik aynı sayılı tarihsizden sonra gelir
    (daha uzun demet büyüktür) — ikisi de aynı modeli gösterir, zararsız. SAF."""
    m = _SURUM_RE.match(model_id or "")
    if not m:
        return ()
    return tuple(int(p) for p in m.group(2).strip("-").split("-"))


def en_yeni(takma, kimlikler):
    """Ailedeki en yüksek sürümlü kimlik; yoksa None. SAF."""
    adaylar = [k for k in (kimlikler or ()) if aile(k) == takma]
    return max(adaylar, key=surum) if adaylar else None


def coz(takma, api_ids, cli_ids):
    """Takma ad → tam kimlik. SAF.

    Öncelik: API ∩ CLI'nin en yenisi (ikisi de biliyor → kesin çalışır).
    API listesi yoksa (ağ yok) CLI'nin bildiği en yeni. CLI listesi yoksa
    (ikili okunamadı) API'nin en yenisi. Hiçbiri yoksa None → takma ad
    olduğu gibi CLI'ye gider, CLI kendi eşlemesini uygular."""
    takma = (takma or "").lower()
    if takma not in TAKMA_ADLAR:
        return None
    api = set(api_ids or ())
    cli = set(cli_ids or ())
    if api and cli:
        return en_yeni(takma, api & cli)
    return en_yeni(takma, api or cli)


def uyari_metni(takma, api_ids, cli_ids):
    """API daha yeni sunuyor ama CLI tanımıyorsa tek satır uyarı; yoksa ''. SAF."""
    api_yeni = en_yeni(takma, api_ids)
    secilen = coz(takma, api_ids, cli_ids)
    if api_yeni and secilen and surum(api_yeni) > surum(secilen):
        return ("%s: API'de %s var, CLI tanımıyor (%s kullanılıyor) — CLI güncellenmeli"
                % (takma, api_yeni, secilen))
    return ""


def ikili_kimlikleri(veri):
    """Ham ikili içeriğinden model kimliklerini süz. SAF (bayt dizisi alır)."""
    return sorted({m.decode() for m in _ID_RE.findall(veri or b"")})


# ---------------------------------------------------------------- kabuk (önbellek)
def _oku():
    try:
        with open(_onbellek_yolu(), encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _yaz(d):
    yol = _onbellek_yolu()
    try:
        os.makedirs(os.path.dirname(yol), exist_ok=True)
        tmp = yol + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, yol)
    except OSError:
        pass


def claude_bin():
    env = config.load_env()
    return (os.environ.get("CLAUDE_BIN") or env.get("CLAUDE_BIN")
            or os.path.expanduser("~/.local/bin/claude"))


def cli_modeller(bin_yolu=None, onbellek=None):
    """CLI ikilisinin tanıdığı kimlikler. İkili (yol, boyut, mtime) değişmediyse
    önbellekten; değiştiyse (güncelleme) baştan taranır (~240 MB, 1-2 sn)."""
    if os.environ.get("GHOST_TEST"):
        return []
    yol = bin_yolu or claude_bin()
    try:
        yol = os.path.realpath(yol)
        st = os.stat(yol)
    except OSError:
        return []
    imza = "%s|%d|%d" % (yol, st.st_size, int(st.st_mtime))
    d = _oku() if onbellek is None else onbellek
    c = d.get("cli") or {}
    if c.get("imza") == imza and c.get("ids"):
        return list(c["ids"])
    ids = set()
    try:
        with open(yol, "rb") as f:
            kuyruk = b""
            while True:
                parca = f.read(8 * 1024 * 1024)
                if not parca:
                    break
                ids.update(ikili_kimlikleri(kuyruk + parca))
                kuyruk = parca[-64:]
    except OSError:
        return []
    d["cli"] = {"imza": imza, "ids": sorted(ids), "ts": int(time.time())}
    if onbellek is None:
        _yaz(d)
    return sorted(ids)


def _api_cek(token, timeout=15):
    req = urllib.request.Request(API_URL, headers={
        "Authorization": "Bearer " + token,
        "anthropic-beta": "oauth-2025-04-20",
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return sorted(m["id"] for m in d.get("data", []) if m.get("id"))


def api_modeller(onbellek=None, simdi=None):
    """API'nin sunduğu kimlikler (önbellek 6 sa; ağ hatasında eski liste 15 dk
    daha, sonra boş → coz CLI listesine düşer). Testte ağa çıkılmaz."""
    if os.environ.get("GHOST_TEST"):
        return []
    simdi = simdi or time.time()
    d = _oku() if onbellek is None else onbellek
    a = d.get("api") or {}
    if a.get("ids") and simdi - a.get("ts", 0) < API_TTL:
        return list(a["ids"])
    if simdi - a.get("hata_ts", 0) < API_HATA_TTL:   # az önce düştü, her turda deneme
        return list(a.get("ids") or [])
    token = config.load_env().get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
        return list(a.get("ids") or [])
    try:
        ids = _api_cek(token)
    except Exception as e:  # ağ/auth — sessiz, eski liste kısa süre daha geçerli
        a["hata"] = "%r" % e
        a["hata_ts"] = int(simdi)
        d["api"] = a
        if onbellek is None:
            _yaz(d)
        if simdi - a.get("ts", 0) < API_TTL + API_HATA_TTL:
            return list(a.get("ids") or [])
        return []
    d["api"] = {"ids": ids, "ts": int(simdi)}
    if onbellek is None:
        _yaz(d)
    return ids


def tam_model(model):
    """Takma ad ise en güncel tam kimlik; tam kimlik/boş ise olduğu gibi.
    Çözülemezse takma ad geri döner (CLI kendi eşlemesini uygular)."""
    m = (model or "").strip()
    if m.lower() not in TAKMA_ADLAR:
        return m
    try:
        return coz(m, api_modeller(), cli_modeller()) or m
    except Exception:
        return m


def uyari():
    """Bayat CLI uyarıları (satır listesi; boş = sorun yok)."""
    try:
        api, cli = api_modeller(), cli_modeller()
    except Exception:
        return []
    if not api or not cli:
        return []
    return [u for u in (uyari_metni(t, api, cli) for t in TAKMA_ADLAR) if u]


def durum_satirlari():
    api, cli = api_modeller(), cli_modeller()
    out = []
    for t in TAKMA_ADLAR:
        secilen = coz(t, api, cli) or "(CLI eşlemesi)"
        ek = uyari_metni(t, api, cli)
        out.append("%-6s → %s%s" % (t, secilen, ("  ⚠️ " + ek) if ek else ""))
    out.append("CLI: %s · bilinen %d kimlik · API %d model" % (claude_bin(), len(cli), len(api)))
    return out


def cli_guncelle(bin_yolu=None, timeout=300):
    """`claude update` koşar (yerel kurulum). (eski_sürüm, yeni_sürüm, hata) döner.
    Koşan süreçler etkilenmez: yeni ikili ayrı dosyaya iner, sembolik bağ döner."""
    yol = bin_yolu or claude_bin()

    def _surum():
        try:
            p = subprocess.run([yol, "--version"], capture_output=True, text=True, timeout=30)
            return (p.stdout or "").strip().split()[0]
        except Exception:
            return ""
    eski = _surum()
    try:
        p = subprocess.run([yol, "update"], capture_output=True, text=True, timeout=timeout)
        hata = "" if p.returncode == 0 else (p.stderr or p.stdout or "")[-300:]
    except Exception as e:
        hata = "%r" % e
    yeni = _surum()
    if yeni != eski:
        cli_modeller(yol)     # önbelleği yeni ikiliye göre tazele
    return eski, yeni, hata


def main(argv):
    cmd = argv[0] if argv else "durum"
    if cmd == "coz":
        print(tam_model(argv[1] if len(argv) > 1 else "sonnet"))
    elif cmd == "guncelle":
        eski, yeni, hata = cli_guncelle()
        print("CLI %s → %s%s" % (eski, yeni, (" · hata: " + hata) if hata else ""))
    elif cmd == "uyari":
        print("\n".join(uyari()) or "temiz")
    else:
        print("\n".join(durum_satirlari()))


if __name__ == "__main__":
    main(sys.argv[1:])
