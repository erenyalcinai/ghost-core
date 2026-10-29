"""İkinci göz — kör denetçi.

NEDEN VAR (2026-08-27): Ghost'un elinde denetim aletleri vardı (/denetim,
/dogrula, /code-review, pre-push) ama biri hariç hepsi İSTEĞE BAĞLIYDI —
onları ne zaman çağıracağına Ghost karar veriyordu. Denetlenenle denetime
karar veren aynı olunca denetim yoktur: işi yanlış yaptığını düşünmeyen bir
ajan denetim çağırmaz, zaten yanlış olduğunu düşünse yapmazdı.

Üç özellik şart, üçü de burada:
  1. TETİKLEYİCİSİ AJAN DEĞİL — kapsamdaki her iş bitişinde kendiliğinden
     koşar (bkz. needs_review). `dersler.py` ve pre-push kapısı neden işliyor?
     Çünkü unutulunca da koşuyorlar.
  2. KÖR — denetçi ajanın gerekçesini GÖRMEZ. Yalnız sahibin emrini ve ortaya
     çıkan raporu görür. Gerekçeyi okuyan göz ikna olur; ikna olan göz göz
     değildir.
  3. VETO — "eksik"/"yanlış" kararı işin ✅ ile raporlanmasını engeller
     (bkz. blocks). Rapora eklenen dipnot süs olurdu.

Kararlar `ops/verify.jsonl`'e düşer; haftalık sentez bu günlüğü okuyup
"hangi tür iddiada sistematik yakalanıyorum" desenini derse çevirir. Asıl
bileşik değer tek tek denetimlerde değil, o desende.
"""
import json
import os
import re
import time

from ghost import config, governance

MODEL = os.environ.get("GHOST_VERIFY_MODEL", "sonnet")
TIMEOUT = int(os.environ.get("GHOST_VERIFY_TIMEOUT", "240"))

GECTI = "geçti"
EKSIK = "eksik"
YANLIS = "yanlış"
BELIRSIZ = "doğrulanamadı"
KARARLAR = (GECTI, EKSIK, YANLIS, BELIRSIZ)

# Denetçinin karara varamadığı hâl işi ENGELLEMEZ (yoksa denetçi her düştüğünde
# Ghost hiçbir işi bitiremezdi), ama günlüğe düşer ve mesajda görünür.
ENGELLEYEN = (EKSIK, YANLIS)

_MIN_UZUNLUK = 40
# Denetçi aksansız yazarsa (gecti/yanlis/dogrulanamadi) aynı karar sayılır.
_ASCII = {"gecti": GECTI, "yanlis": YANLIS, "dogrulanamadi": BELIRSIZ}


def _norm(s):
    """Türkçe-güvenli küçültme. Python'da 'I'.lower() == 'i' (Türkçe'de 'ı')
    ve 'İ'.lower() birleşik aksan üretir; ham re.I bu yüzden 'ÇALIŞIYOR' gibi
    büyük harfli iddiaları kaçırırdı."""
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


# "Bitti/oldu" iddiası taşıyan gövdeler. Ek almış hâlleri de yakalasın diye
# kök bırakıldı (kurul-du/-muş, çalış-ıyor, yayınla-ndı ...).
_IDDIA = re.compile(
    r"(kuruld|kurdum|çalışıyor|çalışır durumda|düzelt|yayında|yayınlan"
    r"|deploy|canlı|aktifleş|aktif hâle|eklendi|ekledim|oluşturuld|oluşturdum"
    r"|güncellend|güncelledim|tamamland|hazır|silindi|sildim|push"
    r"|commit|başarıyla|doğruland|test(?:ler)? geç|sorunsuz)"
)

# Denetimden muaf iş türleri: hiçbir şeye dokunmayan, iddia değil metin üreten
# işler. Kapsam dar tutuluyor — her işi denetlemek maliyeti boğar.
MUAF_TURLER = ("sohbet", "brifing", "istihbarat", "özet")


def kapali():
    """Acil kaçış: dosya ya da env ile kapatılabilir (bilerek gürültülü)."""
    if os.environ.get("GHOST_VERIFY", "1") == "0":
        return True
    return os.path.exists(os.path.join(config.OPS, "verify_disabled"))


def needs_review(prompt, result, kind="", err=False):
    """Bu iş ikinci gözden geçmeli mi? Kural AJANIN KANAATİNE bırakılmaz.

    Kapsam: durum değiştiren (governance READ üstü) YA DA sonucunda
    'oldu/bitti' iddiası taşıyan işler. Hata veren, iptal edilen, çok kısa
    ya da muaf türdeki işler denetlenmez."""
    if kapali() or err:
        return False
    if not result or len(result.strip()) < _MIN_UZUNLUK:
        return False
    if _norm(kind) in [_norm(k) for k in MUAF_TURLER]:
        return False
    if governance.classify(prompt or "") != governance.READ:
        return True
    return bool(_IDDIA.search(_norm(result)))


def build_prompt(request, result):
    """Kör denetçi istemi. DİKKAT: buraya ajanın düşüncesi/gerekçesi/oturum
    geçmişi KOYULMAZ — yalnız emir ve ham rapor. Körlük tasarımın kendisi."""
    return (
        "İKİNCİ GÖZ — KÖR DENETİM.\n\n"
        "Sen bağımsız bir denetçisin. Bir ajan aşağıdaki emri aldı ve işi "
        "bitirdiğini söylüyor. Senin işin onaylamak DEĞİL, çürütmeye "
        "çalışmaktır.\n\n"
        "VARSAYIMIN: iş bitmemiştir. Aksi KANITLANANA kadar bu geçerli.\n\n"
        "Ajanın gerekçesi sana bilerek verilmedi. Yalnız emri ve ortaya çıkan "
        "raporu görüyorsun. Rapora İNANMA — rapor bir iddiadır, kanıt değil. "
        "Her somut iddiayı üreten yerden değil, BAĞIMSIZ yerden doğrula: "
        "dosyayı oku, komutu kendin koştur, URL'yi çek, süreci sor.\n\n"
        "--- EMİR (sahibin istediği) ---\n%s\n"
        "--- RAPOR (ajanın çıktısı — DOĞRULANMAMIŞ İDDİA) ---\n%s\n"
        "--- SON ---\n\n"
        "Sırayla cevapla:\n"
        "1. İSTENEN BU MUYDU? Emrin HER parçası karşılandı mı? Yarısı "
        "yapıldıysa hangi yarısı eksik?\n"
        "2. GERÇEKTEN OLDU MU? Raporun somut iddialarını bağımsız kontrol et.\n"
        "3. KANIT BAĞIMSIZ MI? 'Komut 0 döndürdü' iş oldu demek değildir.\n\n"
        "KURAL: sen yalnız DOĞRULARSIN. Hiçbir şeyi değiştirme, silme, deploy "
        "etme, push etme, dışarı gönderme. Salt okuma ve salt kontrol.\n"
        "Doğrulayamadığın yeri uydurma — 'doğrulayamadım' de.\n\n"
        "ÇIKTI — tam olarak şu üç satır, başka hiçbir şey yazma:\n"
        "KARAR: geçti | eksik | yanlış | doğrulanamadı\n"
        "GEREKÇE: en fazla iki cümle. 'eksik'/'yanlış' ise NE eksik, NEREDE "
        "yanlış — somut.\n"
        "KONTROL: neye bakarak karar verdin (dosya/komut/URL) — tek satır.\n"
        % ((request or "").strip()[:4000], (result or "").strip()[:6000])
    )


def parse(out):
    """Denetçi çıktısını (karar, gerekçe, kontrol) üçlüsüne çevir.
    Biçim tutmazsa BELİRSİZ — sessizce 'geçti'ye düşmek denetimi yok ederdi."""
    txt = out or ""
    karar = BELIRSIZ
    m = re.search(r"KARAR\s*:\s*(.+)", txt, re.I)
    if m:
        ham = _norm(m.group(1).split("\n")[0])
        # 2026-10-28 (Codex S7): alt dize eşleşmesi "KARAR: geçti değil, yanlış"ı GEÇTİ sayıyordu (listede ilk
        # gelen kazanıyordu). Kural: satırın İLK sözcüğü karar olmalı, satırda başka karar sözcüğü ya da
        # olumsuzlama ("değil") olmamalı; noktalama/kalın yazı serbest, kalan açıklama serbest. Gerisi BELİRSİZ.
        sozcukler = [_ASCII.get(t, t) for t in re.findall(r"\w+", ham)]
        bulunan = [k for k in KARARLAR if _norm(k) in sozcukler]
        if (sozcukler and len(bulunan) == 1 and sozcukler[0] == _norm(bulunan[0])
                and not {"değil", "degil"} & set(sozcukler)):
            karar = bulunan[0]
    def _al(alan):
        mm = re.search(alan + r"\s*:\s*(.+?)(?=\n[A-ZÇĞİÖŞÜ]{4,}\s*:|\Z)",
                       txt, re.I | re.S)
        return " ".join(mm.group(1).split())[:400] if mm else ""
    return karar, _al("GEREKÇE"), _al("KONTROL")


def blocks(karar, metin=None):
    """İşin temiz '✅ bitti' olarak raporlanmasını engelliyor mu?
    'doğrulanamadı' da engeller — iş geri alınmaz (PERMANENT) ya da harcama (SPEND) sınıfındaysa
    (2026-10-27, dış inceleme: kritik işte doğrulanamayan sonuç başarı sayılmamalı)."""
    if karar in ENGELLEYEN:
        return True
    if karar == BELIRSIZ and metin:
        from ghost import governance
        return governance.classify(metin) in (governance.PERMANENT, governance.SPEND)
    return False


def log(rec):
    """Karar günlüğü — haftalık sentezin desen çıkaracağı kaynak."""
    rec = dict(rec)
    rec.setdefault("ts", time.strftime("%F %T"))
    try:
        with open(config.VERIFY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return rec


def note(karar, gerekce="", kontrol=""):
    """Telefonda okunacak tek paragraf."""
    if karar == GECTI:
        s = "🔎 İkinci göz: geçti."
        return s + (" (%s)" % kontrol if kontrol else "")
    if karar == BELIRSIZ:
        return "🔎 İkinci göz: DOĞRULANAMADI — %s" % (gerekce or "denetçi karara varamadı")
    return "🔎 İkinci göz: %s — %s" % (karar.upper(), gerekce or "gerekçe verilmedi")


def review(request, result, runner, kind="", jid=None, model=None, timeout=None, ozerk=False):
    """Kör denetimi koştur, günlüğe yaz, (karar, not) döndür.

    runner: (prompt, model, ...) -> (metin, sid, hata) imzalı çağrılabilir —
    köprünün run_claude'u geçilir. Modül köprüyü import ETMEZ (döngüsel olurdu
    ve test edilemezdi)."""
    t0 = time.time()
    p = build_prompt(request, result)
    try:
        # ozerk: denetçi de gözetimsiz işin parçası — kapı geri alınmaz eylemi onda da kessin
        # (2. tur bulgu 1: denetçi tam yetkiyle koşup özerk sınırını deliyordu). Denetçi salt-okuma
        # yapar, yetenek kaybı yok.
        out, _, err = runner(p, model or MODEL, use_mcp=[],
                             timeout=timeout or TIMEOUT, ozerk=ozerk)
    except Exception as e:                      # denetçi asla işi düşürmesin
        out, err = "denetçi çalıştırılamadı: %r" % e, True
    if err:
        karar, gerekce, kontrol = BELIRSIZ, (out or "")[:200], ""
    else:
        karar, gerekce, kontrol = parse(out)
    codex_k = None
    # Faz 2.9 (Sahip 2026-10-29): Claude karara varamadıysa ve iş geri alınmaz/harcama sınıfındaysa codex HAKEM
    # (üçüncü göz, salt-okunur kopya, günde ≤3). Yalnız SIKILAŞTIRIR: codex 'eksik/yanlış' derse karar o olur;
    # 'geçti' derse Claude'un 'doğrulanamadı'sı kalır, not düşer. Özerk işte codex çağrılmaz (harcama yasağı).
    if karar == BELIRSIZ and not ozerk and blocks(karar, request):
        try:
            from ghost import codex
            if codex.uygun()[0]:
                out2, err2 = codex.ucuncu_goz(request, result)
                if not err2:
                    k2, g2, _c2 = parse(out2)
                    codex_k = k2
                    if k2 in ENGELLEYEN:
                        karar, gerekce = k2, "codex (üçüncü göz): " + (g2 or "")
                    else:
                        gerekce = "%s · codex: %s — %s" % (gerekce or "Claude karara varamadı", k2, (g2 or "")[:100])
        except Exception:
            pass                                  # hakem asla işi düşürmesin
    rec = log({"jid": jid, "kind": kind, "karar": karar, "gerekce": gerekce,
               "kontrol": kontrol, "model": model or MODEL, "codex": codex_k,
               "sure": int(time.time() - t0),
               "emir": (request or "")[:200]})
    governance.audit("verify", "ikinci_goz", governance.READ,
                     "#%s %s: %s" % (jid, karar, gerekce[:120]))
    return karar, note(karar, gerekce, kontrol), rec
