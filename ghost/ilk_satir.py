"""İlk satır akıştan (Faz 2.7, 2026-10-29) — turun ilk görünür cümlesi sahibe saniyeler içinde, aynı balon sonuçla dolar.

Önce: Kokpit'te "Ghost çalışıyor · 2:14" şeridi vardı ama Sohbet'te ilk balon tur bitince (p50 49 sn, p95 12 dk)
geliyordu; Telegram'da yalnız ⏳ nabzı (4 dk). Denetim 06 #1: ilk görünür satır ≤3 sn.
Şimdi: run_claude'un stream-json geri çağrısı sarılır; ilk `metin` olayı (modelin "şunu yapacağım" cümlesi)
GECIKME sn sonra hâlâ sonuç gelmemişse `on_ilk(metin)` ile gönderilir (köprü: Telegram sendMessage + Kokpit döküm
satırı gecici=1). Tur bitince köprü AYNI balonu sonuçla günceller (Telegram editMessageText, Kokpit outbox_guncelle);
sonuç ilk cümleden önce gelirse ilk satır hiç gitmez (kısa turda çift balon yok). Ölçü: `gonderim_ts` → metrik ilk_satir_sn.
Araç adımları Akış sayfasında kalır (uygulama katlanır satır olarak gösterir).

Ara notlar (2026-11-10, plan dossier/kokpit/sohbet-tur-deneyimi-plan-2026-11-10.md aşama 1): turun SONRAKİ metinleri
de kaybolmaz. Balon açıldıktan sonra gelen her metin `on_not(refs, metin)` ile aynı balona yazılır (köprü: yalnız Kokpit,
gecici kalır, push yok; Telegram ilk cümlede kalır — ağ çağrısı akış okuyucusunu bekletmesin) ve tam metniyle `notlar`a
girer. Tur bitince köprü `ara_notlar()`ı sonuçla birlikte döküm satırına yazar (son cevabın kendisi not sayılmaz).
Sıra güvencesi dökümde: geçici güncelleme yalnız HÂLÂ geçici olan satıra yazar (`outbox_guncelle(gecici=True)`),
yani geç kalan bir not sonuçla dolmuş balonu ezemez; `on_not` bu yüzden kilit dışında çağrılır.
"""
import json
import re
import threading
import time

from ghost import akis

GECIKME = 0.8          # sn — sonuç bu kadar sürede gelirse ilk satır gönderilmez
EN_UZUN = 300          # ilk satır karakter tavanı (tek cümle/ilk satır)
NOT_ARALIK = 2.0       # sn — canlı balon güncellemeleri arasındaki en kısa süre (atlanan not yine kayda girer)
NOT_TAVAN = 4000       # bir ara notun karakter tavanı (kayıt + canlı balon)
NOT_SAYI = 30          # tur başına saklanan en çok ara not (fazlasında en eskiler düşer)


def _satir_coz(satir):
    """Stream satırı → (tam metin blokları, sonuç metni|None, sonuç_mu). `akis.stream_olaylar` metni 600'de keser;
    not tam metin ister. Alt ajanın (parent_tool_use_id) metni sahibe not değildir."""
    try:
        o = json.loads(satir)
        if not isinstance(o, dict):
            return [], None, False
        t = o.get("type")
        if t == "result":
            r = o.get("result")
            return [], (r if isinstance(r, str) else None), True
        if t != "assistant" or o.get("parent_tool_use_id"):
            return [], None, False
        mesaj = o.get("message")
        icerik = mesaj.get("content") if isinstance(mesaj, dict) else None
        out = []
        for b in icerik if isinstance(icerik, list) else []:
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].strip():
                out.append(b["text"].strip()[:NOT_TAVAN])
        return out, None, False
    except Exception:  # noqa: BLE001 — bozuk/beklenmedik satır turu kırmasın
        return [], None, False


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

    def __init__(self, on_ilk, gecikme=GECIKME, simdi=None, on_not=None, not_aralik=NOT_ARALIK):
        self.on_ilk, self.gecikme = on_ilk, gecikme
        self.on_not, self.not_aralik = on_not, not_aralik
        self._simdi = simdi or time.time
        self._kilit = threading.Lock()
        self._zamanlayici = None
        self._bitti = False
        self.metin = None
        self.refs = None
        self.gonderim_ts = None
        self.notlar = []            # [{"ts": "YYYY-MM-DD HH:MM:SS", "metin": tam metin}] — turun bütün görünür metinleri
        self.sonuc_metin = None     # result satırının metni: son cevap, not sayılmaz
        self._son_not_ts = 0.0

    def _not_al(self, metin):
        """Bir görünür metin: kayda ekle; balon açıksa ve aralık dolduysa canlı güncelle. Asla fırlatmaz."""
        with self._kilit:
            if self._bitti:
                return
            simdi = self._simdi()
            self.notlar.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(simdi)), "metin": metin})
            del self.notlar[:-NOT_SAYI]
            if self.on_not is None or self.refs is None or simdi - self._son_not_ts < self.not_aralik:
                return
            self._son_not_ts = simdi
            refs = self.refs
        try:
            self.on_not(refs, metin)            # kilit DIŞINDA: yavaş bir yazma tur kapanışını bekletmesin (sıra güvencesi dökümde)
        except Exception:  # noqa: BLE001 — ara not turu düşürmesin
            pass

    def ara_notlar(self):
        """Turun ara notları: son cevapla aynı olan son metin hariç. Köprü sonuçla birlikte döküm satırına yazar."""
        with self._kilit:
            notlar = [dict(n) for n in self.notlar]
            son = (self.sonuc_metin or "").strip()[:NOT_TAVAN].strip()
        if notlar and son and " ".join(notlar[-1]["metin"].split()) == " ".join(son.split()):
            notlar.pop()                        # yalnız birebir aynı metin (boşluk farkı hariç); benzer başlayan not kalır
        return notlar

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
        """Bir stream satırı: ilk `metin` olayını yakala, zamanlayıcıyı kur. Sonuç satırı → kapat.
        Her görünür metin ayrıca ara not olarak işlenir (`_not_al`)."""
        metinler, sonuc, sonuc_mu = _satir_coz(satir)
        if sonuc_mu and sonuc is not None:
            with self._kilit:
                self.sonuc_metin = sonuc
        for m in metinler:
            self._not_al(m)
        try:
            olaylar = akis.stream_olaylar(satir)
        except Exception:  # noqa: BLE001
            return
        for ev in olaylar:
            if ev.get("tur") == "sonuc":
                self.kapat()
                return
            if ev.get("tur") == "metin" and self.metin is None and metinler:   # alt ajanın cümlesi ilk satır olmaz (2026-11-10)
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
