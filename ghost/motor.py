"""Motor — Claude CLI'yi koşturan TEK yer (Faz 2.9, 2026-10-29; denetim 03 2a/2b).

Köprüden (ops/telegram_bridge.py) ayrıldı: `run_claude` + akış okuyucu (`_akit`) + sonuç
çözücü (`_sonuc_coz`) + kalıcı süreç yolu (`_run_kalici`) ve bunların ayarı (CLAUDE ikilisi,
argümanlar, zaman aşımı, MCP ayar dosyaları, çalışma ortamı). Köprü yalnız yönlendirir;
sohbet/iş işçileri ve ikinci göz (verify) buradan çağırır. YEDEK MOTOR YOK (Faz 2.9, Sahip 2026-10-29:
`ghost/engines.py` failover kataloğu silindi; codex `ghost/codex.py`'de yalnız üçüncü göz). Zaman aşımı ve
kota işaretleri (is_timeout / is_cost_limit) de burada.

Ayar `.env`'den okunur (CLAUDE_BIN, JARVIS_CLAUDE_ARGS, JARVIS_TIMEOUT); `secrets.env`
yalnız alt sürecin ORTAMINA girer (Ghost curl/wrangler ile kullanır), bu modülün ayarına değil.
Günlük: köprü `gunluk`'a kendi log'unu bağlar; bağlı değilse stderr (journald).
"""
import collections
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.parse

import re

from ghost import chat_policy, config, modeller

BASE = config.BASE
AYAR = config._parse_env_file(os.path.join(BASE, ".env"))   # .env yoksa (CI) boş: varsayılanlar
CLAUDE = AYAR.get("CLAUDE_BIN", "/usr/local/bin/claude")
CLAUDE_ARGS = AYAR.get("JARVIS_CLAUDE_ARGS", "").split()
CLAUDE_TIMEOUT = int(AYAR.get("JARVIS_TIMEOUT", "1800"))

# Token/gecikme ekonomisi: sunucular ayrı dosyalarda, mesaj konusuna göre yalnız gerekli
# olan(lar) yüklenir (köprü `_mcp_servers_for`) — her mesajda hepsini (playwright headless
# Chromium dahil) ayağa kaldırmak yerine.
MCP_CONFIGS = {
    "github": os.path.join(BASE, "mcp-github.json"),        # salt-okunur uç (2026-10-19: 46→28 araç)
    "github-yaz": os.path.join(BASE, "mcp-github-yaz.json"),  # tam uç; yalnız PR/issue YAZMA niyetinde
    "sentry": os.path.join(BASE, "mcp-sentry.json"),
    "playwright": os.path.join(BASE, "mcp-playwright.json"),
    "sosumi": os.path.join(BASE, "mcp-sosumi.json"),        # Apple doküman/HIG/WWDC, 4 araç, anahtarsız
    "posthog": os.path.join(BASE, "mcp-posthog.json"),      # salt-okunur, cli kipi (tek exec aracı)
}

# Claude alt sürecinin ortamı: süreç ortamı + PATH ekleri + secrets.env (gitignore'da).
# API token'ları (Cloudflare vb.) burada; Ghost curl/wrangler ile kullanır.
ORTAM = dict(os.environ)
ORTAM["PATH"] = ORTAM.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"
ORTAM.update(config._parse_env_file(os.path.join(BASE, "secrets.env")))

gunluk = None          # köprü bağlar (bridge.log); yoksa stderr → journald

# Maliyet/kota kaynaklı hata bir ARIZA değil, bir SINIR (Sahip 2026-08-24): sessizce zayıf motora geçmek
# yerine sınır görünür olur, kararı Sahip verir. Zaman aşımı da arıza değil (Sahip 2026-08-28): uzun süren iş
# süresi dolmuş bir çalışmadır; tur SIFIRDAN tekrarlanmaz, yedek motora düşülmez (2026-08-20 hafıza #154 / iş #36:
# codex'in bağlamsız cevabı Ghost'un işi gibi belleğe inmişti). Faz 2.9'dan beri yedek motor hiç yok; işaretler
# yine de ayrışık: köprü sınırı tek satıra indirir, zaman aşımında "yarım" bloğu ekler.
COST_LIMIT = re.compile(
    r"usage limit|rate[ _-]?limit|too many requests|\b429\b|\b402\b|"
    r"quota|credit balance|insufficient (credit|balance|funds|quota)|"
    r"out of credits?|payment required|billing|upgrade to (a )?paid|"
    r"limit reached|kullan[ıi]m s[ıi]n[ıi]r[ıi]|kota",
    re.IGNORECASE,
)
TIMEOUT_MARK = "⏱ Zaman aşımı"
TIMEOUT = re.compile(
    r"⏱ Zaman a[şs][ıi]m[ıi]|zaman a[şs][ıi]m[ıi]|\btimed[ _-]?out\b|"
    r"\btimeout\b|TimeoutExpired",
    re.IGNORECASE,
)


def is_timeout(text):
    """Hata metni zaman aşımını mı anlatıyor (erişim arızasını değil)? True → tur tekrarlanmaz, süre sahibe gösterilir."""
    return bool(text and TIMEOUT.search(str(text)))


def is_cost_limit(text):
    """Hata metni maliyet/kota sınırını mı anlatıyor (erişim arızasını değil)? True → sınır olduğu gibi sahibe."""
    return bool(text and COST_LIMIT.search(str(text)))
_SESSIZ_SON = {}


def _log(msg):
    try:
        (gunluk or (lambda m: print(m, file=sys.stderr)))(msg)
    except Exception:
        pass


def _sessiz(nerede, e, araligi=600):
    """Sessiz `except` kör noktası olmasın: konum etiketiyle günlüğe, aynı konum 10 dk'da en çok bir kez."""
    try:
        simdi = time.time()
        if simdi - _SESSIZ_SON.get(nerede, 0) < araligi:
            return
        _SESSIZ_SON[nerede] = simdi
        _log("sessiz except %s: %r" % (nerede, e))
    except Exception:
        pass


def _mcp_vekile_cevir(ops=None):
    """Sır vekili ayaktaysa (Faz 2/2. aşama) GitHub (6. tur: + PostHog) MCP ayarının SIRSIZ kopyası: adres vekil, token yer tutucu
    ("Bearer vekil" — vekil gerçeğini ekler). Kopya ops/mcp_vekil/ altında (gitignore). Vekil düşükse ya da
    kurulu değilse dokunulmaz, özgün dosya kullanılır. Hata köprüyü düşürmez."""
    try:
        from ghost import vekil as _v
        if not (_v.kurulu() and _v._erisilir()):
            return
        d = os.path.join(ops or config.OPS, "mcp_vekil")
        os.makedirs(d, exist_ok=True)
        for ad, servis in (("github", "github-mcp"), ("github-yaz", "github-mcp-yaz"), ("posthog", "posthog-mcp")):
            kaynak = MCP_CONFIGS.get(ad)
            if not (kaynak and os.path.exists(kaynak)):
                continue
            with open(kaynak, encoding="utf-8") as f:
                cfg = json.load(f)
            g = next(iter(cfg["mcpServers"].values()))
            u = urllib.parse.urlparse(g["url"])
            g["url"] = "%s/%s%s%s" % (_v.TABAN, servis, u.path, "?" + u.query if u.query else "")   # posthog: ?readonly=1&mode=cli
            g.setdefault("headers", {})["Authorization"] = "Bearer vekil"
            hedef = os.path.join(d, os.path.basename(kaynak))
            with open(hedef, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            MCP_CONFIGS[ad] = hedef
    except Exception as e:
        print("mcp vekil kopyası kurulamadı: %r" % e, file=sys.stderr)


_mcp_vekile_cevir()


def mcp_yollari(use_mcp=True):
    """use_mcp: True → hepsi; False/[] → hiçbiri; liste → yalnız o sunucu(lar). Var olan dosyalar."""
    names = list(MCP_CONFIGS) if use_mcp is True else (use_mcp or [])
    return [MCP_CONFIGS[n] for n in names if n in MCP_CONFIGS and os.path.exists(MCP_CONFIGS[n])]


def kill(proc):
    """Süreç GRUBUNU öldür (start_new_session=True ile açıldı): MCP çocukları yetim kalmasın."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception as _e:
            _sessiz("kill", _e)


def _akit(proc, limit, on_event):
    """stream-json: stdout'u SATIR SATIR oku, her satırı on_event'e ver, sonuç
    satırını (type=result) yakala. (sonuc_satiri, kuyruk_metni, stderr, zaman_asimi).
    Neden thread: communicate() çıktıyı sona kadar biriktirir — akış demek
    olayın olduğu anda events tablosuna düşmesi demek (kokpit Akış ekranı)."""
    kuyruk = collections.deque(maxlen=40)
    sonuc = [None]
    err = [""]

    def _stdout():
        try:
            for satir in proc.stdout:
                satir = satir.rstrip("\n")
                if not satir:
                    continue
                kuyruk.append(satir)
                if '"type":"result"' in satir.replace(" ", "") or '"type": "result"' in satir:
                    sonuc[0] = satir
                try:
                    on_event(satir)
                except Exception:
                    pass                      # akış asla cevabı düşürmesin
        except Exception as _e:
            _sessiz("_akit.stdout", _e)

    def _stderr():
        try:
            err[0] = proc.stderr.read() or ""
        except Exception as _e:
            _sessiz("_akit.stderr", _e)

    t1 = threading.Thread(target=_stdout, daemon=True)
    t2 = threading.Thread(target=_stderr, daemon=True)
    t1.start(); t2.start()
    zaman_asimi = False
    try:
        proc.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        zaman_asimi = True
        kill(proc)
    t1.join(5); t2.join(5)
    return sonuc[0], "\n".join(kuyruk), err[0], zaman_asimi


def _sonuc_coz(out, err, returncode, session_id, model, on_usage):
    """CLI'nin nihai JSON'unu (json kipi ya da stream-json `result` satırı)
    (sonuç_metni, session_id, hata_mı) üçlüsüne çevirir; usage geri-çağrısını besler."""
    out = (out or "").strip()
    sid = session_id
    if out:
        try:
            obj = json.loads(out)
            sid = obj.get("session_id", sid)
            if on_usage:
                try:
                    on_usage({
                        "turn": chat_policy.ctx_tokens(obj.get("usage")),
                        "window": chat_policy.ctx_window(obj.get("modelUsage"),
                                                         model=model),
                        "cost": obj.get("total_cost_usd") or 0.0,
                    })
                except Exception:
                    pass                       # ölçüm asla cevabı düşürmesin
            result = obj.get("result") or obj.get("text") or ""
            if obj.get("is_error"):
                return ("⚠️ " + (result or "hata"), sid, True)
            return (result.strip() or "(boş yanıt)", sid, False)
        except Exception:
            return (out, sid, False)  # json değilse ham çıktı
    if returncode != 0:
        return ("⚠️ Hata:\n" + (err or "bilinmeyen")[-1500:], sid, True)
    return ("(boş yanıt)", sid, False)


def _zaman_asimi_metni(limit):
    # Metin TIMEOUT_MARK'tan gelir: kapılar (is_timeout) bu işarete bakıyor — ikisi ayrışmasın.
    return "%s (%d sn) — işi böl." % (TIMEOUT_MARK, limit)


def _sentetik_sonuc(on_event, zaman_asimi, nerede):
    """result satırı yok (zaman aşımı / erken ölüm): akışa sentetik sonuç düş —
    kokpit "Ghost çalışıyor" göstergesi bu olaya bakar (akis.sohbet_durumu)."""
    if not on_event:
        return
    try:
        on_event(json.dumps({"type": "result", "is_error": True,
                             "subtype": "timeout" if zaman_asimi else "kesildi"}))
    except Exception as _e:
        _sessiz(nerede, _e)


def _run_kalici(kalici, prompt, model, session_id, resume, limit, on_usage, on_event):
    """run_claude'un kalıcı süreç yolu: model/oturum uyuyorsa açık süreci kullan,
    uymuyorsa (yeni oturum, /model) kapatıp yeniden aç. MCP'nin tamamı yüklenir
    (terminal gibi) — süreç ömrü boyunca araç listesi sabit kalsın, cache bozulmasın."""
    if not kalici.uygun(model, session_id):
        try:
            kalici.baslat(model, session_id, resume, mcp_yollari(True))
        except Exception as e:
            return ("⚠️ Çalıştırma hatası: %r" % e, session_id, True)
    sonuc, kuyruk, err, zaman_asimi, oldu = kalici.tur(prompt, limit, on_event)
    if sonuc is None:
        _sentetik_sonuc(on_event, zaman_asimi, "_run_kalici")
    if zaman_asimi:
        return (_zaman_asimi_metni(limit), session_id, True)
    if sonuc is None:
        return ("⚠️ Hata:\n" + (err or kuyruk or "bilinmeyen")[-1500:], session_id, True)
    return _sonuc_coz(sonuc, err, 0, session_id, model, on_usage)


def run_claude(prompt, model, session_id=None, resume=False, on_proc=None,
               use_mcp=True, timeout=None, on_usage=None, on_event=None, kalici=None, ozerk=False):
    """(sonuç_metni, session_id, hata_mı) döndürür. on_proc: Popen'ı kaydeder.

    kalici: ghost.kalici_oturum.KaliciOturum verilirse tur o süreçte koşar
    (tek süreç çok tur; use_mcp/on_proc yok sayılır, MCP'nin tamamı yüklüdür).

    on_event: verilirse CLI `stream-json` kipinde koşar ve HER ham satır bu
    geri-çağrıya gider (ghost/akis.py normalize edip events'e yazar). Sonuç
    ayrıştırması değişmez: `result` satırı json kipindeki nesnenin aynısıdır
    (result, session_id, is_error, usage, modelUsage, total_cost_usd).
    use_mcp: False/[] → entegrasyon yok (hızlı sohbet için); True → geriye
    dönük, hepsi; liste (["github", ...]) → yalnız o sunucu(lar) yüklenir.

    on_usage: CLI'nin döndürdüğü `usage`/`modelUsage`/`total_cost_usd`
    ölçümlerini alan geri-çağrı — bağlam doluluğunun tek doğru ölçüsü orada
    (bkz. ghost/chat_policy.py). Geri-çağrı: havuzda işçiler paralel koşar,
    modül seviyesinde global tutmak yarış koşulu olurdu.

    ozerk: GHOST_OZERK=1 Claude SÜRECİNİN ortamına girer; güvenlik kapısı bu işaretle
    geri alınmaz eylemleri araç seviyesinde reddeder. Oturumun Bash'i bu ortamı
    değiştiremez — kanca claude sürecinin çocuğudur (2026-10-28)."""
    limit = timeout or CLAUDE_TIMEOUT
    # Takma ad (opus/sonnet/…) → API+CLI'nin ortak EN GÜNCEL kimliği (Sahip 2026-10-01).
    # CLI'nin kendi eşlemesi derlendiği güne aittir; bkz. ghost/modeller.py.
    model = modeller.tam_model(model)
    if kalici is not None:
        return _run_kalici(kalici, prompt, model, session_id, resume, limit,
                           on_usage, on_event)
    cmd = [CLAUDE, "-p", prompt, "--output-format",
           "stream-json" if on_event else "json", "--model", model] + CLAUDE_ARGS
    if on_event:
        cmd.append("--verbose")               # stream-json --print bunu şart koşar
    paths = mcp_yollari(use_mcp)
    if paths:
        cmd += ["--mcp-config"] + paths
    if resume and session_id:
        cmd += ["--resume", session_id]
    elif session_id:
        cmd += ["--session-id", session_id]
    try:
        env = dict(ORTAM, GHOST_OZERK="1") if ozerk else ORTAM
        proc = subprocess.Popen(cmd, cwd=BASE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=env,
                                start_new_session=True)
        if on_proc:
            on_proc(proc)
        if on_event:
            sonuc, kuyruk, err, zaman_asimi = _akit(proc, limit, on_event)
            if sonuc is None:
                _sentetik_sonuc(on_event, zaman_asimi, "run_claude")
            if zaman_asimi:
                return (_zaman_asimi_metni(limit), session_id, True)
            # result satırı yoksa CLI erken öldü: dönüş kodu sıfır değilse hata,
            # sıfırsa kuyruk metni ham çıktı gibi işlenir
            if sonuc is None and proc.returncode != 0:
                return ("⚠️ Hata:\n" + (err or kuyruk or "bilinmeyen")[-1500:],
                        session_id, True)
            out = sonuc if sonuc is not None else kuyruk
        else:
            try:
                out, err = proc.communicate(timeout=limit)
            except subprocess.TimeoutExpired:
                kill(proc)
                return (_zaman_asimi_metni(limit), session_id, True)
    except Exception as e:
        return ("⚠️ Çalıştırma hatası: %r" % e, session_id, True)

    return _sonuc_coz(out, err, proc.returncode, session_id, model, on_usage)
