"""Komut kayıt defteri — köprünün dispatcher'ı `ghost/` içine taşınıyor (Faz 3).

Köprü `handle_command` önce buraya bakar: `bul(cmd)` bir işleyici dönerse onu
`(arg, mgr, ctx)` ile çağırır ve biter; dönmezse eski if/elif zincirine düşer.
`ctx` köprü modülüdür (send, durum_text, saglik_text… oradan alınır) — böylece
komutlar tek tek göç eder, köprü her göçte kısalır, davranış değişmez.

`liste()` uygulama (GET /v1/komutlar) ve `setMyCommands` için tek kaynaktır;
köprüdeki BOT_COMMANDS henüz göçmemiş komutlar için yedek olarak birleşir.
"""
_KAYIT = {}       # "/durum" → (işleyici, açıklama, ana_ad)
_SIRA = []        # ana adlar, kayıt sırası (liste için)


def komut(ad, aciklama, *takma):
    def sar(f):
        _KAYIT[ad] = (f, aciklama, ad)
        for t in takma:
            _KAYIT[t] = (f, aciklama, ad)
        _SIRA.append(ad)
        return f
    return sar


def bul(cmd):
    r = _KAYIT.get((cmd or "").lower())
    return r[0] if r else None


def liste():
    """[(komut, açıklama)] — yalnız ana adlar, kayıt sırasıyla."""
    return [(ad, _KAYIT[ad][1]) for ad in _SIRA]


# ---------------------------------------------------------------- göçmüş komutlar
@komut("/durum", "sistem durumu", "/status")
def _durum(arg, mgr, ctx):
    ctx.send(mgr.chat_id, ctx.durum_text(mgr))


@komut("/saglik", "servisler, nöbetçiler, bağlam")
def _saglik(arg, mgr, ctx):
    ctx.send(mgr.chat_id, ctx.saglik_text(mgr))


@komut("/kota", "abonelik kotası: oturum, haftalık, Fable")
def _kota(arg, mgr, ctx):
    from ghost import kota
    if (arg or "").strip() == "yenile":
        kota.yenile()
    ctx.send(mgr.chat_id, kota.metin())


@komut("/jobs", "iş listesi")
def _jobs(arg, mgr, ctx):
    ctx.send(mgr.chat_id, mgr.jobs_summary())


@komut("/cancel", "<iş no|all> işi iptal et")
def _cancel(arg, mgr, ctx):
    if not arg:  # argümansız "hepsini iptal" tehlikeli bir varsayılandı
        return ctx.send(mgr.chat_id, "Kullanım: /cancel <iş no> veya /cancel all\n"
                        + mgr.jobs_summary())
    ctx.send(mgr.chat_id, mgr.cancel(arg))


@komut("/freeze", "[sebep] tüm otonom eylemleri durdur")
def _freeze(arg, mgr, ctx):
    from ghost import governance
    governance.freeze(arg or "telegram komutu")
    ctx.send(mgr.chat_id, "⛔ FREEZE — tüm otonom eylemler durdu. Açmak için /unfreeze")


@komut("/unfreeze", "freeze'i kaldır")
def _unfreeze(arg, mgr, ctx):
    from ghost import governance
    governance.unfreeze()
    ctx.send(mgr.chat_id, "▶️ Freeze kalktı — Ghost normal çalışıyor.")


@komut("/cihaz", "[ad] kokpit eşleme kodu üret")
def _cihaz(arg, mgr, ctx):
    from ghost import kokpit
    kod = kokpit.kod_uret(arg or "iPhone")
    if kod is None:     # onay servisi (Faz 1): kod servisten YALNIZ Telegram'a gider, dökümde görünmez
        return ctx.send(mgr.chat_id, "📱 Eşleme kodu Telegram'a gönderildi (10 dk, tek kullanım).")
    ctx.send(mgr.chat_id, "📱 Kokpit eşleme kodu (10 dk, tek kullanım): %s\n"
                          "Uygulamadaki Eşleme ekranına gir." % kod)


@komut("/cihazlar", "kokpit cihazlarını listele")
def _cihazlar(arg, mgr, ctx):
    from ghost import kokpit
    cs = kokpit.cihazlar()
    ctx.send(mgr.chat_id, "📱 Kokpit cihazları:\n" + ("\n".join(
        "• %s %s [%s] son: %s" % (c["id"], c["ad"], c["durum"], c["last_seen"] or "-")
        for c in cs) or "yok"))


@komut("/cihaz_iptal", "[id] kokpit cihazını kapat (id yoksa hepsi)", "/cihaz-iptal")
def _cihaz_iptal(arg, mgr, ctx):
    from ghost import kokpit
    n = kokpit.iptal(arg or None)
    ctx.send(mgr.chat_id, "🔒 %d kokpit cihazı kapatıldı." % n)


@komut("/kanal", "[kokpit|telegram] birincil kanalı gör/seç")
def _kanal(arg, mgr, ctx):
    from ghost import channels, governance
    if arg:
        try:
            k = channels.birincil_ayarla(arg.strip().lower())
        except ValueError as e:
            return ctx.send(mgr.chat_id, "Kullanım: /kanal kokpit|telegram (%s)" % e)
        governance.audit("owner", "kanal.birincil", governance.READ, k)
        ctx.send(mgr.chat_id, "📡 Birincil kanal artık %s.\n%s" % (k, channels.durum_metni()))
    else:
        ctx.send(mgr.chat_id, channels.durum_metni())


@komut("/istekler", "olay veriyolunda bekleyen otomatik istekler")
def _istekler(arg, mgr, ctx):
    from ghost import bus
    rows = bus.bekleyen_istekler(20)
    if not rows:
        return ctx.send(mgr.chat_id, "📨 Bekleyen otomatik istek yok.")
    ctx.send(mgr.chat_id, "📨 Bekleyen istekler:\n" + "\n".join(
        "• #%d %s [%s] %s" % (r["id"], r["ts"][11:16], r["source"], r["payload"].get("kind", ""))
        for r in rows))


# ---------------------------------------------------------------- 2026-09-03: kalan komutlar
# ctx = köprü modülü. Köprüye özgü şeyler (HELP, STATE_FILE, ROLE, baton, RC, kısayol
# prompt'ları, needs_approval, route_text) ctx'ten; ghost modülleri tembel import
# (ghost/api.py bu dosyayı komut listesi için yükler — ağır modül çekmesin).

@komut("/help", "Komut listesi")
def _help(arg, mgr, ctx):
    from ghost import cc_komutlar
    ctx.send(mgr.chat_id, ctx.HELP + "\n\n" + cc_komutlar.yardim())


@komut("/mcp", "bağlı entegrasyonlar")
def _mcp(arg, mgr, ctx):
    import json
    import os
    if not os.path.exists(ctx.MCP_CONFIG):
        return ctx.send(mgr.chat_id, "Henüz bağlı entegrasyon yok. GitHub ile "
                        "başlayalım — token oluşturup bana ver.")
    try:
        with open(ctx.MCP_CONFIG, encoding="utf-8") as f:
            servers = json.load(f).get("mcpServers", {})
        if not servers:
            return ctx.send(mgr.chat_id, "mcp.json boş.")
        lines = ["Entegrasyonlar:"]
        for name, s in servers.items():
            blob = json.dumps(s)
            ok = "PUT_" not in blob and "<" not in blob
            lines.append("• %s %s" % (name, "✅" if ok else "⚠️ token eksik"))
        ctx.send(mgr.chat_id, "\n".join(lines))
    except Exception as e:
        ctx.send(mgr.chat_id, "mcp.json okunamadı: %r" % e)


@komut("/new", "Sohbeti sıfırla", "/tazele", "/yeni")
def _new(arg, mgr, ctx, _cmd=None):
    """Sohbet oturumunu sıfırlayan İKİ komut. Otomatik tazeleme kapalı (bkz.
    ghost/chat_policy.py KARAR notu); tek sıfırlama yolu burası:
      /new    — temiz sayfa: hiçbir şey taşımaz, geçmişi de siler.
      /tazele — aynı konu, ucuz bağlam: son 8 alışverişi yeni oturuma devreder."""
    from ghost import chat_policy, continuity
    was = chat_policy.fill_percent(mgr.chat_ctx, mgr.chat_window)
    tok = f"{int(mgr.chat_ctx or 0):,}".replace(",", ".")
    carry = (_cmd or "/tazele") != "/new"
    if carry and mgr.history:
        mgr.chat_carry = "\n".join("K: %s\nG: %s" % (k, g) for k, g in mgr.history[-8:])
    elif not carry:
        mgr.history = []      # temiz sayfa: restart-kurtarma da eskiyi anmasın
    try:  # oturum kapanıyor: hafızaya yazılmadıysa bayrak bırak
        ipucu = mgr.history[-1][0] if mgr.history else ""
        continuity.oturum_bitti(mgr.chat_turns, ipucu)
    except Exception as e:
        ctx.log("süreklilik bayrağı bırakılamadı: %r" % e)
    mgr.chat_sid = None
    mgr.chat_established = False
    mgr.chat_turns = 0
    mgr.chat_ctx = 0
    mgr.chat_cost = 0.0       # oturum sayacı sıfırlanır; toplam korunur
    mgr.chat_advice_turn = 0
    mgr._save_chat()          # restart bu sıfırlamayı geri almasın
    ctx.send(mgr.chat_id, "%s (önceki bağlam %%%d — %s token%s)." % (
        "Bağlam tazelendi, konuyu devraldım" if mgr.chat_carry
        else "Yeni sohbet oturumu", was, tok, "" if mgr.chat_carry else ", taşınmadı"))


@komut("/model", "Motor seç (fable/opus/sonnet/haiku)")
def _model(arg, mgr, ctx):
    m = arg.lower()
    if m not in ("fable", "sonnet", "opus", "haiku"):
        return ctx.send(mgr.chat_id, "Motor: fable | opus | sonnet | haiku")
    mgr.state["model"] = m
    ctx.save_json(ctx.STATE_FILE, mgr.state)
    from ghost import modeller   # takma ad → en güncel tam kimlik (2026-10-01)
    ctx.send(mgr.chat_id, "Motor: %s → %s" % (m, modeller.tam_model(m)))


@komut("/guard", "Özerk işte onay aç/kapa")
def _guard(arg, mgr, ctx):
    mgr.state["guard"] = (arg.lower() != "off")
    ctx.save_json(ctx.STATE_FILE, mgr.state)
    ctx.send(mgr.chat_id, "Guard: %s" % ("on" if mgr.state["guard"] else "off"))


@komut("/sesli", "Sesli cevap aç/kapa")
def _sesli(arg, mgr, ctx):
    mgr.state["voice"] = (arg.lower() == "on")
    ctx.save_json(ctx.STATE_FILE, mgr.state)
    ctx.send(mgr.chat_id, "🔊 Sesli cevap: %s (sesli mesajla sorarsan her zaman sesli "
             "cevap gelir)" % ("açık" if mgr.state["voice"] else "kapalı"))


@komut("/ogren", "Hafızaya bilgi yaz")
def _ogren(arg, mgr, ctx):
    from ghost import memory
    if not arg:
        return ctx.send(mgr.chat_id, "Kullanım: /ogren <bilgi> — kalıcı hafızaya yaz")
    memory.remember(arg, kind="öğretilen")
    ctx.send(mgr.chat_id, "🧠 Kaydettim (%d kayıt)." % memory.count())


@komut("/motorlar", "AI motorları (failover)")
def _motorlar(arg, mgr, ctx):
    from ghost import engines
    ctx.send(mgr.chat_id, "🧠 Kurulu motorlar (failover sırası):\n" + "\n".join(
        "• %s — %s" % (n, note) for n, _c, note in engines.available()))


@komut("/departmanlar", "Uzman birimler")
def _departmanlar(arg, mgr, ctx):
    from ghost import agents
    ctx.send(mgr.chat_id, "🏢 Ghost departmanları:\n" + "\n".join(
        "• %s — %s" % (d["ad"], ", ".join(d["araclar"])) for d in agents.DEPARTMENTS.values()))


@komut("/rapor", "Şeffaflık: ne yaptım")
def _rapor(arg, mgr, ctx):
    from ghost import transparency
    days = int(arg) if arg.strip().isdigit() else 7
    ctx.send(mgr.chat_id, transparency.report(days=days))


@komut("/is", "İşimizin sayıları (AppA/backend/analitik)", "/iş")
def _is(arg, mgr, ctx):
    import sys
    try:  # sabah dijestiyle aynı kaynak; save=False → snapshot'a dokunmaz
        if ctx.OPS not in sys.path:
            sys.path.insert(0, ctx.OPS)
        import biz_digest
        text = biz_digest.build(save=False)
        ctx.send(mgr.chat_id, text.strip() if text
                 else "⚠️ Hiçbir kaynak yanıt vermedi (ASC/D1/PostHog).")
    except ImportError:
        ctx.send(mgr.chat_id, "Bu kurulumda iş dijesti rayı yok (ops/biz_digest.py).")
    except Exception as e:
        ctx.send(mgr.chat_id, "⚠️ İş dijesti hata: %r" % e)


@komut("/kazanc", "Kazanç birimi durumu (huni/ROI)", "/kazanç")
def _kazanc(arg, mgr, ctx):
    try:
        from ghost.economy import report, feedback
        low = arg.lower().split()
        if low and low[0] in ("gonderildi", "gönderildi") and len(low) >= 3:
            feedback.mark_submitted(int(low[1]), arg.split()[2])
            return ctx.send(mgr.chat_id, "✅ #%s gönderildi olarak işaretlendi." % low[1])
        if low and low[0] == "merged" and len(low) >= 2:
            feedback.mark_merged(int(low[1]))
            return ctx.send(mgr.chat_id, "✅ #%s merge olarak işaretlendi." % low[1])
        if low and low[0] in ("odendi", "ödendi") and len(low) >= 3:
            ok = feedback.mark_paid(int(low[1]), float(low[2]))
            return ctx.send(mgr.chat_id, "💰 #%s ödendi ($%s)." % (low[1], low[2])
                            if ok else "Bulunamadı.")
        if low and low[0] == "merge-kontrol":
            r = feedback.check_pr_merges()
            return ctx.send(mgr.chat_id, "🔄 %d PR yoklandı, %d merge, %d hata."
                            % (r["checked"], r["merged"], r["errors"]))
        ctx.send(mgr.chat_id, report.render()
                 + "\n\nDurum ilerlet: /kazanc gonderildi <id> <pr_url> · "
                 "merged <id> · odendi <id> <tutar> · merge-kontrol")
    except ImportError:
        ctx.send(mgr.chat_id, "Bu kurulumda Kazanç birimi yok (ghost/economy).")
    except Exception as e:
        ctx.send(mgr.chat_id, "⚠️ Kazanç hata: %r" % e)


def _coz_veya_denetle(arg, mgr, ctx, is_hunt, cmd):
    import threading
    if not arg.strip().split():
        return ctx.send(mgr.chat_id, "Kullanım: %s <fırsat_id> (/kazanc ile id'leri gör)" % cmd)
    try:
        opp_id = int(arg.split()[0])
    except ValueError:
        return ctx.send(mgr.chat_id, "Geçersiz id. /kazanc ile fırsatları listele.")
    chat = mgr.chat_id

    def work():
        try:
            from ghost.economy import ledger, pipeline, hunt_pipeline
            o = ledger.get_opportunity(opp_id)
            if not o:
                return ctx.send(chat, "Fırsat #%s bulunamadı." % opp_id)
            ctx.send(chat, "🔧 #%s (%s) %s — izole+hapisli ortamda başladı, birkaç dk sürebilir."
                     % (opp_id, o["repo"], "denetleniyor" if is_hunt else "çözülüyor"))
            if is_hunt:
                r = hunt_pipeline.audit_pipeline(opp_id, o["repo"])
                if r.get("stage") != "ready":
                    return ctx.send(chat, "⚠️ Durdu (%s): %s" % (r.get("stage"), r.get("note")))
                v = r.get("verification") or {}
                ctx.send(chat, "🎯 #%s denetim taslağı hazır — %s\n\n%s"
                         % (opp_id, r.get("note", ""), (r.get("findings") or "")[:3000]))
                if v.get("verdicts"):
                    ctx.send(chat, "🔍 Adversarial doğrulama:\n%s" % v["verdicts"][:2500])
            else:
                r = pipeline.solve_pipeline(opp_id, o["repo"], o["title"], o["url"])
                if r.get("stage") != "ready":
                    return ctx.send(chat, "⚠️ Durdu (%s): %s" % (r.get("stage"), r.get("note")))
                val = r.get("validation") or {}
                ctx.send(chat, "✅ #%s çözüm taslağı hazır (%s)\nDoğrulama: %s\n\n"
                         "```diff\n%s\n```\n\nİncele → doğruysa PR aç → /kazanc gonderildi %s <pr_url>"
                         % (opp_id, ", ".join(r.get("changed_files") or []),
                            val.get("summary", "-"), (r.get("diff") or "")[:2500], opp_id))
        except Exception as e:
            ctx.send(chat, "⚠️ Hata: %r" % e)
    threading.Thread(target=work, daemon=True).start()


@komut("/denetle", "Tek hedefi güvenlik denetle → bulgu")
def _denetle(arg, mgr, ctx):
    _coz_veya_denetle(arg, mgr, ctx, True, "/denetle")


@komut("/hedef", "Duran hedef (boşken çalışırım)")
def _hedef(arg, mgr, ctx):
    from ghost import goals
    low = arg.lower()
    if not arg or low == "listele":
        gs = goals.list_goals(all_states=True)
        if not gs:
            return ctx.send(mgr.chat_id, "Duran hedef yok.\n/hedef ekle <başlık> | "
                            "[detay] — Ghost 6 saatte bir üzerinde çalışır.")
        return ctx.send(mgr.chat_id, "🎯 Duran hedefler:\n" + "\n".join(
            "• #%d [%s] %s (%d tur)" % (g["id"], g["status"], g["title"][:60], g["work_count"])
            for g in gs))
    if low.startswith("ekle "):
        parts = [p.strip() for p in arg[5:].split("|", 1)]
        goals.add(parts[0], parts[1] if len(parts) > 1 else "")
        return ctx.send(mgr.chat_id, "🎯 Hedef kaydedildi — Ghost boş kaldıkça üzerinde "
                        "çalışıp rapor verecek.")
    if low.startswith(("durdur ", "sil ")):
        gid = int(arg.split()[1])
        ok = goals.set_status(gid, "duraklatıldı" if low.startswith("durdur") else "tamam")
        return ctx.send(mgr.chat_id, "🎯 Güncellendi." if ok else "Bulunamadı.")
    ctx.send(mgr.chat_id, "Kullanım: /hedef · /hedef ekle <başlık> | [detay] "
             "· /hedef durdur <no> · /hedef sil <no>")


@komut("/varlik", "Dünya modeli: varlık + ilişkiler")
def _varlik(arg, mgr, ctx):
    from ghost import entities
    if not arg:
        return ctx.send(mgr.chat_id, "Kullanım:\n/varlik <ad> — grafikte göster\n"
                        "/varlik ekle <ad> | <tür> | <not>\n"
                        "/varlik bagla <kaynak> | <ilişki> | <hedef>")
    low = arg.lower()
    if low.startswith("ekle "):
        parts = [p.strip() for p in arg[5:].split("|")]
        name = parts[0]
        etype = parts[1] if len(parts) > 1 else "şey"
        notes = parts[2] if len(parts) > 2 else None
        entities.add(name, etype, notes)
        return ctx.send(mgr.chat_id, "🕸 Eklendi: %s (%s)" % (name, etype))
    if low.startswith("bagla ") or low.startswith("bağla "):
        parts = [p.strip() for p in arg[6:].split("|")]
        if len(parts) != 3:
            return ctx.send(mgr.chat_id, "Kullanım: /varlik bagla <kaynak> | <ilişki> | <hedef>")
        entities.link(parts[0], parts[1], parts[2])
        return ctx.send(mgr.chat_id, "🕸 Bağlandı: %s —%s→ %s" % tuple(parts))
    ctx.send(mgr.chat_id, entities.render(arg))


@komut("/hafiza", "Kalıcı bellekte ara")
def _hafiza(arg, mgr, ctx):
    from ghost import memory
    if not arg:
        return ctx.send(mgr.chat_id, "Kullanım: /hafiza <sorgu> — kalıcı bellekte ara "
                        "(%d kayıt)" % memory.count())
    rows = memory.recall(arg, k=5)
    if not rows:
        return ctx.send(mgr.chat_id, "Hafızada eşleşme yok: %s" % arg)
    ctx.send(mgr.chat_id, "🧠 Hafıza:\n" + "\n".join(
        "• [%s|%s] %s" % (r[0][:10], r[1], r[2][:200]) for r in rows))


@komut("/izle", "URL değişim monitörü")
def _izle(arg, mgr, ctx):
    from ghost import monitors
    if not arg:
        ms = monitors.list_monitors()
        if not ms:
            return ctx.send(mgr.chat_id, "İzlenen bir şey yok.\n"
                            "/izle <url> [dakika] [analiz] — ekle\n/izle sil <no>")
        return ctx.send(mgr.chat_id, "👁 Monitörler:\n" + "\n".join(
            "• #%d %s — %ddk (%s)" % (m["id"], m["url"], m["interval_min"],
                                      m["last_status"] or "henüz koşmadı") for m in ms))
    if arg.lower().startswith("sil "):
        ok = monitors.remove(int(arg.split()[1]))
        return ctx.send(mgr.chat_id, "🗑 Silindi." if ok else "Bulunamadı.")
    parts = arg.split()
    url = parts[0]
    if not url.startswith("http"):
        url = "https://" + url
    mins = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
    analyze = "analiz" in [p.lower() for p in parts[1:]]
    monitors.add(url.split("//")[-1].split("/")[0], url, mins, analyze)
    ctx.send(mgr.chat_id, "👁 Monitör kuruldu: %s her %d dk%s"
             % (url, mins, " + değişimde analiz" if analyze else ""))


@komut("/hatirlat", "Hatırlatma kur")
def _hatirlat(arg, mgr, ctx):
    import time
    from ghost import schedule
    if not arg:
        return ctx.send(mgr.chat_id, "Kullanım: /hatirlat <zaman> <metin>\n"
                        "örn: /hatirlat 20dk çay demle · /hatirlat yarın 09:00 faturayı öde")
    spec, msg = ctx._split_spec(arg)
    if not spec or not msg:
        return ctx.send(mgr.chat_id, "Zamanı anlayamadım. Örnek: 10dk · 2 saat sonra · "
                        "09:30 · yarın 14:00 · her gün 09:00 · her cuma 18:00")
    sid, ts = schedule.add(spec, "reminder", msg)
    ctx.send(mgr.chat_id, "⏰ Kuruldu (%s): %s\nİlk çalışma: %s"
             % (spec, msg, time.strftime("%d.%m %H:%M", time.localtime(ts))))


@komut("/gorev", "Zamanlı görev kur")
def _gorev(arg, mgr, ctx):
    import time
    from ghost import schedule
    if not arg:
        ss = schedule.list_schedules()
        if not ss:
            return ctx.send(mgr.chat_id, "Zamanlı görev yok.\n"
                            "/gorev <zaman> | <görev> — ekle · /gorev sil <no>")
        return ctx.send(mgr.chat_id, "📋 Zamanlı görevler:\n" + "\n".join(
            "• #%d [%s] %s → %s" % (s["id"], s["spec"], s["payload"][:60],
                                    time.strftime("%d.%m %H:%M", time.localtime(s["next_ts"])))
            for s in ss))
    if arg.lower().startswith("sil "):
        ok = schedule.remove(int(arg.split()[1]))
        return ctx.send(mgr.chat_id, "🗑 Silindi." if ok else "Bulunamadı.")
    if "|" in arg:
        spec, task = [x.strip() for x in arg.split("|", 1)]
    else:
        spec, task = ctx._split_spec(arg)
    if not spec or not task or not schedule.parse_spec(spec):
        return ctx.send(mgr.chat_id, "Kullanım: /gorev <zaman> | <görev>\n"
                        "örn: /gorev her gün 08:30 | siteleri kontrol et, sorun varsa yaz")
    sid, ts = schedule.add(spec, "job", task)
    ctx.send(mgr.chat_id, "📋 Görev kuruldu (%s). İlk çalışma: %s"
             % (spec, time.strftime("%d.%m %H:%M", time.localtime(ts))))


@komut("/araclar", "Alet kutusu")
def _araclar(arg, mgr, ctx):
    from ghost.tools import registry as tool_registry
    tools = tool_registry.list_tools()
    lines = ["🧰 Alet kutusu (%d):" % len(tools)]
    for t in tools:
        lines.append("• %s [%s] — %s" % (t["name"], t["kind"], t["descr"][:60]))
    ctx.send(mgr.chat_id, "\n".join(lines))


@komut("/job", "Arka plan işi başlat")
def _job(arg, mgr, ctx):
    if not arg:
        return ctx.send(mgr.chat_id, "Kullanım: /job <görev>")
    # Onay ön-kapısı yok: /job'u sahibin kendisi yazıyor (düz metinle aynı kural —
    # bkz. route_text). Riskli adımı iş içinde Ghost sorar.
    mgr.enqueue_job(arg, kind="iş")
    ctx.send(mgr.chat_id, "🎫 Kuyruğa alındı — paralel çalışıyor, bitince rapor gelir.")


@komut("/mac", "Mac'te çalıştır / devral")
def _mac(arg, mgr, ctx):
    import os
    import time
    sub = arg.lower().strip()
    if sub == "on":
        ctx.baton_set("mac", int(time.time()), note=ctx._devir_note(mgr))
        return ctx.send(mgr.chat_id, "🖥 Mac'e geçiliyor — Mac, Telegram'ı DOĞRUDAN "
                        "devralıyor (~birkaç sn). Artık mesajların sunucuya uğramadan "
                        "Mac'te, anında çalışır. Mac uykudaysa ~45sn sonra VPS'e döner. "
                        "Geri dönmek: /mac off")
    if sub == "off":
        ctx.baton_set("vps", int(time.time()), note=ctx._devir_note(mgr))
        return ctx.send(mgr.chat_id, "🖥 VPS'e dönülüyor — komuta yine sunucuda.")
    if sub == "new":
        mgr.chat_sid = None
        mgr.chat_established = False
        mgr.chat_turns = 0
        mgr.chat_ctx = 0
        if ctx.ROLE == "mac":
            try:  # yereldeyiz: mac_worker oturumunu doğrudan sıfırla
                os.remove(ctx.MAC_SESSION_FILE)
            except OSError:
                pass
        else:
            mgr.queue_mac("", reset=True)
        return ctx.send(mgr.chat_id, "🖥 Mac oturumu sıfırlandı (temiz bağlam).")
    if not arg:
        b = ctx.baton_get()
        who = "Mac" if b.get("owner") == "mac" else "VPS"
        return ctx.send(mgr.chat_id, "Şu an dinleyen: %s\n/mac on – Mac devralsın · "
                        "/mac off – VPS'e dön · /mac new – Mac oturumu sıfırla · "
                        "/mac <iş> – tek seferlik iş" % who)
    if ctx.ROLE == "mac":  # zaten Mac'teyiz: kuyruk yetim kalır, yerel havuzda koş
        mgr.enqueue_job(arg, kind="mac iş")
        return ctx.send(mgr.chat_id, "🖥 Mac işi başladı (yerel).")
    mgr.queue_mac(arg)
    ctx.send(mgr.chat_id, "🖥 Mac işi kuyruğa alındı — Mac uyanıkken çalışır, sonuç buraya düşer.")


@komut("/cc", "Mac'te remote-control oturumu")
def _cc(arg, mgr, ctx):
    parts = arg.split(None, 1)
    sub = parts[0].lower() if parts else ""
    if sub == "list":
        if ctx.ROLE == "mac":
            return ctx.send(mgr.chat_id, ctx._rc_list())
        return ctx.send(mgr.chat_id, "🖥 RC oturumları Mac'te — önce /mac on, sonra /cc list.")
    if sub == "stop":
        t = parts[1].strip() if len(parts) > 1 else ""
        if not t:
            return ctx.send(mgr.chat_id, "Kullanım: /cc stop <isim>  (isimler için /cc list)")
        if ctx.ROLE == "mac":
            return ctx.send(mgr.chat_id, ctx._rc_stop(t))
        return ctx.send(mgr.chat_id, "🖥 RC oturumları Mac'te — önce /mac on, sonra /cc stop.")
    if not arg:
        return ctx.send(mgr.chat_id, "Kullanım: /cc <görev> — Mac'te REMOTE-CONTROL "
                        "oturumu açar; telefonda görünür, canlı sürersin.\n"
                        "/cc list — açık oturumlar · /cc stop <isim> — elle kapat")
    if ctx.ROLE == "mac":
        name, url, err = ctx.launch_rc(arg)
        if err:
            return ctx.send(mgr.chat_id, "⚠️ Oturum başlatılamadı: %s" % err)
        msg = "🖥 Remote-control oturumu başladı: %s\nGörev: %s" % (name, arg[:120])
        msg += ("\n📱 Telefonda aç: " + url) if url else \
               "\n📱 Claude uygulamasında birkaç sn içinde görünür."
        return ctx.send(mgr.chat_id, msg)
    mgr.queue_mac(arg, rc=True)
    ctx.send(mgr.chat_id, "🖥 /cc Mac'e iletildi — Mac uyanıkken remote-control oturumu "
             "başlar, uygulamada görünür.")


@komut("/arastir", "<kişi|şirket|konu|domain> OSINT araştırma dosyası (Claude Code skill'i, iş)")
def _arastir(arg, mgr, ctx):
    if not arg:
        return ctx.send(mgr.chat_id, "Kullanım: /arastir <kişi|şirket|konu|domain>\n"
                        "OSINT: kamuya açık kaynaklardan çapraz-doğrulanmış dosya.")
    # Prompt köprüde değil skill'de (.claude/skills/arastir): iş metni slash ile başlar,
    # iş yolu cc_komutlar.prompt_kur ile komutu başa alır, Claude Code skill'i açar.
    mgr.enqueue_job("/arastir " + arg, kind="araştırma")
    ctx.send(mgr.chat_id, "🔎 Araştırma başladı (arastir skill'i, arka plan) — çapraz doğrulanmış dosya gelecek.")


@komut("/fleet", "Ajan filosu (büyük işler)")
def _fleet(arg, mgr, ctx):
    if not arg:
        return ctx.send(mgr.chat_id, "Kullanım: /fleet <görev> (büyük/kapsamlı işler için)")
    prompt = ctx.shortcut_prompt("fleet", arg)
    if ctx.needs_approval(arg, mgr):
        pid = mgr.add_pending(prompt, kind="fleet")
        return ctx.send(mgr.chat_id, "⚠️ Filo görevi değişiklik içerebilir:\n“%s”\n"
                        "Onaylıyor musun?" % arg[:300],
                        buttons=[[("✅ Onayla", "ok:%s" % pid), ("❌ İptal", "no:%s" % pid)]])
    mgr.enqueue_job(prompt, kind="filo")
    ctx.send(mgr.chat_id, "🚁 Filo görevi başladı — böl, paralel işle, doğrula, sentezle. "
             "Bitince rapor gelir.")


def _kisayol(name):
    """deploy: köprü kısayol prompt'u (onaylı). istihbarat/denetim: prompt yok — iş metni
    '/istihbarat' ya da '/denetim <hedef>' olarak gider, Claude Code skill'i tek kaynak."""
    def h(arg, mgr, ctx):
        if name in ("istihbarat", "denetim"):
            prompt = ("/%s %s" % (name, arg)).strip()
        else:
            prompt = ctx.shortcut_prompt(name, arg)
        if name == "deploy" and mgr.state.get("guard", True):
            pid = mgr.add_pending(prompt, kind="deploy")
            return ctx.send(mgr.chat_id, "⚠️ Deploy: %s\nOnaylıyor musun?" % (arg or "?"),
                            buttons=[[("✅ Onayla", "ok:%s" % pid), ("❌ İptal", "no:%s" % pid)]])
        mgr.enqueue_job(prompt, kind=name)
        ctx.send(mgr.chat_id, "🎫 %s başladı." % name.capitalize())
    return h


komut("/istihbarat", "günlük istihbarat brifingi (Claude Code skill'i, iş)")(_kisayol("istihbarat"))
komut("/denetim", "[repo|site|hepsi] denetim filosu (Claude Code skill'i, iş)")(_kisayol("denetim"))
komut("/deploy", "deploy (onaylı)")(_kisayol("deploy"))


@komut("/sahne", "nabiz|temizle — uygulamada ekran bas")
def _sahne(arg, mgr, ctx):
    from ghost import sahne
    # 2026-09-07 (Sahip: "sahne ekranındaki demo bölümü kaldır"): 'ornek' demo sahnesi ve
    # argümansız /sahne'nin Nabız fotoğrafı basması kaldırıldı — donmuş anlık görüntü
    # Sahne'de günlerce asılı kalıyordu. Sahne yalnız gerçek iş çıktısı taşır.
    a = (arg or "").strip().lower()
    if a == "temizle":
        return ctx.send(mgr.chat_id, "🖼 Sahne temizlendi (seq %d)." % sahne.temizle())
    if a in ("nabiz", "nabız"):
        seq, uyari = sahne.goster(sahne.hazir_nabiz())
    else:
        return ctx.send(mgr.chat_id, "Kullanım: /sahne nabiz · /sahne temizle")
    ctx.send(mgr.chat_id, "🖼 Sahne basıldı (%s, seq %d) — uygulamada Sahne sekmesi." % (a, seq)
             + ("\nuyarı: " + "; ".join(uyari) if uyari else ""))


# ---------------------------------------------------------------- Faz 5: duyular
@komut("/nerede", "Sahip nerede + telefon durumu (izinli duyular)")
def _nerede(arg, mgr, ctx):
    from ghost import duyular
    ctx.send(mgr.chat_id, duyular.metin())


@komut("/bugun", "bugünün takvimi (telefondan)", "/bugün")
def _bugun(arg, mgr, ctx):
    from ghost import duyular
    ctx.send(mgr.chat_id, duyular.bugun_metin())


@komut("/duyular", "[ac|kapat] konum/takvim/cihaz duyularının şalteri")
def _duyular(arg, mgr, ctx):
    from ghost import duyular
    a = (arg or "").strip().lower()
    if a in ("ac", "aç", "on"):
        duyular.salter(True)
        return ctx.send(mgr.chat_id, "👁 Duyular AÇIK — konum/takvim/cihaz bağlama girer.\n\n"
                        + duyular.metin())
    if a in ("kapat", "kapa", "off"):
        duyular.salter(False)
        return ctx.send(mgr.chat_id, "🔕 Duyular KAPALI — uygulama veri gönderemez (403), "
                        "saklı veri bağlama girmez. Açmak için /duyular ac")
    ctx.send(mgr.chat_id, duyular.metin() + "\n\n(/duyular ac · /duyular kapat)")


# ---------------------------------------------------------------- Faz 6: eller
@komut("/gorevler", "telefona yazılan hatırlatıcı/etkinlikler ve sonuçları")
def _gorevler(arg, mgr, ctx):
    from ghost import gorevler
    a = (arg or "").strip()
    if a.startswith("iptal"):
        gid = a.split(None, 1)[1].strip("# ") if " " in a else ""
        if not gid.isdigit():
            return ctx.send(mgr.chat_id, "Kullanım: /gorevler iptal <no>")
        return ctx.send(mgr.chat_id, "🚫 #%s iptal edildi." % gid if gorevler.iptal(gid)
                        else "#%s bulunamadı ya da zaten bitmiş." % gid)
    ctx.send(mgr.chat_id, gorevler.metin(15))


@komut("/bolge", "[ekle <ad> [m] | sil <ad>] coğrafi tetik — varınca Ghost düşünür")
def _bolge(arg, mgr, ctx):
    from ghost import bolgeler
    p = (arg or "").split()
    if not p:
        return ctx.send(mgr.chat_id, bolgeler.metin())
    if p[0] in ("ekle", "kur") and len(p) >= 2:
        yaricap = int(p[-1]) if p[-1].isdigit() else bolgeler.VARSAYILAN_YARICAP
        ad = " ".join(p[1:-1] if p[-1].isdigit() else p[1:])
        b, hata = bolgeler.buradan(ad, yaricap)
        if hata:
            return ctx.send(mgr.chat_id, "❌ " + hata)
        return ctx.send(mgr.chat_id,
                        "📍 '%s' bölgesi kuruldu — %dm yarıçap%s.\n"
                        "Buraya girdiğinde Ghost uyanıp düşünecek. (Kaldır: /bolge sil %s)"
                        % (b["ad"], b["yaricap"],
                           (" · " + b["yer"]) if b.get("yer") else "", b["ad"]))
    if p[0] == "sil" and len(p) >= 2:
        ad = " ".join(p[1:])
        return ctx.send(mgr.chat_id, "🗑 '%s' silindi." % ad if bolgeler.sil(ad)
                        else "'%s' diye bir bölge yok." % ad)
    ctx.send(mgr.chat_id, "Kullanım: /bolge · /bolge ekle <ad> [yarıçap m] · /bolge sil <ad>\n\n"
             + bolgeler.metin())
