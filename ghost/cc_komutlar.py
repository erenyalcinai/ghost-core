"""Claude Code komutları — Ghost'un sohbet motoru `claude -p` bir Claude Code oturumudur;
skill'ler (/dogrula, /arastir …) ve birkaç yerleşik (/cost, /context, /compact) o kipte
GERÇEKTEN çalışır (2026-09-04'te ölçüldü). Tek şart: slash komutu prompt'un BAŞINDA olmalı.
Köprü mesajın önüne araç notu, duyu satırı ve persona koyuyordu — komut ortada kalıyor,
Claude Code onu düz metin sanıyordu. `prompt_kur` bunu düzeltir: komut öne, notlar arkaya.

Kaynaklar:
- Depo skill'leri `.claude/skills/*/SKILL.md` (ad + description frontmatter'dan, elle liste yok).
- Global skill'ler ve yerleşikler diskte yok (CLI ile gelir): sabit izin listesi `GLOBAL`.
  `/help`, `/status`, `/memory`, `/permissions` bu kipte "isn't available" döner — listede değil.
  `/clear` ve `/model` Ghost'un kendi oturum yönetimiyle çakışır (/new, /model) — listede değil.

Sahip kararı (2026-09-04): köprüdeki elle yazılmış /arastir, /istihbarat, /denetim prompt'ları
kaldırıldı (skill'ler tek kaynak), ölü komutlar (/mail, /bounty, /hunt, /tara, /survivor, /coz)
silindi.
"""
import os
import re

from ghost import config

# (ad, açıklama). Açıklamadaki <…>/[…] uygulamada "argüman ister" demektir (KomutlarView).
GLOBAL = [
    ("code-review", "[low|high] değişen kodu hata/sadeleşme için incele (Claude Code)"),
    ("simplify", "değişen kodu sadeleştir ve uygula (Claude Code)"),
    ("security-review", "dal üzerindeki değişikliklerin güvenlik incelemesi (Claude Code)"),
    ("cost", "bu sohbet oturumunun maliyeti/süresi"),
    ("context", "bağlam doluluğu: hangi parça ne kadar yer tutuyor"),
    ("compact", "oturumu sıkıştır (deneysel — Ghost'un /tazele'si konuyu devrederek yapar)"),
]
# Skill açıklaması uzun; uygulama listesi için argüman ipucu + kısa özet.
ARG_IPUCU = {
    "arastir": "<kişi|şirket|konu|domain>", "denetim": "[repo|site|hepsi]",
    "telefon": "<görev>", "uygulama": "<görev>", "dogrula": "[iddia]",
}
_KISA = {
    "arastir": "OSINT araştırma dosyası (kamuya açık, çapraz doğrulanmış)",
    "denetim": "paralel denetim filosu — güvenlik, sağlamlık, maliyet",
    "dogrula": "iddia etmeden/bitti demeden doğrulama geçişi",
    "istihbarat": "günlük istihbarat brifingi (en fazla 3 madde + 1 fırsat)",
    "telefon": "iPhone'u sür — ekranı gör, dokun, yaz, uygulama aç",
    "uygulama": "AppA/AppB/App C işleri — doğru dizin, yığın, derleme komutu",
}
_AD = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _skill_dizinleri():
    return [os.path.join(config.BASE, ".claude", "skills"),
            os.path.join(os.path.expanduser("~"), ".claude", "skills")]


def skiller():
    """Diskteki skill'ler: [(ad, açıklama)] — SKILL.md frontmatter'ından, ad sırasıyla."""
    out, gorulen = [], set()
    for d in _skill_dizinleri():
        try:
            adlar = sorted(os.listdir(d))
        except OSError:
            continue
        for ad in adlar:
            y = os.path.join(d, ad, "SKILL.md")
            if ad in gorulen or not _AD.match(ad) or not os.path.isfile(y):
                continue
            aciklama = ""
            try:
                with open(y, encoding="utf-8") as f:
                    bas = f.read(4000)
                m = re.search(r"^description:\s*(.+)$", bas, re.M)
                aciklama = (m.group(1).strip().strip('"') if m else "")
            except OSError:
                pass
            gorulen.add(ad)
            out.append((ad, aciklama))
    return out


def liste():
    """Uygulama/Telegram listesi: [{"komut": "/ad", "aciklama": …, "grup": "claude"}]."""
    out = []
    for ad, uzun in skiller():
        kisa = _KISA.get(ad) or (uzun.split(" - ")[0].split(". ")[0][:110] if uzun else "skill")
        ip = ARG_IPUCU.get(ad)
        out.append({"komut": "/" + ad, "aciklama": (ip + " " if ip else "") + kisa, "grup": "claude"})
    for ad, a in GLOBAL:
        out.append({"komut": "/" + ad, "aciklama": a, "grup": "claude"})
    return out


def adlar():
    return {o["komut"] for o in liste()}


def komut_mu(text):
    """Metin bir Claude Code komutuyla mı başlıyor? ('/dogrula', '/arastir X', '/cost')."""
    if not text:
        return False
    ilk = text.strip().split(None, 1)[0] if text.strip() else ""
    return ilk.startswith("/") and ilk in adlar()


def prompt_kur(ham, prompt):
    """Köprünün kurduğu prompt'u (notlar + sarmalanmış mesaj) Claude Code'un tanıyacağı
    biçime çevir: `ham` bir CC komutuysa komut EN BAŞA, kalan her şey arkaya. Değilse dokunma.
    `ham` prompt içinde bir kez geçer (sarmalayıcılar '…yeni mesajı: %s' der); ilk geçiş
    alınır ki komut metni iki kez görünmesin."""
    if not komut_mu(ham):
        return prompt
    kalan = prompt.replace(ham, "", 1).strip()
    if not kalan:
        return ham.strip()
    return "%s\n\n[Ghost bağlam notları — komutun kendisi değil, gerekirse kullan]\n%s" % (ham.strip(), kalan)


def telegram_uygun():
    """setMyCommands için (ad, açıklama): Telegram yalnız [a-z0-9_] kabul eder — '-' içerenler
    (code-review …) menüye giremez, yazılınca yine çalışır."""
    return [(o["komut"][1:], o["aciklama"]) for o in liste()
            if re.match(r"^[a-z0-9_]{1,32}$", o["komut"][1:])]


def yardim():
    satir = " · ".join(o["komut"] for o in liste())
    return ("Claude Code komutları (sohbet oturumunda koşar, skill'ler tek kaynak):\n%s\n"
            "Argüman alanlar: %s" % (satir, ", ".join("/%s %s" % (k, v) for k, v in ARG_IPUCU.items())))
