"""İlk satır akıştan (Faz 2.7, 2026-10-29) — turun ilk görünür cümlesi sahibe saniyeler içinde, aynı balon sonuçla dolar.

Önce: Kokpit'te "Ghost çalışıyor · 2:14" şeridi vardı ama Sohbet'te ilk balon tur bitince (p50 49 sn, p95 12 dk)
geliyordu; Telegram'da yalnız ⏳ nabzı (4 dk). Denetim 06 #1: ilk görünür satır ≤3 sn.
Şimdi: run_claude'un stream-json geri çağrısı sarılır; ilk `metin` olayı (modelin "şunu yapacağım" cümlesi)
GECIKME sn sonra hâlâ sonuç gelmemişse `on_ilk(metin)` ile gönderilir (köprü: Telegram sendMessage + Kokpit döküm
satırı gecici=1). Tur bitince köprü AYNI balonu sonuçla günceller (Telegram editMessageText, Kokpit outbox_guncelle);
sonuç ilk cümleden önce gelirse ilk satır hiç gitmez (kısa turda çift balon yok). Ölçü: `gonderim_ts` → metrik ilk_satir_sn.
Araç adımları Akış sayfasında kalır (uygulama katlanır satır olarak gösterir).
"""
import re
import threading
import time

from ghost import akis

GECIKME = 0.8          # sn — sonuç bu kadar sürede gelirse ilk satır gönderilmez
EN_UZUN = 300          # ilk satır karakter tavanı (tek cümle/ilk satır)


def ilk_cumle(metin, n=EN_UZUN):
    """İlk dolu satırın ilk cümlesi (≤n karakter)."""
    satirlar = [x.strip() for x in (metin or "").splitlines() if x.strip()]
    if not satirlar:
        return ""
    ilk = " ".join(satirlar[0].split())
    m = re.split(r"(?<=[.!?…])\s", ilk, 1)
    c = m[0].strip() if m else ilk
    return c if len(c) <= n else c[: n - 1] + "…"


class Sarmal:
    """on_event sarmalayıcısı. Kullanım:
        s = Sarmal(on_ilk=gonder); res = run_claude(..., on_event=s.sar(on_event)); s.kapat()
        s.refs (gönderildiyse on_ilk'in dönüşü), s.gonderim_ts (ölçü)."""

    def __init__(self, on_ilk, gecikme=GECIKME, simdi=None):
        self.on_ilk, self.gecikme = on_ilk, gecikme
        self._simdi = simdi or time.time
        self._kilit = threading.Lock()
        self._zamanlayici = None
        self._bitti = False
        self.metin = None
        self.refs = None
        self.gonderim_ts = None

    def _ates(self):
        with self._kilit:
            if self._bitti or self.refs is not None or not self.metin:
                return
            metin = self.metin
        try:
            r = self.on_ilk(metin)
        except Exception:  # noqa: BLE001 — ilk satır turu düşürmesin
            r = None
        with self._kilit:
            if r is not None and not self._bitti:
                self.refs, self.gonderim_ts = r, self._simdi()

    def gor(self, satir):
        """Bir stream satırı: ilk `metin` olayını yakala, zamanlayıcıyı kur. Sonuç satırı → kapat."""
        try:
            olaylar = akis.stream_olaylar(satir)
        except Exception:  # noqa: BLE001
            return
        for ev in olaylar:
            if ev.get("tur") == "sonuc":
                self.kapat()
                return
            if ev.get("tur") == "metin" and self.metin is None:
                c = ilk_cumle(ev.get("metin", ""))
                if not c:
                    continue
                with self._kilit:
                    if self._bitti or self.metin is not None:
                        return
                    self.metin = c
                    if self.gecikme <= 0:
                        pass
                    else:
                        self._zamanlayici = threading.Timer(self.gecikme, self._ates)
                        self._zamanlayici.daemon = True
                        self._zamanlayici.start()
                if self.gecikme <= 0:
                    self._ates()

    def sar(self, on_event):
        def _cb(satir):
            self.gor(satir)
            if on_event:
                on_event(satir)
        return _cb

    def kapat(self):
        """Tur bitti: bekleyen zamanlayıcı iptal; gönderilmişse refs kalır (köprü günceller)."""
        with self._kilit:
            self._bitti = True
            z, self._zamanlayici = self._zamanlayici, None
        if z:
            z.cancel()
        return self.refs
