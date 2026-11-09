"""Ajan organizasyonu — departmanlar (VISION.md: 'ajandan kuruma').

departments.yaml'i minimal parser'la okur (stdlib-only; PyYAML gerektirmez —
sınırlı ama bizim şemamıza yeter). route(text) → en uygun departman.

İKİ KİP (Sahip 2026-11-08 "iki mod olsun, ikisi arasında seçim yapabileyim"; Faz 4 planı
dossier/faz4/departmanlar-v1-plan-2026-11-08.md):
  * `tek`       — bugünkü: tek oturum her şeyi yapar; departman = işe/sohbete eklenen persona ÖNEKİ.
  * `departman` — işe düşen departman GERÇEK ajan: `claude --agents <json> --agent <ad>` (kendi yönergesi,
                  `izin` araç listesi, `model`); sohbet oturumu koordinatör kalır.
Kip durum tablosunda `ajan_mod` (`/mod`, `python3 -m ghost.agents mod`). KÖPRÜ KODU DEĞİŞMEZ: system_prefix
departman kipinde yalnız `[DEPARTMAN:<ad>]` işareti döner; ghost/motor.run_claude işareti iş yolunda
`--agent`e, kalıcı sohbette koordinatör notuna çevirir (işaret metne hiç ulaşmaz).
"""
import json
import os
import re

_DIR = os.path.dirname(os.path.abspath(__file__))
_YAML = os.path.join(_DIR, "departments.yaml")

MODLAR = ("tek", "departman")
MOD_VARSAYILAN = "tek"
MOD_AD = "ajan_mod"                       # durum tablosu kaydı: {"mod": "tek"|"departman"}
_ISARET = "[DEPARTMAN:%s]"
# İşin AÇIK model seçimi işarete biner (Sahip 2026-11-08 "opus iyi; ama işin önem ve zorluğuna göre karar verebilmelisin"):
# `departman ver --model fable` ya da önem yükseltmesi → [DEPARTMAN:x@fable]; motor bunu yaml varsayılanının ÜSTÜNE koyar.
_ISARET_MODEL = "[DEPARTMAN:%s@%s]"
_ISARET_RE = re.compile(r"\[DEPARTMAN:([a-z_]+)(?:@([a-z0-9.-]+))?\]\n?")
_LISTE_ALANLAR = ("anahtar", "araclar", "izin")


def _deger(val):
    """yaml skaler → python: '[a, b]' liste, true/false bool, sayı int, gerisi metin."""
    v = (val or "").strip()
    if v.startswith("[") and v.endswith("]"):
        return [x.strip() for x in v[1:-1].split(",") if x.strip()]
    low = v.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def _parse_departments(path=_YAML):
    """departments.yaml → {anahtar: {ad, anahtar[], persona, araclar[], izin[], model, ajan, ...}}.
    Şemaya özel basit ayrıştırıcı: 2-boşluk girinti, '[..]' listeleri, '>' katlanmış bloklar."""
    deps, cur, field = {}, None, None
    buf = []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return {}

    def flush_persona():
        if cur and buf:
            deps[cur]["persona"] = " ".join(x.strip() for x in buf).strip()

    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and line.rstrip().endswith(":"):
            flush_persona()
            buf = []
            cur = line.strip()[:-1]
            deps[cur] = {"_key": cur, "ad": cur, "anahtar": [], "persona": "", "araclar": [], "izin": [],
                         "model": "inherit", "ajan": True, "aciklama": "", "butce_hafta": 0, "hafiza": ""}
            field = None
            continue
        if cur is None:
            continue
        m = re.match(r"\s+(\w+):\s*(.*)$", line)
        if m and indent == 2:
            flush_persona() if field == "persona" else None
            if field == "persona":
                buf = []
            field = m.group(1)
            val = m.group(2).strip()
            if field in _LISTE_ALANLAR:
                d = _deger(val)
                deps[cur][field] = d if isinstance(d, list) else []
                field = None
            elif val and val != ">":
                deps[cur][field] = _deger(val)
                field = None
            continue
        if field == "persona":
            buf.append(line)
    flush_persona()
    return deps


DEPARTMENTS = _parse_departments()


# ---------- kip ----------

def mod_oku():
    """Geçerli kip: 'tek' (varsayılan) | 'departman'. Durum tablosu okunamazsa 'tek' (bugünkü davranış)."""
    try:
        from ghost import durum
        d = durum.oku(MOD_AD) or {}
        m = d.get("mod") if isinstance(d, dict) else None
        return m if m in MODLAR else MOD_VARSAYILAN
    except Exception:
        return MOD_VARSAYILAN


def mod_yaz(mod):
    """Kipi yaz; geçersizse ValueError. Sonraki işten itibaren geçerli (kalıcı sohbet süreci aynı kalır)."""
    m = (mod or "").strip().lower()
    _takma = {"1": "tek", "bugunku": "tek", "bugünkü": "tek", "2": "departman", "departmanlar": "departman", "ajan": "departman"}
    m = _takma.get(m, m)
    if m not in MODLAR:
        raise ValueError("kip: %s" % " | ".join(MODLAR))
    from ghost import durum
    durum.yaz(MOD_AD, {"mod": m})
    return m


def ajan_mi(key):
    """Departman kipinde gerçek ajan olur mu? (`ajan: false` = yalnız persona, ör. Finans)"""
    d = DEPARTMENTS.get(key)
    return bool(d) and d.get("ajan", True) is not False


def ajanlar():
    """Departman kipinde ajan olan anahtarlar (sırayla)."""
    return [k for k in DEPARTMENTS if ajan_mi(k)]


# ---------- yönlendirme ----------

def _matches(kw, text):
    """Kelime sınırı eşleşmesi — 'bug' artık 'bugün' içinde tutmaz.
    Türkçe harfleri de sınır sayar (\\b latin-only olduğu için elle)."""
    return re.search(r"(?<![0-9a-zçğıöşü])%s(?![0-9a-zçğıöşü])"
                     % re.escape(kw), text) is not None


def route(text):
    """(departman_anahtarı, departman_dict) — anahtar kelime skoruyla en iyi
    eşleşme; hiçbiri tutmazsa None (genel işlem)."""
    t = (text or "").lower()
    best, best_score = None, 0
    for key, dep in DEPARTMENTS.items():
        score = sum(1 for kw in dep["anahtar"] if _matches(kw.lower(), t))
        if score > best_score:
            best, best_score = key, score
    return (best, DEPARTMENTS[best]) if best else (None, None)


def _persona_onek(dep):
    return ("[Ghost %s departmanı olarak çalış. %s Öncelikli araçların: %s]\n\n"
            % (dep["ad"], dep["persona"], ", ".join(dep["araclar"])))


def system_prefix(text, dep=None):
    """SOHBET yoluna iliştirilecek departman yönergesi (yoksa boş). dep önceden
    route() ile hesaplandıysa geçilir → çift route çağrısı önlenir.

    tek kipi: persona öneki (araçlar KISIT değil, ÖNCELİK — MCP'nin tamamı zaten yüklü).
    departman kipi: ajan olan departmanda yalnız `[DEPARTMAN:<ad>]` işareti (motor koordinatör notuna çevirir);
    ajan olmayan (Finans) yine persona öneki.
    İŞ yolu bunu artık KULLANMAZ (4.1.6, 2026-11-09): köprü route() anahtarını `motor.run_claude(dep=)` ile geçirir,
    persona/ajan kararı orada (`ajan_anahtar`, `persona_sistem`)."""
    if dep is None:
        _key, dep = route(text)
    if not dep:
        return ""
    key = dep.get("_key")
    if mod_oku() == "departman" and key and ajan_mi(key):
        return _ISARET % key + "\n"
    return _persona_onek(dep)


def ajan_anahtar(key):
    """İş yolunda GERÇEK AJAN koşacak departman anahtarı (4.1.6): departman kipi + ajan tanımı varsa anahtar,
    yoksa None (tek kip, ajan olmayan Finans, bilinmeyen/boş anahtar)."""
    return key if (key and mod_oku() == "departman" and ajan_mi(key)) else None


def persona_sistem(key):
    """Ajan koşmayan işte (tek kip ya da ajan olmayan departman) Claude'un SİSTEM istemine eklenen persona
    (`--append-system-prompt`); departman yoksa None. Metin `_persona_onek` ile aynı — eskiden köprü aynı satırı
    istemin başına yazardı (system_prefix), 4.1.6 ile motor sistem istemine koyar."""
    d = DEPARTMENTS.get(key) if key else None
    return _persona_onek(d).strip() if d else None


# ---------- departman kipi: işaret ↔ ajan ----------

def isaret_coz_tam(prompt):
    """İstemdeki [DEPARTMAN:x] / [DEPARTMAN:x@model] işaretlerini SİL, (anahtar, temiz_istem, model|None) döndür.
    Birden çok işaret varsa SONUNCUSU kazanır (köprünün yönlendirmesi önce, `departman ver`in
    açık seçimi metnin içinde sonra gelir → açık seçim üstün). Kip 'tek'e dönmüşse işaret
    yine silinir ama anahtar/model None (iş bugünkü gibi koşar). model = işaretteki açık seçim
    (takma ad ya da tam kimlik); yoksa None → motor yaml varsayılanına (ajan_model) bakar."""
    bul = list(_ISARET_RE.finditer(prompt or ""))
    temiz = _ISARET_RE.sub("", prompt or "") if bul else prompt
    key, model = None, None
    for m in bul:
        if ajan_mi(m.group(1)):
            key, model = m.group(1), (m.group(2) or None)
    if key and mod_oku() != "departman":
        key, model = None, None
    return key, temiz, model


def isaret_coz(prompt):
    """isaret_coz_tam'ın modelsiz hali: (anahtar, temiz_istem). Eski çağıranlar için."""
    key, temiz, _model = isaret_coz_tam(prompt)
    return key, temiz


_AJAN_EK = (" Ghost'un %(ad)s departmanı olarak AYRI bir ajansın (Faz 4, departman kipi): bu iş sana verildi, "
            "koordinatör (sohbet oturumu) sonucunu bekliyor. Türkçe çalış; proje kuralları (CLAUDE.md) aynen geçerli; "
            "geri alınmaz iş (deploy, silme, push, para, mail) için onay kartı aç ya da ÖNERİLEN EYLEM bloğu yaz, kendin yapma. "
            "Kendi çalışma notlarını dossier/departman/%(key)s/ altına yaz (varsa güncelle). Çıktının İLK PARAGRAFI sahibe gider "
            "(ürün sahibi, teknik inceleme yapmaz, telefonda okur): 2-4 düz cümle — ne yaptın, sonuç, ondan ne bekleniyor; "
            "hedef/Masa/iş numarası, dosya yolu, commit, madde işareti, kalın yazı YOK. Ayrıntı ondan sonra (tamamı Masa'ya iner). "
            "Doğrulamadığın şeyi 'bitti' deme.")


def departman_dizin(key, kok=None):
    """Departmanın çalışma notları: dossier/departman/<key>/ (4.1.4 departman hafızası — ajan kendi alanında derinleşir)."""
    if kok is None:
        from ghost import config
        kok = config.BASE
    return os.path.join(kok, "dossier", "departman", key)


def hafiza_ozeti(key, en_fazla=8, kok=None):
    """Departmanın son notları (ad + ilk başlık), istem için kısa blok; not yoksa ''. Okuma hatası sessiz."""
    dizin = departman_dizin(key, kok)
    try:
        dosyalar = [f for f in os.listdir(dizin) if f.endswith(".md")]
    except OSError:
        return ""
    dosyalar.sort(key=lambda f: os.path.getmtime(os.path.join(dizin, f)), reverse=True)
    satirlar = []
    for f in dosyalar[:en_fazla]:
        baslik = ""
        try:
            with open(os.path.join(dizin, f), encoding="utf-8") as fh:
                for satir in fh:
                    if satir.strip():
                        baslik = satir.strip().lstrip("#").strip()[:90]
                        break
        except OSError:
            pass
        satirlar.append("- %s%s" % (f, (" — " + baslik) if baslik else ""))
    if not satirlar:
        return ""
    return (" Departman notların (%s, en yeni önce; önce bunlara bak, aynı konuda yeni dosya açma, güncelle):\n%s"
            % (os.path.relpath(dizin, kok or departman_dizin(key).rsplit("/dossier/", 1)[0]), "\n".join(satirlar)))


def ajan_tanim(key, kok=None, model=None):
    """`claude --agents` JSON'undaki tek ajan tanımı: description + prompt (+ departman hafızası) + (tools) + (model).
    model: işin açık seçimi (takma ad ya da tam kimlik) — yaml varsayılanını EZER; None → yaml (`inherit` ise alan yok).
    Ajan tanımındaki model CLI `--model`den üstün olduğu için koşan model buraya da yazılır (motor, model_sinavi)."""
    d = DEPARTMENTS[key]
    tanim = {"description": d.get("aciklama") or d["ad"],
             "prompt": (d["persona"] + _AJAN_EK % {"ad": d["ad"], "key": key} + hafiza_ozeti(key, kok=kok)).strip()}
    izin = d.get("izin") or []
    if izin:
        tanim["tools"] = list(izin)
    secim = model or d.get("model") or "inherit"
    if isinstance(secim, str) and secim.lower() not in ("inherit", ""):
        from ghost import modeller
        tanim["model"] = modeller.tam_model(secim)
    return tanim


def ajan_json(key=None, model=None):
    """--agents için JSON: tek departman (key) ya da ajan olan hepsi. model yalnız `key`e uygulanır.
    Tek seviye kuralı: iş koşan ajana YALNIZ kendi tanımı verilir (departman departman açamaz)."""
    keys = [key] if key else ajanlar()
    return json.dumps({k: ajan_tanim(k, model=(model if k == key else None)) for k in keys if ajan_mi(k)},
                      ensure_ascii=False)


def ajan_args(key, model=None):
    """motor.run_claude'un komut satırına ekleyeceği bayraklar (model = işin açık seçimi, yoksa yaml)."""
    return ["--agents", ajan_json(key, model=model), "--agent", key]


def sohbet_args():
    """Kalıcı sohbet sürecine (koordinatör) TÜM departman tanımları (4.1.3): Agent aracında
    subagent_type=muhendislik|istihbarat|operasyon kısa alt ajan. Kip ne olursa olsun tanım yüklü — kullanım
    koordinatör notuna bağlı; böylece /mod değişince süreç yeniden açılmaz. Tanım yoksa/hata → []."""
    try:
        j = ajan_json()
        return ["--agents", j] if j and j != "{}" else []
    except Exception:
        return []


def ajan_model(key):
    """Departmanın yaml'daki modeli (takma ad) ya da None (inherit = çağıranın modeli)."""
    m = (DEPARTMENTS.get(key) or {}).get("model") or "inherit"
    return None if (not isinstance(m, str) or m.lower() in ("inherit", "")) else m


def koordinator_notu(key):
    """Kalıcı sohbette işaretin yerine giren not: koordinatör kısa işi kendi yapar, derin işi departmana verir."""
    d = DEPARTMENTS.get(key) or {"ad": key}
    return ("[Koordinatör notu — departman kipi: bu konu %s departmanının alanı. Kısa iş (≤3 araç çağrısı) sende. "
            "Derin, uzun ya da paralel iş → `python3 -m ghost.departman ver %s \"<görev>\"` (ayrı ajan koşar, sonuç iş "
            "bitince sohbete düşer).]\n\n" % (d["ad"], key))


# ---------- CLI ----------

def _main(argv):
    cmd = argv[0] if argv else "liste"
    if cmd == "liste":
        print("kip: %s" % mod_oku())
        for k, d in DEPARTMENTS.items():
            print("• %s (%s) — ajan: %s · model: %s · izin: %s · anahtar: %d" % (
                d["ad"], k, "evet" if ajan_mi(k) else "hayır (persona)", d.get("model") or "inherit",
                ", ".join(d.get("izin") or []) or "hepsi (miras)", len(d["anahtar"])))
        return 0
    if cmd == "mod":
        if len(argv) > 1:
            print("kip: %s" % mod_yaz(argv[1]))
        else:
            print("kip: %s  (seç: %s)" % (mod_oku(), " | ".join(MODLAR)))
        return 0
    if cmd == "json":
        print(ajan_json(argv[1] if len(argv) > 1 else None))
        return 0
    if cmd == "dene":
        metin = " ".join(argv[1:])
        key, dep = route(metin)
        print("yönlendirme: %s · kip: %s" % (key or "— (genel)", mod_oku()))
        if key:
            onek = system_prefix(metin, dep=dep)
            print("önek: %r" % onek[:120])
            k2, _ = isaret_coz(onek + metin)
            print("iş yolu: %s" % ("--agent %s (model %s)" % (k2, ajan_model(k2) or "miras") if k2 else "persona öneki, bugünkü gibi"))
        return 0
    print("kullanım: python3 -m ghost.agents liste | mod [tek|departman] | json [ad] | dene \"<metin>\"")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
