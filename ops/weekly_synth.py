#!/usr/bin/env python3
"""Haftalık stratejik sentez — köprünün istek kuyruğuna bir iş bırakır.

launchd her Pazar akşamı çalıştırır. Köprü isteği alıp tam entegrasyonla
(PostHog, Cloudflare, GitHub, Sentry) çalıştırır ve sonucu Telegram'a yollar.
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROMPT = (
    "HAFTALIK STRATEJİK SENTEZ. Sen Ghost'sun — sahibin operasyon zekâsı.\n"
    "Kaynakları oku/çek:\n"
    "• ~/jarvis/intel/ — bu haftanın istihbarat brifingleri\n"
    "• ~/jarvis/ops/jobs/ — bu hafta yapılan işler/olaylar\n"
    "• PostHog (env: POSTHOG_API_KEY/HOST/PROJECT_ID, salt-okunur) — kullanıcı/"
    "aktivite/funnel trendi\n"
    "• Cloudflare (env token) — sitelerin haftalık trafiği, anomali\n"
    "• GitHub/Sentry (MCP) — commit temposu, açık hatalar\n\n"
    "ÜRET (düz metin, telefonda okunacak, başlık '📊 Haftalık Sentez — [tarih]'):\n"
    "1) Bu haftanın 3 önemli gelişmesi (istihbarat + metrik + olaylardan süz).\n"
    "2) Ürün sinyali: PostHog'da dikkat çeken trend/değişim (varsa sayıyla).\n"
    "3) Öneri: gelecek hafta ODAKLAN → ..., ERTELE → ... (net, eyleme dönük).\n"
    "4) İKİNCİ GÖZ DESENİ: ~/jarvis/ops/verify.jsonl — kör denetçinin bu "
    "haftaki kararları. 'eksik'/'yanlış' çıkanları grupla ve şu soruyu "
    "cevapla: HANGİ TÜR iddiada sistematik yakalanıyorum? (ör. 'kurulum "
    "bitti' derken doğrulamadan; ör. emrin ikinci yarısını atlayarak). "
    "Tek cümlelik desen çıkarsa 'Ders:' diye yaz — asıl değer tek tek "
    "denetimlerde değil bu desende. Karar yoksa 'bu hafta denetim yok' de.\n"
    "5) GÜVENLİK KAPISI: ~/jarvis/ops/guvenlik_kapisi.log'daki bu haftanın ENGEL satırlarını say ve "
    "ayır: (a) yanlış alarm (gerçek bir işi durdurmuş — hangi kural, öneri) (b) olası saldırı/injection "
    "(güvenilmez içerikten gelmiş görünen) (c) Ghost'un kendi alışkanlığı (değeri basma vb.). Yoksa "
    "'kapı bu hafta sessiz' de. KARANTİNA: ~/jarvis/ops/karantina.log'da bu hafta kaç okuma yapıldı ve kaçı "
    "talimat=true (dış metin Ghost'a iş yaptırmaya çalıştı) — sayı ve kaynak türü (posta/spool/web/masa); 0 ise 'karantina temiz' de.\n"
    "6) ÖZ-GELİŞİM (kendini geliştir): ~/jarvis/ops/bridge.log ve ops/audit.jsonl"
    "'deki hata/tekrar desenlerine bak. Ghost'un KENDİSİNDE gördüğün en değerli "
    "1-2 iyileştirmeyi öner (somut: hangi dosya, ne değişmeli, neden). Sahip "
    "onaylarsa uygulanacak — sen sadece öner, DEĞİŞTİRME.\n"
    "Kısa ve keskin ol; doldurma yok. Veri çekilemezse dürüstçe belirt.\n"
    "SON ADIM (Faz 1.16): sentez metnini ~/jarvis/dossier/sentez/<YYYY>-W<WW>.md dosyasına da YAZ "
    "(ISO hafta, örn. 2026-W44.md; dizin yoksa oluştur) — sabah brifingi ODAKLAN/ERTELE kararlarını oradan okur."
)

sys.path.insert(0, BASE)
from ghost import bus  # noqa: E402
eid = bus.istek(PROMPT, "haftalık sentez", "weekly", model="opus",
                announce="📊 Haftalık sentez hazırlanıyor")
print("haftalık sentez isteği bırakıldı: olay #%s" % eid)

# Yönetişim şeffaflık raporu — "Ghost bu hafta ne yaptı" (denetim özeti,
# claude'suz, doğrudan). Sentezle birlikte Telegram'a düşer.
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ghost import transparency, notify
    notify.telegram(transparency.report(days=7))
except Exception as e:
    print("şeffaflık raporu hatası:", e)
