"""Sohbet sunucusu istemcisi (Faz 3.2): köprü tarafında `kalici_oturum.KaliciOturum` ile AYNI arayüz —
`motor._run_kalici` farkı bilmez. Her çağrı kendi kısa bağlantısını açar; `tur` bağlantıyı tur boyunca tutar.

Köprü restart'ı: köprü her yeni turdan önce `tur_id` koyar (kesik_mesaj kaydıyla aynı kimlik); açılışta kayıt
tur_id taşır → `devralinabilir(tur_id)` evet derse köprü mesajı yeniden KOŞTURMAZ; `bagla_id` özniteliğini koyar,
bir sonraki `tur()` sunucudaki süren/bitmiş tura `bagla` ile katılır
ve sonucu olağan yoldan (ilk satır balonu, metrik, döküm) teslim eder. `uygun()` bu kipte True döner ki
`_run_kalici` süreci yeniden başlatıp turu öldürmesin.

`kapat("köprü kapanıyor")` sunucuya GİTMEZ (uzak=True): süreç köprüyle ölmesin diye bu birim var. FREEZE ve
model/oturum değişimi normal `kapat`/`baslat` ile sunucuya iner.
"""
import json
import os
import socket
import time

from ghost import sohbet_sunucu

BAGLANTI_SN = 5
KOPRU_KAPANIS_NEDENLERI = ("köprü kapanıyor",)


def _istek(obj, zaman_asimi=BAGLANTI_SN, yol=None):
    """Tek istek → tek cevap (akışsız op'lar). Sunucu yoksa OSError fırlatır."""
    with _bagla(yol, zaman_asimi) as f:
        f.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        f.flush()
        satir = f.readline()
    if not satir:
        raise OSError("sohbet sunucusu cevap vermedi")
    return json.loads(satir.decode("utf-8"))


class _Baglanti:
    def __init__(self, yol, zaman_asimi):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(zaman_asimi)
        self.s.connect(yol or sohbet_sunucu.soket_yolu())
        self.f = self.s.makefile("rwb")

    def __enter__(self):
        return self.f

    def __exit__(self, *a):
        for x in (self.f, self.s):
            try:
                x.close()
            except Exception:
                pass


def _bagla(yol=None, zaman_asimi=BAGLANTI_SN):
    return _Baglanti(yol, zaman_asimi)


def sunucu_durumu(yol=None):
    """{durum} ya da None (soket yok / cevap yok)."""
    try:
        d = _istek({"op": "durum"}, yol=yol)
        return d if isinstance(d, dict) and "canli" in d else None
    except (OSError, ValueError):
        return None


def sunucu_var(yol=None):
    return sunucu_durumu(yol) is not None


class KaliciIstemci:
    uzak = True

    def __init__(self, log=None, yol=None):
        self.log = log or (lambda m: None)
        self.yol = yol
        self.on_gec_sonuc = None
        self.tur_id = None          # köprü koyar: bir sonraki YENİ turun kimliği (kesik_mesaj kaydıyla aynı)
        self.bagla_id = None        # köprü koyar: bir sonraki tur() sunucudaki bu tura BAĞLANIR (yeniden koşmaz)
        self.model = None
        self.sid = None
        self.err = [""]
        self.mesgul = False

    # ---- KaliciOturum arayüzü ----
    def _durum(self):
        return sunucu_durumu(self.yol) or {}

    def canli(self):
        return bool(self._durum().get("canli"))

    def uygun(self, model, sid):
        if self.bagla_id:                    # devralma kipi: süreç ne olursa olsun yeniden BAŞLATMA
            return True
        try:
            return bool(_istek({"op": "uygun", "model": model, "sid": sid}, yol=self.yol).get("uygun"))
        except (OSError, ValueError):
            return False

    def baslat(self, model, sid, resume, mcp_paths=()):
        r = _istek({"op": "baslat", "model": model, "sid": sid, "resume": bool(resume),
                    "mcp_paths": list(mcp_paths or ())}, zaman_asimi=60, yol=self.yol)
        if r.get("hata"):
            raise RuntimeError(r["hata"])
        self.model, self.sid = model, sid
        self.log("sohbet sunucusunda oturum açıldı model=%s %s" % (model, "resume" if resume else "yeni"))

    def kapat(self, neden=""):
        if neden in KOPRU_KAPANIS_NEDENLERI:
            self.log("sohbet sunucusu köprüden bağımsız — kapatılmadı (%s)" % neden)
            return
        try:
            _istek({"op": "kapat", "neden": neden}, zaman_asimi=20, yol=self.yol)
        except (OSError, ValueError) as e:
            self.log("sohbet sunucusu kapat hatası: %r" % e)

    def bosta_oku(self):
        try:
            s = _istek({"op": "bosta_oku"}, yol=self.yol).get("sonuclar") or []
        except (OSError, ValueError):
            return []
        for satir in s:
            if self.on_gec_sonuc:
                try:
                    self.on_gec_sonuc(satir)
                except Exception as e:   # noqa: BLE001
                    self.log("geç sonuç geri-çağrısı hatası: %r" % e)
        if s:
            self.log("sohbet sunucusu: %d geç sonuç teslim edildi" % len(s))
        return s

    def devralinabilir(self, tur_id):
        """Açılışta: sunucuda bu kimlikle süren ya da teslim edilmemiş bir tur var mı?"""
        d = self._durum()
        return bool(tur_id and d.get("tur_id") == tur_id and (d.get("mesgul") or d.get("bekleyen")))

    def tur(self, prompt, limit, on_event=None):
        """(sonuc|None, kuyruk, err, zaman_asimi, oldu) — KaliciOturum.tur ile aynı sözleşme."""
        bid, self.bagla_id = self.bagla_id, None
        tid, self.tur_id = self.tur_id, None
        if bid:
            istek = {"op": "bagla", "tur_id": bid, "limit": limit}
            self.log("sohbet sunucusundaki tura bağlanılıyor: %s" % bid[:12])
        else:
            istek = {"op": "tur", "prompt": prompt, "limit": limit, "tur_id": tid or tur_kimligi()}
        self.mesgul = True
        kuyruk = []
        try:
            with _bagla(self.yol, float(limit) + 180) as f:
                f.write((json.dumps(istek, ensure_ascii=False) + "\n").encode("utf-8"))
                f.flush()
                while True:
                    satir = f.readline()
                    if not satir:
                        return None, "\n".join(kuyruk), "sohbet sunucusu bağlantısı koptu", False, True
                    try:
                        m = json.loads(satir.decode("utf-8"))
                    except ValueError:
                        continue
                    if "olay" in m:
                        kuyruk.append(m["olay"])
                        del kuyruk[:-40]
                        if on_event:
                            try:
                                on_event(m["olay"])
                            except Exception:
                                pass
                        continue
                    if m.get("hata"):
                        if m["hata"] == "mesgul":
                            return None, "", "sohbet sunucusu meşgul (tur %s)" % m.get("tur_id"), False, True
                        return None, "\n".join(kuyruk), str(m["hata"]), False, True
                    if "son" in m:
                        son = m["son"]
                        if son is None:          # bağlanacak tur yok (bitmiş/teslim edilmiş/başka tur)
                            return None, "", "bağlanacak tur yok", False, True
                        sonuc, kuyruk_s, err, zaman_asimi, oldu = son
                        self.err = [err or ""]
                        return sonuc, kuyruk_s or "\n".join(kuyruk), err or "", bool(zaman_asimi), bool(oldu)
        except socket.timeout:
            return None, "\n".join(kuyruk), "sohbet sunucusu zaman aşımı", True, False
        except OSError as e:
            return None, "\n".join(kuyruk), "sohbet sunucusu: %r" % e, False, True
        finally:
            self.mesgul = False


def kalici_sec(claude_bin, ekstra_args, cwd, env, log, yerel=None):
    """Köprü için: sunucu varsa KaliciIstemci, yoksa yerel KaliciOturum (eski yol). `yerel` testte yamalanır."""
    if sunucu_var():
        log("sohbet: sunucu (ghost-sohbet) kullanılıyor — Claude süreci köprüden bağımsız")
        return KaliciIstemci(log=log)
    from ghost import kalici_oturum
    log("sohbet: ghost-sohbet yok — kalıcı süreç köprünün çocuğu (eski yol)")
    return (yerel or kalici_oturum.KaliciOturum)(claude_bin, ekstra_args, cwd=cwd, env=env, log=log)


def tur_kimligi():
    return "%d-%s" % (int(time.time()), os.urandom(4).hex())
