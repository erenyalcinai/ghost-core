"""Codex — ÜÇÜNCÜ GÖZ (Faz 2.9, Sahip onayı 2026-10-29). Yedek motor DEĞİL.

sahibin çerçevesi: Claude denetim gözü olarak kalır; codex (OpenAI, ChatGPT girişi, kotası daha dar) nadir ve
bütçeli çağrılır — (1) geri alınmaz sınıf işte (korunan dosya yaması, deploy, kapı/vekil değişikliği) ikinci
fikir, (2) Claude'un ikinci gözü "doğrulanamadı" dediğinde hakem, (3) `/codex <görev>` ile sahibin verdiği
salt-okunur görev. Harcamayı özerk iş başlatamaz (özerk kipte çağrılmaz); tavan kodda: günde 3, haftada 10,
sayaç durum katmanında (`durum.oku("codex")`); tavan dolunca SESSİZCE Claude'la devam, bildirim yok.

Güvenlik: codex güvenlik kapısının (PreToolUse kancası Claude'un) DIŞINDA koşar → yalnız `--sandbox read-only`
+ `--ephemeral`, ve asla gerçek çalışma ağacında değil: git'in izlediği/ignore etmediği dosyaların geçici
KOPYASINDA (sır dosyaları gitignore'da → kopyaya girmez; jarvis'te yalnız kod kapsamı — dossier/CLAUDE.md/
kişisel veri rayı girmez). Girdi OpenAI'ye gider: yalnız kod ve doküman.
"""
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import time

from ghost import config, durum, governance

CODEX = shutil.which("codex") or "/usr/bin/codex"
GUNLUK_TAVAN = int(os.environ.get("GHOST_CODEX_GUN", "3"))
HAFTALIK_TAVAN = int(os.environ.get("GHOST_CODEX_HAFTA", "10"))
ZAMAN_ASIMI = int(os.environ.get("GHOST_CODEX_SN", "900"))
DURUM_AD = "codex"
# jarvis'te codex'in görebileceği kapsam (açık çekirdek mantığı: kod + genel doküman; dossier/CLAUDE.md/marka girmez)
JARVIS_KAPSAM = ("ghost", "ops", "tests", "README.md", "ARCHITECTURE.md", "pyproject.toml")
# gitignore'a ek savunma: bu adlar/uzantılar kopyaya hiç girmez
SIR_ADLARI = {".env", "secrets.env", "owner.json", "sirlar.env"}
SIR_UZANTI = (".p8", ".pem", ".key", ".sqlite", ".db", ".jsonl")
KOSULANLAR = []     # test kipi: (kullanım, istem, cwd)


def kurulu():
    return os.path.exists(CODEX)


def _test():
    return os.environ.get("GHOST_TEST") == "1"


def _gun():
    return time.strftime("%Y-%m-%d")


def _hafta():
    y, w, _ = datetime.date.today().isocalendar()
    return "%d-W%02d" % (y, w)


def kota():
    d = durum.oku(DURUM_AD, {}) or {}
    return {"gun_n": d.get("gun_n", 0) if d.get("gun") == _gun() else 0,
            "hafta_n": d.get("hafta_n", 0) if d.get("hafta") == _hafta() else 0,
            "gun_tavan": GUNLUK_TAVAN, "hafta_tavan": HAFTALIK_TAVAN, "son": d.get("son")}


def uygun():
    """(True, "") ya da (False, neden). Neden ne olursa olsun çağıran sessizce Claude'la devam eder."""
    if not kurulu():
        return False, "codex kurulu değil"
    if governance.is_frozen():
        return False, "FREEZE"
    if os.environ.get("GHOST_OZERK") == "1":
        return False, "özerk iş harcama başlatamaz"
    k = kota()
    if k["gun_n"] >= GUNLUK_TAVAN:
        return False, "günlük tavan doldu (%d)" % GUNLUK_TAVAN
    if k["hafta_n"] >= HAFTALIK_TAVAN:
        return False, "haftalık tavan doldu (%d)" % HAFTALIK_TAVAN
    return True, ""


def _sayac(kullanim, ozet, sure):
    def f(d):
        d = dict(d or {})
        if d.get("gun") != _gun():
            d["gun"], d["gun_n"] = _gun(), 0
        if d.get("hafta") != _hafta():
            d["hafta"], d["hafta_n"] = _hafta(), 0
        d["gun_n"] = d.get("gun_n", 0) + 1
        d["hafta_n"] = d.get("hafta_n", 0) + 1
        d["son"] = {"ts": time.strftime("%F %T"), "kullanim": kullanim, "ozet": (ozet or "")[:120], "sure": int(sure)}
        return d
    return durum.guncelle(DURUM_AD, f, {})


def durum_metni():
    k = kota()
    if not kurulu():
        return "codex: kurulu değil"
    son = k["son"] or {}
    s = "codex (üçüncü göz, salt-okunur): bugün %d/%d · hafta %d/%d" % (k["gun_n"], k["gun_tavan"], k["hafta_n"], k["hafta_tavan"])
    if son:
        s += " · son: %s %s (%s sn)" % (son.get("ts", "")[:16], son.get("kullanim", ""), son.get("sure", "?"))
    return s


# ---------------------------------------------------------------- çalışma kopyası
def _repo_dogrula(repo):
    repo = os.path.realpath(os.path.expanduser(repo or config.BASE))
    ev = os.path.realpath(os.path.expanduser("~"))
    if not (repo == ev or repo.startswith(ev + os.sep)) or os.path.basename(repo).startswith(".") \
            or (os.sep + "." in repo[len(ev):]):
        raise ValueError("codex yalnız ev dizinindeki kod depolarına bakar (gizli dizin olmaz): %s" % repo)
    if not os.path.isdir(os.path.join(repo, ".git")):
        raise ValueError("git deposu değil: %s" % repo)
    return repo


def _sir_mi(rel):
    ad = os.path.basename(rel)
    return ad in SIR_ADLARI or ad.endswith(SIR_UZANTI) or ad.startswith(".env")


def calisma_kopyasi(repo=None):
    """Geçici salt-okunur kopya: git'in izlediği + izlenmeyen-ama-ignore-edilmemiş dosyalar (çalışma ağacı hâli;
    sırlar gitignore'da → girmez). jarvis'te yalnız JARVIS_KAPSAM. (dizin, dosya_sayısı) döner."""
    repo = _repo_dogrula(repo)
    jarvis = repo == os.path.realpath(config.BASE)
    yollar = list(JARVIS_KAPSAM) if jarvis else ["."]
    out = subprocess.run(["git", "-C", repo, "ls-files", "-co", "--exclude-standard", "-z", "--"] + yollar,
                         capture_output=True, timeout=60).stdout
    hedef = tempfile.mkdtemp(prefix="ghost-codex-")
    n = 0
    for rel in out.decode("utf-8", "replace").split("\0"):
        if not rel or _sir_mi(rel):
            continue
        kaynak = os.path.join(repo, rel)
        if not os.path.isfile(kaynak):
            continue
        dst = os.path.join(hedef, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(kaynak, dst)
        n += 1
    return hedef, n


def _ortam():
    # secrets.env YOK; codex girişi ~/.codex'te (CODEX_HOME varsayılanı), sır vekiline de dokunmaz
    return {"HOME": os.path.expanduser("~"), "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": os.environ.get("LANG", "C.UTF-8"), "TERM": "dumb", "NO_COLOR": "1"}


def kos(prompt, cwd, kullanim, zaman=None):
    """`codex exec` salt-okunur + ephemeral, cwd = çalışma kopyası. (metin, hata_mı). Her çağrı kotadan düşer
    (başarısız da — OpenAI saydı). Kota/FREEZE/özerk → çağrı yok, ("codex atlandı: …", True)."""
    ok, neden = uygun()
    if not ok:
        return "codex atlandı: %s" % neden, True
    if _test():
        KOSULANLAR.append((kullanim, prompt, cwd))
        return "(test kipi: codex koşmadı)", False
    fd, cikti = tempfile.mkstemp(prefix="codex-son-", suffix=".md")
    os.close(fd)
    cmd = [CODEX, "exec", "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check",
           "-C", cwd, "-o", cikti, "--color", "never", prompt]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=cwd, env=_ortam(), capture_output=True, text=True,
                           timeout=zaman or ZAMAN_ASIMI, stdin=subprocess.DEVNULL)
        rc, err = r.returncode, (r.stderr or "")
    except subprocess.TimeoutExpired:
        rc, err = -1, "zaman aşımı (%d sn)" % (zaman or ZAMAN_ASIMI)
    try:
        with open(cikti, encoding="utf-8", errors="replace") as f:
            metin = f.read().strip()
    except OSError:
        metin = ""
    finally:
        try:
            os.remove(cikti)
        except OSError:
            pass
    sure = time.time() - t0
    if rc != 0 and _giris_hatasi(err):
        # Oturum düşmüş (ChatGPT refresh token): OpenAI saymadı → kotadan düşmez; Sahip `codex login` yenilemeli.
        governance.audit("codex", kullanim, governance.READ, "giriş hatası (kota düşmedi): %s" % prompt[:80])
        return "codex atlandı: oturumu düşmüş — VPS'te `codex login` ile yenilenmeli (Sahip)", True
    _sayac(kullanim, prompt, sure)
    governance.audit("codex", kullanim, governance.READ, "rc=%s %ds: %s" % (rc, int(sure), prompt[:80]))
    if rc != 0 or not metin:
        return ("codex hata (rc=%s): %s" % (rc, (err or metin or "boş çıktı")[-800:])), True
    return metin, False


def _giris_hatasi(err):
    e = (err or "").lower()
    return "could not be refreshed" in e or "401 unauthorized" in e or "please log out and sign in" in e or "not logged in" in e


def _temizle(dizin):
    shutil.rmtree(dizin, ignore_errors=True)


def _kaydet(ad, metin):
    d = os.path.join(config.BASE, "dossier", "codex")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "%s-%s.md" % (time.strftime("%Y-%m-%d-%H%M"), ad))
    with open(p, "w", encoding="utf-8") as f:
        f.write(metin.rstrip() + "\n")
    return p


# ---------------------------------------------------------------- kullanımlar
YAMA_ISTEMI = """Bu depo Ghost'un (kişisel AI altyapısı, Python/stdlib) KOD KOPYASI; INCELEME.patch az sonra
ana dala uygulanacak. Yamayı ve dokunduğu dosyaları oku; UYGULAMA, yalnız incele.
Ara: (1) hata/kırılma (ad hatası, kaldırılan sembolü hâlâ kullanan yer, test eksiği), (2) niyet dışı davranış
değişikliği, (3) güvenlik (sır sızması, kapı atlatma, dışarı yazan yol), (4) dikkat edilecek yan etki.
Türkçe yaz, en çok 25 satır, bulgular önem sırasıyla "dosya:satır — bulgu" biçiminde; bulgu yoksa yazma.
Son satır tam olarak: SONUÇ: uygun | şüpheli | sakıncalı — tek kelime seç, bir cümle gerekçe.
%s"""


def yama_incele(patch_yolu, baglam=""):
    """Korunan dosya yaması için onay kartı AÇILMADAN önce üçüncü göz. (metin, hata_mı, kayıt_yolu|None)."""
    patch_yolu = os.path.abspath(patch_yolu)
    kopya, _n = calisma_kopyasi(config.BASE)
    try:
        shutil.copy2(patch_yolu, os.path.join(kopya, "INCELEME.patch"))
        metin, hata = kos(YAMA_ISTEMI % (("Bağlam: " + baglam) if baglam else ""), kopya, "yama")
    finally:
        _temizle(kopya)
    yol = None if hata else _kaydet("yama-" + os.path.splitext(os.path.basename(patch_yolu))[0], metin)
    return metin, hata, yol


def ucuncu_goz(request, result):
    """verify.review hakemi: Claude 'doğrulanamadı' deyince (ve iş geri alınmaz/harcama sınıfındaysa) aynı
    denetim istemi codex'e gider; cevap verify.parse biçiminde (KARAR/GEREKÇE/KONTROL). (metin, hata_mı)."""
    from ghost import verify
    p = verify.build_prompt(request, result)
    p += ("\n\n[Sen bağımsız üçüncü gözsün (Codex). Depo kopyası salt-okunur; dosyalara bakabilirsin. "
          "Cevabı YALNIZ şu üç satırla ver: KARAR: geçti|eksik|yanlış|doğrulanamadı / GEREKÇE: … / KONTROL: …]")
    kopya, _n = calisma_kopyasi(config.BASE)
    try:
        return kos(p, kopya, "ucuncu_goz")
    finally:
        _temizle(kopya)


def sor(gorev, repo=None):
    """`/codex <görev> [@repo]`: sahibin verdiği salt-okunur görev. (metin, hata_mı, kayıt_yolu|None)."""
    kopya, n = calisma_kopyasi(repo)
    try:
        p = ("%s\n\n[Salt-okunur kod kopyasındasın (%d dosya); dosya yazamazsın, komut ÖNEREBİLİRSİN. "
             "Türkçe, en çok 40 satır, somut dosya/satır referanslı.]" % (gorev, n))
        metin, hata = kos(p, kopya, "sor")
    finally:
        _temizle(kopya)
    return metin, hata, (None if hata else _kaydet("sor", "# /codex: %s\n\n%s" % (gorev, metin)))


def _main(argv):
    if not argv or argv[0] == "durum":
        print(durum_metni())
        return 0
    if argv[0] == "yama" and len(argv) >= 2:
        metin, hata, yol = yama_incele(argv[1], " ".join(argv[2:]))
        print(metin)
        print("\n→ %s" % (yol or ("HATA" if hata else "")))
        return 1 if hata else 0
    if argv[0] == "sor" and len(argv) >= 2:
        repo = None
        if "--repo" in argv:
            i = argv.index("--repo")
            repo = argv[i + 1]
            argv = argv[:i] + argv[i + 2:]
        metin, hata, yol = sor(" ".join(argv[1:]), repo)
        print(metin)
        print("\n→ %s" % (yol or ("HATA" if hata else "")))
        return 1 if hata else 0
    print("kullanım: python3 -m ghost.codex durum | yama <patch> [bağlam] | sor \"<görev>\" [--repo yol]")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
