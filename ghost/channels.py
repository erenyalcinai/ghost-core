"""Kanallar — Ghost'un ağızları (docs/kokpit.md Faz 3).

Köprünün `send()`'i artık buraya gelir: metin bir kez hazırlanır, her kanala
dağıtılır. Kanal listesi sabit değil; birincil kanal `ops/kanal.json` ile
seçilir (`/kanal kokpit|telegram`, Sahip kararı).

  TelegramKanal — Bot API sendMessage (4000'lik parçalar, ilk parçada butonlar).
                  `api_cagri` çağrı ANINDA çözülür ki testler köprünün `api`'sini
                  yamalayabilsin.
  AppKanal      — kokpit dökümü (outbox) + APNs push. Push kuralı:
                  butonlu (onay isteyen) her mesaj; birincil=kokpit ise HER
                  Ghost mesajı; ayrıca sahibin son mesajı UYGULAMADAN geldiyse
                  (son_kanal) her cevap — soru nereden geldiyse cevap oraya.

Hiçbir kanal diğerini düşürmez: her biri kendi hatasını yutar.
"""
import json
import os

from ghost import config

VARSAYILAN = "telegram"
KANALLAR = ("telegram", "kokpit")


def _yol():
    return os.path.join(config.OPS, "kanal.json")


def _oku():
    try:
        with open(_yol(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _yaz(d):
    tmp = _yol() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, _yol())


def birincil():
    k = _oku().get("birincil")
    return k if k in KANALLAR else VARSAYILAN


def birincil_ayarla(kanal):
    if kanal not in KANALLAR:
        raise ValueError("kanal: %s" % "|".join(KANALLAR))
    d = _oku()
    d["birincil"] = kanal
    _yaz(d)
    return kanal


def son_kanal():
    """sahibin SON mesajı hangi kanaldan geldi? Cevap o kanalı bulur: uygulamadan
    sorulduysa cevap uygulamaya push olur (2026-09-03, Sahip: "mesaj bildirimi gelmiyor").
    Birincil kanal kararını değiştirmez — yalnız 'soru nereden geldiyse cevap oraya'."""
    k = _oku().get("son")
    return k if k in KANALLAR else None


def son_kanal_ayarla(kanal):
    if kanal not in KANALLAR:
        return
    d = _oku()
    if d.get("son") != kanal:
        d["son"] = kanal
        _yaz(d)


class TelegramKanal:
    ad = "telegram"

    def __init__(self, api_cagri, chat_id, log=None, duzlestir=None):
        self.api_cagri, self.chat_id, self.log = api_cagri, chat_id, log
        self.duzlestir = duzlestir      # Markdown soyucu (köprünün _plain'i); uygulama HAM alır

    def send(self, text, buttons=None):
        if self.duzlestir:
            text = self.duzlestir(text)
        params = {}
        if buttons:  # [[(etiket, veri), ...], ...]
            kb = [[{"text": t, "callback_data": c} for (t, c) in row] for row in buttons]
            params["reply_markup"] = json.dumps({"inline_keyboard": kb})
        ids = []
        for i in range(0, len(text), 4000):
            try:
                r = self.api_cagri("sendMessage", chat_id=self.chat_id, text=text[i:i + 4000],
                                   **(params if i == 0 else {}))
                mid = ((r or {}).get("result") or {}).get("message_id") if isinstance(r, dict) else None
                if mid:
                    ids.append(mid)
            except Exception as e:
                if self.log:
                    self.log("send hata: %r" % e)
        return ids

    def guncelle(self, ref, text, buttons=None):
        """Faz 2.7: ilk satır mesajını (ref = send'in döndürdüğü id listesi) sonuçla değiştir; taşan parça yeni mesaj."""
        if self.duzlestir:
            text = self.duzlestir(text)
        mid = (ref or [None])[0] if isinstance(ref, list) else ref
        if not mid:
            return self.send(text, buttons)
        params = {}
        if buttons:
            kb = [[{"text": t, "callback_data": c} for (t, c) in row] for row in buttons]
            params["reply_markup"] = json.dumps({"inline_keyboard": kb})
        try:
            self.api_cagri("editMessageText", chat_id=self.chat_id, message_id=mid, text=text[:4000], **params)
        except Exception as e:
            if self.log:
                self.log("edit hata: %r" % e)
            if len(text) <= 4000:
                return self.send(text, buttons)
        ids = [mid]
        if len(text) > 4000:
            ids += self.send(text[4000:]) or []
        return ids


class AppKanal:
    ad = "kokpit"

    def __init__(self, log=None):
        self.log = log

    def send(self, text, buttons=None, gecici=False):
        from ghost import apns, kokpit
        rid = kokpit.outbox_yaz(text, "ghost", buttons, gecici=gecici)
        if gecici:
            return rid                      # ilk satır: push yok, sonuç gelince gider
        try:
            if buttons:
                apns.herkese("Ghost onay bekliyor", text[:200], {"sekme": "kararlar"},
                             collapse="karar")
            elif birincil() == "kokpit" or son_kanal() == "kokpit":
                apns.herkese("Ghost", text[:200], {"sekme": "sohbet"}, collapse="sohbet")
        except Exception as e:
            if self.log:
                self.log("push hata: %r" % e)
        return rid

    def guncelle(self, ref, text, buttons=None):
        """Faz 2.7: ilk satır balonunu (ref = döküm id) sonuçla doldur; push sonuçta gider (send kuralı)."""
        from ghost import apns, kokpit
        if not ref or not kokpit.outbox_guncelle(ref, text, buttons):
            return self.send(text, buttons)
        try:
            if buttons:
                apns.herkese("Ghost onay bekliyor", text[:200], {"sekme": "kararlar"}, collapse="karar")
            elif birincil() == "kokpit" or son_kanal() == "kokpit":
                apns.herkese("Ghost", text[:200], {"sekme": "sohbet"}, collapse="sohbet")
        except Exception as e:
            if self.log:
                self.log("push hata: %r" % e)
        return ref


def app_medya(yol, aciklama="", kaynak="ghost"):
    """Ghost'un Telegram'a attığı görsel/video/belge (ops/send_photo.py vb.) dökümde de
    görünsün. Push kuralı AppKanal.send ile aynı: yalnız kokpit birincilken.
    Döner: döküm satır id'si ya da None. Asla fırlatmaz."""
    try:
        from ghost import apns, kokpit
        rid = kokpit.outbox_yaz(aciklama or "", kaynak, medya=yol)
        if rid and kaynak == "ghost" and birincil() == "kokpit":
            apns.herkese("Ghost", (aciklama or "📎 %s" % os.path.basename(yol))[:200],
                         {"sekme": "sohbet"}, collapse="sohbet")
        return rid
    except Exception:
        return None


class Kanallar:
    """Dağıtıcı: aynı mesaj tüm kanallara. Sıra: uygulama (döküm) önce — Telegram
    çökse bile kayıt düşer; ikinci kanalın amacı tam olarak bu."""

    def __init__(self, *kanallar):
        self.kanallar = list(kanallar)

    def send(self, text, buttons=None, yalniz=None, gecici=False):
        """`yalniz="telegram"` → yalnız o kanala. Kullanım: uygulamada zaten canlı bir
        karşılığı olan mesajlar (⏳ nabız satırı) dökümü kirletmesin. → {kanal_adı: ref} (Faz 2.7: güncelleme için)."""
        refs = {}
        for k in self.kanallar:
            if yalniz and getattr(k, "ad", None) != yalniz:
                continue
            try:
                r = k.send(text, buttons, gecici=True) if (gecici and getattr(k, "ad", None) == "kokpit") else k.send(text, buttons)
                if r:
                    refs[getattr(k, "ad", "")] = r
            except Exception:
                pass
        return refs

    def guncelle(self, refs, text, buttons=None):
        """Faz 2.7: send(gecici=True) ile açılan balonları sonuçla doldur; ref'i olmayan kanal normal send alır."""
        out = {}
        for k in self.kanallar:
            ad = getattr(k, "ad", "")
            try:
                r = k.guncelle((refs or {}).get(ad), text, buttons) if hasattr(k, "guncelle") else k.send(text, buttons)
                if r:
                    out[ad] = r
            except Exception:
                pass
        return out


def durum_metni():
    b = birincil()
    return ("📡 Kanallar: birincil %s · yedek %s · son mesaj %s\n"
            "(kokpit birincilken her cevap telefona push olur; telegram birincilken "
            "onay/iş/alarm + uygulamadan sorulan her şeyin cevabı)"
            % (b, "telegram" if b == "kokpit" else "kokpit", son_kanal() or "—"))
