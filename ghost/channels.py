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
        for i in range(0, len(text), 4000):
            try:
                self.api_cagri("sendMessage", chat_id=self.chat_id, text=text[i:i + 4000],
                               **(params if i == 0 else {}))
            except Exception as e:
                if self.log:
                    self.log("send hata: %r" % e)


class AppKanal:
    ad = "kokpit"

    def __init__(self, log=None):
        self.log = log

    def send(self, text, buttons=None):
        from ghost import apns, kokpit
        kokpit.outbox_yaz(text, "ghost", buttons)
        try:
            if buttons:
                apns.herkese("Ghost onay bekliyor", text[:200], {"sekme": "kararlar"},
                             collapse="karar")
            elif birincil() == "kokpit" or son_kanal() == "kokpit":
                apns.herkese("Ghost", text[:200], {"sekme": "sohbet"}, collapse="sohbet")
        except Exception as e:
            if self.log:
                self.log("push hata: %r" % e)


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

    def send(self, text, buttons=None, yalniz=None):
        """`yalniz="telegram"` → yalnız o kanala. Kullanım: uygulamada zaten canlı bir
        karşılığı olan mesajlar (⏳ nabız satırı) dökümü kirletmesin."""
        for k in self.kanallar:
            if yalniz and getattr(k, "ad", None) != yalniz:
                continue
            try:
                k.send(text, buttons)
            except Exception:
                pass


def durum_metni():
    b = birincil()
    return ("📡 Kanallar: birincil %s · yedek %s · son mesaj %s\n"
            "(kokpit birincilken her cevap telefona push olur; telegram birincilken "
            "onay/iş/alarm + uygulamadan sorulan her şeyin cevabı)"
            % (b, "telegram" if b == "kokpit" else "kokpit", son_kanal() or "—"))
