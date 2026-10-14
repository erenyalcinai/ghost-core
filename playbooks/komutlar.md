# Ghost Komut Referansı (v2)

Köprü: `ops/telegram_bridge.py` — async iş yöneticisi.

## Mimari
- **Ana döngü:** getUpdates + buton + yönlendirme. Asla bloklanmaz.
- **Sohbet şeridi (1 işçi):** sıralı, bağlam süren konuşma (izole session uuid).
- **İş havuzu (JARVIS_POOL=3 işçi):** paralel bağımsız görevler.
- **Onay kapısı (guard):** riskli iş önce Telegram'da [Onayla]/[İptal] sorar.

## Komutlar
| Komut | İş |
|---|---|
| düz metin/ses/foto/dosya | sohbet şeridi (bağlam sürer) |
| `/job <görev>` | paralel arka plan işi |
| `/fleet <görev>` | **ajan filosu**: böl → paralel alt-ajanlar → çapraz doğrula → sentezle. Kapsamlı denetim/migrasyon/derin review için. Riskli görevde onay ister. |
| `/jobs` | aktif işler |
| `/cancel <no\|all>` | iş iptal (süreç grubu öldürülür); argüman ZORUNLU — argümansız sadece listeler |
| `/new` | sohbet oturumunu sıfırla |
| `/durum` | sistem+site sağlığı (AI'sız, anında) |
| `/kota [yenile]` | abonelik kotası (Claude uygulamasındaki Kullanım ekranı gibi): oturum 5 sa · haftalık tüm modeller · haftalık Fable, yüzde+sıfırlanma; Nabız "Kota" bölümüyle aynı veri (`ghost/kota.py`, Mac Keychain belirteci, dolar değil) |
| `/istihbarat` | anında brifing — iş metni `/istihbarat`, Claude Code skill'i açılır (köprüde prompt YOK) |
| `/denetim <hedef>` | denetim filosu — iş metni `/denetim <hedef>`, skill tek kaynak |
| `/arastir <hedef>` | OSINT dosyası — iş metni `/arastir <hedef>`, skill tek kaynak |
| `/deploy <hedef>` | deploy — guard on'ken onay ister |
| `/model <fable\|opus\|sonnet\|haiku>` | motor seçimi; **fable = en güçlü** (Mythos sınıfı, Opus üstü), haiku = en hızlı/ucuz |
| `/guard <on\|off>` | riskli iş onayı aç/kapa |
| `/help` | komut listesi |

## Claude Code komutları (2026-09-04, Sahip onayı)
Sohbet motoru `claude -p` bir Claude Code oturumudur; skill'ler ve birkaç yerleşik o kipte
gerçekten çalışır. Kural: slash komutu prompt'un BAŞINDA olmalı — köprü notları (araç, duyu,
persona, hafıza) ARKAYA koyar (`ghost/cc_komutlar.prompt_kur`; sohbet + iş yolu).
- Kaynak: `.claude/skills/*/SKILL.md` otomatik keşfedilir (elle liste yok) + `cc_komutlar.GLOBAL`
  (code-review, simplify, security-review, cost, context, compact).
- Uygulama Komutlar sekmesi iki bölüm (Ghost · Claude Code, `grup` alanı `GET /v1/komutlar`);
  Telegram menüsünde yalnız `[a-z0-9_]` adlılar (code-review gibi tireliler yazılınca yine çalışır).
- Bu kipte ÇALIŞMAYANLAR (listede yok): /help, /status, /memory, /permissions, /doctor ("isn't
  available"); /clear ve /model Ghost'un oturum yönetimiyle çakışır → Ghost'unki geçerli.
- `/compact` deneysel: Ghost'un `/tazele`si konuyu devrederek yapar, tercih o.
- Kaldırılanlar (ölü): `/mail` (giden posta yok), `/bounty`, `/hunt`, `/tara`, `/survivor`, `/coz`
  (economy stand-down). `/denetle`, `/hedef`, `/kazanc` duruyor.

## Ayarlar (.env)
- `JARVIS_POOL` paralel iş sayısı (varsayılan 3)
- `JARVIS_MODEL` varsayılan motor (sonnet)
- `JARVIS_TIMEOUT` iş zaman aşımı sn (1800)
- `JARVIS_CLAUDE_ARGS` claude bayrakları (tam yetki modu)

## Riskli kalıplar (guard tetikler)
rm/delete/sil, git push, deploy, wrangler deploy, eas submit, force,
reset --hard, drop table, üzerine yaz — hepsi onay ister.

## Kod değişince
- **VPS köprüsü:** `ops/selfcheck.py` (15 dk) diskteki kod süreçten yeniyse ve köprü BOŞTAYSA
  kendiliğinden yeniler. Acele: `setsid nohup ops/self_restart.sh "ne değişti" >/dev/null 2>&1 &`
  (boşalana dek bekler). Sohbet turunun içinden ASLA `systemctl restart` — cevap kendi ebeveyniyle ölür.
- **Mac köprüsü (macbridge):** autoreload (WatchPaths) kendiliğinden yeniler.
- **Mac işçisi (mac_worker):** launchd dosyayı taze okur; reload gerekmez.
- **Kokpit API (ghost-api, kullanıcı birimi):** selfcheck bayatsa yeniler.

## Faz 3 (2026-09-03) — omurga komutları
- `/kanal [kokpit|telegram]` — birincil kanalı gör/seç. Kokpit birincilken her Ghost cevabı telefona push olur; telegram birincilken yalnız onay/iş/alarm. Varsayılan telegram, karar sahibin.
- `/istekler` — olay veriyolunda (type=request) bekleyen otomatik istekler (monitör, zamanlayıcı, hedef, webhook, uptime, haftalık sentez).
- `/sahne nabiz|temizle` — uygulamanın Sahne sekmesine ekran basar; `nabiz` gerçek sağlık verisi (kartlar + tablolar), argümansız çağrı yalnız kullanım yazar. (`ornek` demo sahnesi 2026-09-07'de kaldırıldı.)

## Faz 5 (2026-09-03) — duyular (telefondan gelen izinli bağlam)
- `/nerede` — Sahip hangi semtte (yer adı + koordinat + doğruluk), bugünün etkinlikleri,
  telefonun hâli (pil/şarj/ağ/düşük güç). Veri bayatsa satırda "bayat" yazar.
- `/bugun` (`/bugün`) — yalnız bugünün takvimi: başlık, saat, yer.
- `/duyular [ac|kapat]` — **ana şalter**. Kapalıyken uygulama veri gönderemez (403) ve
  saklı veri sohbet bağlamına girmez. Argümansız çağrı durumu basar.
- Nereden geliyor: Kokpit → Ayarlar → **Duyular** altındaki üç anahtar (konum · takvim ·
  telefon durumu). Varsayılan kapalı; her anahtar kendi iOS iznini ister, izin reddedilirse
  anahtar kendini geri kapatır. Konum semt hassasiyetinde (significant location change).
- Bayatlık: konum 6 sa, takvim 3 sa, cihaz 2 sa. Bu yaştan sonra veri Ghost'un
  bağlamına ENJEKTE EDİLMEZ — eski konumla kurulan cevap yanlış cevaptan beterdir.

## Faz 6 (2026-09-03) — eller (Ghost → telefon)
- `/gorevler` — Ghost'un telefona yazdığı hatırlatıcı/etkinlikler ve sonuçları
  (⏳ bekliyor · ✅ yazıldı · ❌ hata). İptal: `/gorevler iptal <no>`.
- `/bolge` — tanımlı coğrafi tetikler. `/bolge ekle <ad> [yarıçap m]` telefonun
  ŞU ANKİ konumundan daire kurar; `/bolge sil <ad>` kaldırır. Sahip o bölgeye
  girince Ghost uyanır ve düşünür. iOS tavanı 20 bölge, taban yarıçap 100 m,
  aynı bölgeye 15 dk soğuma.
- Ghost'un kendi aracı (komut değil, CLI):
  `python3 -m ghost.gorevler hatirlatici "<başlık>" "<yarın 10:00|30dk|ISO>" ["not"]`
  `python3 -m ghost.gorevler etkinlik "<ad>" "<başlangıç>" ["bitiş"] ["yer"]`
- **`/hatirlat` ile aynı şey DEĞİL:** o Ghost'un zamanlayıcısı, Telegram mesajı atar
  (telefon sessizdeyse kaybolur). Bu iOS'un kendi alarmı — Ghost kapalıyken de çalar.
