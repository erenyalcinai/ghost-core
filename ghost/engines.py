"""Motor çoğulluğu — Ghost tek beyne bağlı değildir.

Kayıtlı motorlar otomatik algılanır (binary varsa aktif). Birincil motor
hata verirse sıradaki denenır (failover). Yeni bir CLI kurulduğunda
(codex/gemini/aider/ollama) kendiliğinden kullanılabilir olur.

Sınıflar: 'primary' (tam yetenek, MCP'li işler) · 'local' (mahrem/ucuz/
çevrimdışı — Ollama) · failover sırası AVAILABLE() sırasıdır.
"""
import os
import re
import shutil

from ghost import sandbox

# (ad, binary, komut şablonu, not)
CATALOG = [
    ("claude", "claude",
     ["claude", "-p", "{prompt}", "--output-format", "text", "--model", "{model}"],
     "birincil akıl (Anthropic)"),
    ("ollama", "ollama",
     ["ollama", "run", "{model}", "{prompt}"],
     "yerel model (mahrem/çevrimdışı yedek)"),
    ("codex", "codex", ["codex", "exec", "{prompt}"], "OpenAI kod ajanı"),
    ("gemini", "gemini", ["gemini", "-p", "{prompt}"], "Google kod ajanı"),
]

DEFAULT_MODELS = {"claude": "sonnet", "ollama": "llama3.2:3b"}

# Maliyet/kota kaynaklı hata YEDEK MOTORU TETİKLEMEZ (sahibin kararı,
# 2026-08-24). Gerekçe: "usage limit reached" bir ARIZA değil, bir SINIR.
# O anda sessizce codex'e düşmek, sahibin haberi olmadan daha zayıf ve
# entegrasyonsuz (MCP'siz) bir motorun cevabını Claude'un yerine koyuyor —
# üstelik "Claude erişilemedi" diye yanlış teşhis yazıyordu. Sınır görünür
# olmalı, kararı Sahip versin. GERÇEK arıza (ağ kopması, CLI çökmesi, zaman
# aşımı, auth bozulması) failover'ı hâlâ tetikler.
COST_LIMIT = re.compile(
    r"usage limit|rate[ _-]?limit|too many requests|\b429\b|\b402\b|"
    r"quota|credit balance|insufficient (credit|balance|funds|quota)|"
    r"out of credits?|payment required|billing|upgrade to (a )?paid|"
    r"limit reached|kullan[ıi]m s[ıi]n[ıi]r[ıi]|kota",
    re.IGNORECASE,
)


# Zaman aşımı da yedek motoru TETİKLEMEZ (sahibin kararı, 2026-08-28).
# Gerekçe: uzun süren iş bir ARIZA değil, süresi dolmuş bir çalışmadır. Köprü
# eskiden zaman aşımını "oturum kurulamadı" sanıp turu SIFIRDAN bir kez daha
# koşuyordu (600+600 = 20 dk sessizlik), sonra bağlamsız ve MCP'siz codex'e
# düşüp onun cevabını Ghost'un cevabı gibi gönderiyordu. İş yolunda daha da
# kötüsü oluyordu: codex'in cevabı err=False ile "bitti" sayılıp kalıcı belleğe
# iniyordu (hafıza kaydı #154 / iş #36, 20 Ağustos 2026 — "deploy yap → hangi
# projeyi deploy edeyim?"). Zaman aşımı görünür olmalı, kararı Sahip versin.
TIMEOUT_MARK = "⏱ Zaman aşımı"

TIMEOUT = re.compile(
    r"⏱ Zaman a[şs][ıi]m[ıi]|zaman a[şs][ıi]m[ıi]|\btimed[ _-]?out\b|"
    r"\btimeout\b|TimeoutExpired",
    re.IGNORECASE,
)


def is_timeout(text):
    """Hata metni zaman aşımını mı anlatıyor (erişim arızasını değil)?

    True → yedek motora DÜŞME ve turu sıfırdan TEKRARLAMA; süreyi sahibe göster.
    """
    return bool(text and TIMEOUT.search(str(text)))


def is_cost_limit(text):
    """Hata metni maliyet/kota sınırını mı anlatıyor (erişim arızasını değil)?

    True → yedek motora DÜŞME, sınırı olduğu gibi sahibe göster.
    """
    return bool(text and COST_LIMIT.search(str(text)))


def available():
    """Kurulu motorlar, failover sırasıyla."""
    return [(name, cmd, note) for name, binary, cmd, note in CATALOG
            if shutil.which(binary)]


def run(prompt, prefer=None, model=None, timeout=600):
    """(cevap, motor_adı, hata_mı). prefer verilirse o başa alınır;
    hata olursa sıradaki kurulu motora düşer."""
    engines = available()
    if prefer:
        engines.sort(key=lambda e: 0 if e[0] == prefer else 1)
    last_err = "kurulu motor yok"
    for name, cmd_tpl, _note in engines:
        m = model or DEFAULT_MODELS.get(name, "")
        if name == "claude":                 # takma ad → en güncel tam kimlik
            from ghost import modeller
            m = modeller.tam_model(m)
        cmd = [c.replace("{prompt}", prompt).replace("{model}", m)
               for c in cmd_tpl]
        cmd = [c for c in cmd if c != ""]
        # Motorlar güvenilir ikili (LLM-üretimi kod değil) ve claude auth
        # env'ine muhtaç → sandbox'ın temiz-env varsayılanını bilerek aşıyoruz.
        rc, out, err, _secs = sandbox.run(cmd, timeout=timeout,
                                          env=dict(os.environ))
        if rc == 0 and out.strip():
            return out.strip(), name, False
        last_err = (err or out or "rc=%d" % rc)[:300]
    return "⚠️ Tüm motorlar başarısız: %s" % last_err, None, True
