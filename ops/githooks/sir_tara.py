#!/usr/bin/env python3
"""Ghost sır tarayıcısı — commit'e de, PUSH edilen aralığa da aynı göz (2026-10-28, Faz 1 madde 14).

NEDEN VAR: sır kapısı yalnız pre-commit'teydi. `core.hookspath` (küçük harf) ile başka kanca dizini,
plumbing (`git commit-tree`, `update-ref`) ya da Mac'ten gelen bir commit o kapıya hiç uğramadan
`main`'e girebiliyor, knowsync da 15 dk içinde GitHub'a itiyordu (bağımsız denetim Y9). Artık aynı
tarayıcı üç yerde koşar: pre-commit (sahnelenen), pre-push (uzak..yerel aralığındaki HER commit),
knowsync (her `git push` öncesi).

Bakılan:
  * sır dosyasının kendisi (secrets.env, .env, *.p8, ssh anahtarı, .git-credentials, .netrc);
  * secrets.env / .env / mcp*.json'daki GERÇEK değerlerin düz geçişi (≥12 karakter);
  * genel token biçimleri (GitHub, Telegram botu, Anthropic/OpenAI, AWS, Slack, Cloudflare, özel anahtar).
Değer ASLA basılmaz: yalnız dosya adı, commit kısaltması ve anahtarın/desenin ADI.

Yanlış pozitif kaçışı: aynı satırda `sir-tara: yoksay` işareti (test dosyalarındaki sahte örnekler için).
Yalnız GENEL desenleri susturur — gerçek değerin düz eşleşmesi ve sır dosyası işaretle susmaz.

Kullanım:
  ops/githooks/sir_tara.py --cached                 sahnelenen değişiklik (pre-commit)
  ops/githooks/sir_tara.py --aralik A..B            aralıktaki her commit'in eklediği satırlar
  ops/githooks/sir_tara.py --tip SHA --uzak NAME    yeni dal: uzakta olmayan commit'ler (tavanlı)
Çıkış: 0 temiz · 1 bulgu · 2 tarama yapılamadı (çağıran güvenli tarafa düşer: durdurur).
"""
import glob
import json
import os
import re
import subprocess
import sys

GIZLI_DEGIL = {"NTFY_TOPIC", "CLOUDFLARE_ACCOUNT_ID", "POSTHOG_HOST", "POSTHOG_PROJECT_ID", "ASC_ISSUER_ID",
               "ASC_KEY_ID", "ASC_KEY_PATH", "APNS_KEY_ID", "APNS_TEAM_ID", "APNS_KEY_PATH", "APNS_TOPIC",
               "ELEVENLABS_VOICE_ID", "GMAIL_ADDRESS", "WIKIDATA_USER", "APPLE_ID", "HF_API_KEY_ID",
               "JARVIS_CLAUDE_ARGS", "CLAUDE_BIN", "TELEGRAM_CHAT_ID"}
SIR_DOSYA = re.compile(r"(^|/)(secrets\.env|\.env|[^/]*\.p8|id_[a-z0-9]+|ghost_vps|ghost_runtime|"
                       r"\.git-credentials|\.netrc)$")
YOKSAY = "sir-tara: yoksay"
TAVAN = 500           # yeni dalda en çok bu kadar commit taranır (tüm geçmiş değil)

# (ad, desen) — biçim tek başına sır demek; isim/bağlam gerektirenler bağlamla yazıldı.
DESENLER = [
    ("özel anahtar bloğu", re.compile(r"-----BEGIN ([A-Z0-9]+ )*PRIVATE KEY-----")),
    ("GitHub ince ayarlı PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("Telegram bot token", re.compile(r"(?<![\w:])\d{8,10}:[A-Za-z0-9_-]{35}(?![\w-])")),
    ("Anthropic anahtarı", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI/sk anahtarı", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}")),
    ("AWS erişim anahtarı", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[bpas]-[A-Za-z0-9-]{10,}")),
    ("Cloudflare API token", re.compile(
        r"(?i)(?:cloudflare|\bcf)[_-]?(?:api[_-]?)?token['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9_-]{40}(?![\w-])")),
]


def kok():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.stdout.strip() or os.getcwd()


def degerler(k):
    """Gerçek sır değerleri → anahtar adı. Yalnız bellekte; hiçbir yere yazılmaz/basılmaz."""
    out = {}
    for ad in ("secrets.env", ".env"):
        try:
            with open(os.path.join(k, ad), encoding="utf-8") as f:
                for satir in f:
                    m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", satir.rstrip("\n"))
                    if not m or m.group(1) in GIZLI_DEGIL:
                        continue
                    v = m.group(2).strip().strip("'\"")
                    if len(v) >= 12 and not v.startswith(("/", "~", "http", "-")):
                        out[v] = m.group(1)
        except OSError:
            pass

    def _yuru(o, anahtar=""):
        if isinstance(o, dict):
            for a, b in o.items():
                yield from _yuru(b, a)
        elif isinstance(o, list):
            for b in o:
                yield from _yuru(b, anahtar)
        elif isinstance(o, str) and re.search(r"(?i)auth|token|key|secret|pass", anahtar):
            yield re.sub(r"^(Bearer|token|Basic)\s+", "", o.strip())
    for yol in glob.glob(os.path.join(k, "mcp*.json")):      # MCP uç başlıkları (GitHub PAT'leri)
        if yol.endswith(".example.json"):
            continue
        try:
            with open(yol, encoding="utf-8") as f:
                for v in _yuru(json.load(f)):
                    if len(v) >= 12 and not v.startswith(("/", "~", "http", "-", "$")):
                        out[v] = "MCP başlığı"
        except (OSError, ValueError):
            pass
    return out


def _yer_tutucu(s):
    """xxxx…, aaaa…, 0000… gibi sahte örnekler: çok az farklı karakter."""
    govde = re.sub(r"^[a-z_]+[-_]", "", s, flags=re.I)
    return len(set(govde)) < 8


def satir_tara(satir, vs):
    """Bir eklenen satırdaki bulgu adları (değersiz)."""
    bulgu = []
    for v, ad in vs.items():
        if v in satir:
            bulgu.append(f"{ad} değeri")
    if YOKSAY in satir:
        return bulgu
    for ad, d in DESENLER:
        for m in d.finditer(satir):
            if ad == "özel anahtar bloğu" or not _yer_tutucu(m.group(0)):
                bulgu.append(ad)
                break
    return bulgu


def yama_tara(metin, vs, commit_desen=None):
    """`git diff`/`git log -p` çıktısı → ["commit dosya: neden", ...]."""
    bulgu, dosya, commit = [], "?", ""
    for satir in metin.splitlines():
        if commit_desen and satir.startswith(commit_desen):
            commit = satir[len(commit_desen):].strip()[:10] + " "
            continue
        if satir.startswith("+++ "):
            dosya = satir[6:] if satir.startswith("+++ b/") else satir[4:]
            if dosya != "/dev/null" and SIR_DOSYA.search(dosya):
                bulgu.append(f"{commit}{dosya}: sır dosyası commit'lenemez")
            continue
        if not satir.startswith("+") or satir.startswith("+++"):
            continue
        for neden in satir_tara(satir[1:], vs):
            bulgu.append(f"{commit}{dosya}: {neden}")
    return bulgu


def _git(*a):
    r = subprocess.run(["git", *a], capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (a[0], r.stderr.strip()[:200]))
    return r.stdout


def cached_tara(k):
    adlar = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR").split()
    bulgu = [f"{a}: sır dosyası commit'lenemez" for a in adlar if SIR_DOSYA.search(a)]
    diff = _git("diff", "--cached", "-U0", "--no-color", "--diff-filter=ACMR")
    return bulgu + yama_tara(diff, degerler(k))


_ISARET = "@@sir-tara-commit@@ "


def aralik_tara(k, *rev, tavan=None):
    """Aralıktaki HER commit'in yaması (ara commit'te eklenip sonra silinen sır da yayına gider).
    --first-parent YOK: birleştirmeyle gelen dal commit'leri de taranır."""
    metin = _git("log", "-p", "-U0", "--no-color", "--no-ext-diff", "--diff-filter=ACMR",
                 "--format=" + _ISARET + "%H", *(["-n", str(tavan)] if tavan else []), *rev)
    return yama_tara(metin, degerler(k), commit_desen=_ISARET)


def raporla(bulgu, baglam):
    print("⛔ Ghost sır kapısı — %s durdu (değerler basılmadı):" % baglam, file=sys.stderr)
    for b in sorted(set(bulgu)):
        print("   - " + b, file=sys.stderr)
    print("   Değeri dosyadan çıkar, $DEGISKEN/secrets.env kullan. Test örneğiyse satıra '%s' yaz."
          % YOKSAY, file=sys.stderr)


def main(argv):
    k = kok()
    try:
        if argv[:1] == ["--cached"]:
            bulgu, baglam = cached_tara(k), "commit"
        elif argv[:1] == ["--aralik"] and len(argv) > 1:
            bulgu, baglam = aralik_tara(k, argv[1]), "push"
        elif argv[:1] == ["--tip"] and len(argv) > 1:
            rev = [argv[1]]
            if "--uzak" in argv:
                rev += ["--not", "--remotes=" + argv[argv.index("--uzak") + 1]]
            bulgu, baglam = aralik_tara(k, *rev, tavan=TAVAN), "push"
        else:
            print(__doc__.split("Kullanım:")[1], file=sys.stderr)
            return 2
    except Exception as e:      # tarayamadıysa güvenli taraf: çağıran durdurur
        print("⛔ Ghost sır kapısı — tarama yapılamadı: %s" % e, file=sys.stderr)
        return 2
    if bulgu:
        raporla(bulgu, baglam)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
