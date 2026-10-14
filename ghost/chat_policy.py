"""Sohbet oturumu tazeleme politikası — SAF, test edilebilir.

KÖK SEBEP (2026-08-18, sahibin itirazı): eski kural "30 turda bir oturumu
tazele"ydi. Tur sayısı yanlış ölçü:

- **Maliyetle ilgisi yok.** 30 tane "tamam / olur / bak bakalım" neredeyse
  bedava; buna karşılık TEK bir tur (Ghost bir repo grepler, 3.000 satır dosya
  okur) bağlamı on binlerce token şişirir. Sayaç ikisini aynı sayıyor.
- **İşin ortasında kesiyor.** 30. tur bir hata ayıklamanın ortasına denk
  gelirse şerit ölür, yerine alışveriş başına birkaç yüz karaktere kırpılmış
  bir not geçer. "Dedim ya az önce" şikâyeti buradan çıkıyordu; not 4'ten 8
  alışverişe çıkarılmıştı — semptomu yamamak, sebebi değil.
- **Gerçek sinyal zaten elimizde ve çöpe gidiyordu.** Claude CLI her yanıtta
  `usage` (input + cache_read + cache_creation) ve `modelUsage[...].contextWindow`
  döndürüyor. Yani bağlamın kaç token olduğunu ve pencerenin ne kadarının
  dolduğunu TAM olarak biliyoruz. run_claude bu alanı ayrıştırıp atıyordu.

YENİ POLİTİKA — üç kapı, sırayla:
  1. DOLULUK: bağlam pencerenin `ratio`'sunu aşarsa tazele (asıl ölçü).
  2. ATALET: uzun sessizlikten sonraki ilk mesaj temiz başlar. Sebep sadece
     "konu değişmiştir" değil, EKONOMİK: prompt cache TTL'i dolduğunda eski
     bağlamı sürdürmek tam fiyattan yeniden okunur — turnikeyi asıl orada
     çevirmek gerekiyor.
  3. EMNİYET FRENİ: usage okunamıyorsa (eski CLI, yedek motor) yüksek bir tur
     tavanı yine de sonsuz büyümeyi keser.

Böylece kısa muhabbet hiç tazelenmez, ağır alet kullanan sohbet erken tazelenir
— yani tazeleme keyfi bir sayaca değil, gerçek doluluğa bağlanır.

KARAR (2026-08-18, Sahip): OTOMATİK TAZELEME KAPATILDI (`DEFAULT_AUTO = False`).
Ölçü doğrulanınca ortaya çıkan asıl itiraz şuydu: "mevcut iş varsa sıfırlamamalı
veya sıfırlama tamamen benim komutuma bağlı olmalı." İkisinden ikincisi seçildi,
çünkü BİRİNCİSİ MÜHENDİSLİK OLARAK GÜVENİLİR DEĞİL: sohbet şeridinden "şu an bir
iş sürüyor mu" diye bir sinyal yok — mesaj metnine bakıp tahmin etmek gerekirdi,
tahmin yanılınca tam da şikâyet edilen yere (işin ortasında kesilme) geri
dönülürdü. Komut ise %100 kesin.

Üstelik iki kapı da tam olarak EN KÖTÜ ANDA ateşliyordu:
- Doluluk kapısı, bağlam ağırlaştığında ateşler; bağlamı ağırlaştıran şey zaten
  derin iş yapmaktır (grep, dosya okuma, çok adımlı hata ayıklama). Yani "%60
  doldu" sinyali pratikte "bu iş ciddi" sinyaliyle aynı andı.
- Atalet kapısı saate bakar, konuya değil. Telefondan kullanımda bir saat ara
  normaldir; dönüşteki ilk mesaj çoğu kez "evet", "yap", "devam" gibi TEK BAŞINA
  hiçbir bilgi taşımayan bir mesajdır — bağlamın en çok gerektiği an.

Kapılar SİLİNMEDİ, yalnız eylemden koparıldı: `decide()` artık `advice` döndürür
(hangi kapı dolmuş olurdu), köprü bunu yanıtın altına bir satır uyarı olarak
ekler. Yani ekonomi sinyali görünür kalır, kararı Sahip verir: `/tazele`
(bağlamı sıfırla ama son alışverişleri devret) ya da `/new` (temiz sayfa).
`JARVIS_CHAT_AUTO_ROTATE=1` otomatiği geri açar.

İlgili: [[jarvis-altyapi]] (token ekonomisi), ops/telegram_bridge.py
"""

# Otomatik tazeleme AÇIK mı? Varsayılan KAPALI (bkz. modül başındaki KARAR).
# Kapalıyken kapılar ölçülmeye devam eder ama sadece `advice` üretir; oturumu
# yalnız sahibin komutu sıfırlar.
DEFAULT_AUTO = False

# Pencerenin bu oranı dolunca tazele.
# DÜZELTME (2026-08-18): önce "taşma riskine karşı" diye gerekçelendirmiştim,
# YANLIŞTI — Claude Code bağlam sınıra yaklaşınca kendi sıkıştırıyor
# (auto-compact), yani taşma zaten olmuyor. Doğru gerekçe SAF EKONOMİ:
# resmi doküman "Claude Code her istekte TÜM konuşmayı gönderir; araç kullanan
# her tur bunu bir daha taşır" diyor. Üstelik sıkıştırmanın kendisi de pahalı
# bir istek ("compacting a large context is itself a large request"), oysa
# temiz başlamak bedava. Yani beklemek değil, tazelemek ucuz.
DEFAULT_RATIO = 0.60
# Atalet kapısı — prompt cache ömrü referans alınır (abonelikte 1 saat).
# Cache soğuduktan sonraki ilk mesaj bağlamın TAMAMINI tam fiyattan yeniden
# işletir; büyük bir bağlamı öylece sürüklemek burada pahalıya patlar.
DEFAULT_IDLE_HOURS = 1.0
# ...ama küçük bir bağlamı soğuk da olsa taşımak ucuz — sırf ara verdi diye
# sürekliliği kırmanın anlamı yok. Atalet kapısı ancak bağlam bu boyutu
# aşmışsa çalışır (ikisi BİRLİKTE: soğuk VE ağır).
DEFAULT_IDLE_MIN_TOKENS = 60000
# Bağlam boyutundan bağımsız olarak: bu kadar sessizlikten sonra konu kesin
# değişmiştir, temiz başla.
DEFAULT_STALE_HOURS = 24.0
# usage hiç okunamazsa devreye giren tavan. Tur sayısı kötü bir ölçü olduğu için
# yüksek: normal koşulda ASLA tetiklenmemeli, yalnız sinyalsizlikte.
DEFAULT_HARD_TURNS = 250
# Ne CLI ölçümü ne model adı eşleşirse son varsayılan. Bilerek KÜÇÜK:
# bilinmeyen modelde erken uyarı, geç uyarıdan iyidir.
DEFAULT_WINDOW = 200000
# Model adı → pencere (2026-08-30, kaynak: Anthropic model kataloğu).
# CLI'nin nihai JSON'undaki `modelUsage` bir süredir BOŞ dönüyor ({}) →
# contextWindow ölçümü hiç gelmiyor ve kod 200k varsayılanına düşüyordu;
# 1M pencereli fable'da doluluk yüzdesi 5 kat şişik görünüyordu. Ölçüm
# gelirse YİNE ölçüm kazanır (ctx_window'da öncelik onda); bu harita yalnız
# ölçümsüz durumda model adından (alias ya da tam ID, alt dize eşleşmesi)
# pencereyi verir. Claude 5 ailesi (fable/opus/sonnet) 1M, haiku 200k.
MODEL_WINDOWS = (
    ("haiku", 200_000),
    ("fable", 1_000_000),
    ("mythos", 1_000_000),
    ("opus", 1_000_000),
    ("sonnet", 1_000_000),
)
# MUTLAK tavan. Oran tek başına yetmiyor: canlı ölçümde pencere 1.000.000 çıktı
# (1M bağlamlı model) → %60 = 600.000 token demek ki her tur servet yakar.
# Oran TAŞMAYA karşı korur, mutlak tavan EKONOMİYE karşı; hangisi önce dolarsa o.
DEFAULT_MAX_TOKENS = 300000


def ctx_tokens(usage):
    """TEK bir API çağrısının taşıdığı bağlam. SAF.

    ⚠️ CLI'nin nihai JSON'undaki `usage` alanına BUNU uygulama: orası bir turun
    TÜM araç iterasyonlarının TOPLAMI (canlı ölçüm 2026-08-18: köprü 671.199
    kaydetti, gerçek bağlam 74.486'ydı — 9 kat şişik, oturum boş yere
    tazelenecekti). Nihai `usage` bir MALİYET ölçüsüdür, doluluk ölçüsü değil.
    Doluluk için `ctx_from_transcript()` kullan.

    Üçü de bağlamdır: `input_tokens` (cache'lenmemiş yeni kısım),
    `cache_read_input_tokens` (cache'ten okunan gövde — ucuz ama BAĞLAMDA),
    `cache_creation_input_tokens` (cache'e yeni yazılan). `output_tokens`
    dahil DEĞİL: o üretilen yanıt, taşınan bağlam değil (bir sonraki turda
    zaten input tarafında görünür)."""
    u = usage or {}

    def _n(k):
        v = u.get(k)
        return v if isinstance(v, (int, float)) and v > 0 else 0
    return int(_n("input_tokens") + _n("cache_read_input_tokens")
               + _n("cache_creation_input_tokens"))


def ctx_from_transcript(tail_text):
    """Oturum transcript'inin (JSONL) son parçasından GERÇEK bağlam boyutu. SAF.

    Doğru ölçü budur: her `assistant` satırı TEK bir API çağrısıdır ve
    `message.usage` o çağrının taşıdığı bağlamı verir. Bağlam turlar boyunca
    büyüdüğü için SON geçerli satır güncel doluluğu gösterir.

    `tail_text` dosyanın sonundan okunan parça olabilir; baştaki yarım satır
    JSON olarak çözülemez ve sessizce atlanır (bu yüzden tam dosya okumaya
    gerek yok — transcript megabaytlara çıkabiliyor)."""
    import json as _json
    last = 0
    for ln in (tail_text or "").splitlines():
        if '"assistant"' not in ln:
            continue
        try:
            d = _json.loads(ln)
        except Exception:
            continue                            # yarım/bozuk satır
        if d.get("type") != "assistant":
            continue
        t = ctx_tokens((d.get("message") or {}).get("usage"))
        if t:
            last = t
    return last


def window_for_model(model, default=DEFAULT_WINDOW):
    """Model adından (alias: "fable" ya da tam ID: "claude-fable-5") pencere. SAF."""
    m = (model or "").lower()
    for key, w in MODEL_WINDOWS:
        if key in m:
            return w
    return default


def ctx_window(model_usage, default=DEFAULT_WINDOW, model=None):
    """Modelin gerçek bağlam penceresi. SAF.

    `modelUsage` sözlüğü model adı → {contextWindow: N} taşıyor. Sabit 200k
    varsaymak yerine CLI ne diyorsa onu kullanırız (model değişince politika
    kendiliğinden uyar). Birden fazla model varsa en KÜÇÜK pencere bağlayıcıdır.
    CLI ölçüm döndürmezse (2026-08-30'dan beri modelUsage boş geliyor) `model`
    adından MODEL_WINDOWS haritasıyla çözülür; o da eşleşmezse `default`."""
    windows = []
    for v in (model_usage or {}).values():
        w = (v or {}).get("contextWindow")
        if isinstance(w, (int, float)) and w > 0:
            windows.append(int(w))
    return min(windows) if windows else window_for_model(model, default)


def _gate(ctx_used, window, idle_seconds, turns, ratio, idle_hours,
          hard_turns, max_tokens, idle_min_tokens, stale_hours):
    """Hangi kapı dolmuş? SAF. Döner: gerekçe metni ("" = hiçbiri).

    Sıra önemli: mutlak tavan > doluluk > atalet > emniyet freni. Gerekçe metni
    kullanıcıya ve loga gider — tazeleme bir daha "sebepsiz unutma" görünmesin.
    Bu fonksiyon yalnız ÖLÇER; tazeleyip tazelememeye `decide()` karar verir."""
    window = window if (isinstance(window, (int, float)) and window > 0) else DEFAULT_WINDOW
    used = max(0, int(ctx_used or 0))
    pct = used / float(window)
    if max_tokens and used >= max_tokens:
        return ("bağlam %s token (mutlak tavan %s)"
                % (f"{used:,}".replace(",", "."),
                   f"{int(max_tokens):,}".replace(",", ".")))
    if used and pct >= ratio:
        return ("bağlam %d%% dolu (%s/%s token)"
                % (round(pct * 100), f"{used:,}".replace(",", "."),
                   f"{int(window):,}".replace(",", ".")))
    idle_s = idle_seconds or 0
    # Soğuk cache + AĞIR bağlam: sürüklemek tam fiyata mal olur.
    if idle_hours and idle_s >= idle_hours * 3600 and used >= idle_min_tokens:
        return ("%.0f saattir sessizlik + %s token bağlam — cache soğudu, "
                "taşımak tam fiyat" % (idle_s / 3600.0,
                                       f"{used:,}".replace(",", ".")))
    # Bağlam küçük olsa bile çok uzun sessizlikten sonra konu değişmiştir.
    if stale_hours and idle_s >= stale_hours * 3600:
        return "%.0f saattir sessizlik" % (idle_s / 3600.0)
    if hard_turns and (turns or 0) >= hard_turns:
        return "%d tur (emniyet freni — bağlam ölçülemedi)" % turns
    return ""


def decide(ctx_used, window=DEFAULT_WINDOW, idle_seconds=0.0, turns=0,
           ratio=DEFAULT_RATIO, idle_hours=DEFAULT_IDLE_HOURS,
           hard_turns=DEFAULT_HARD_TURNS, max_tokens=DEFAULT_MAX_TOKENS,
           idle_min_tokens=DEFAULT_IDLE_MIN_TOKENS,
           stale_hours=DEFAULT_STALE_HOURS, auto=DEFAULT_AUTO):
    """Oturum tazelensin mi? SAF.

    Döner: {"rotate": bool, "reason": str, "advice": str}

    `advice` HER ZAMAN dolu kapının gerekçesidir (varsa) — otomatik kapalıyken
    bile. `rotate`/`reason` ise yalnız `auto=True` iken dolar.

    VARSAYILAN `auto=False`: hiçbir koşulda kendiliğinden tazelenmez. Kapı
    dolduğunda tek yaptığı, köprünün yanıt altına bir satır uyarı koyabilmesi
    için `advice` üretmektir; oturumu sahibin komutu (/tazele, /new) sıfırlar.
    Gerekçesi modül başındaki KARAR notunda."""
    reason = _gate(ctx_used, window, idle_seconds, turns, ratio, idle_hours,
                   hard_turns, max_tokens, idle_min_tokens, stale_hours)
    if reason and auto:
        return {"rotate": True, "reason": reason, "advice": reason}
    return {"rotate": False, "reason": "", "advice": reason}


def fill_percent(ctx_used, window=DEFAULT_WINDOW):
    """Doluluk yüzdesi — /durum çıktısı için. SAF."""
    window = window if (isinstance(window, (int, float)) and window > 0) else DEFAULT_WINDOW
    return round(max(0, int(ctx_used or 0)) / float(window) * 100)
