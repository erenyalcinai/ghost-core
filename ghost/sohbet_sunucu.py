"""Sohbet sunucusu (Faz 3.2, 2026-10-30 — bütünsel denetim 03 2c): kalıcı Claude sohbet süreci köprüden AYRI bir
systemd biriminde (`ghost-sohbet`) yaşar; köprü ona Unix soketinden konuşur.

NEDEN: cevabı yazan `claude` süreci köprünün çocuğuydu — köprü yeniden başlayınca (kendi yenilemesi, apt, OOM) tur
ölüyor, mesaj `kesik_mesaj` ile BAŞTAN koşuyordu (2026-09-26 kök nedeni; 30 günde 7 kayıp). Süreç burada durunca köprü
restart'ı turu kesmez: yeni köprü aynı tura YENİDEN BAĞLANIR (`bagla`), cevap kaybı 0, tekrar koşma 0.

TASARIM (ince sunucu — sohbet mantığı köprüde kalır):
- İçeride tek `kalici_oturum.KaliciOturum` (süreç, kuyruk, boşta kapanma, geç sonuç, ops/kalici_oturum.json aynen).
- Soket: $XDG_RUNTIME_DIR/ghost-sohbet.sock (testte GHOST_OPS/sohbet.sock). Satır-JSON istek/cevap, tek istek per bağlantı.
  op: durum · uygun{model,sid} · baslat{model,sid,resume,mcp_paths} · tur{prompt,limit,tur_id} (akış: {"olay":…}… +
  {"son":[sonuc,kuyruk,err,zaman_asimi,oldu]}) · bagla{tur_id} (süren tura katıl ya da teslim edilmemiş sonucu al) ·
  bosta_oku · kapat{neden}.
- Tur ayrı iş parçacığında koşar; istemci koparsa tur SÜRER, olaylar/sonuç `Tur` nesnesinde bekler; `bagla` aynı tur_id
  ile gelen ilk istemciye yeni olayları + sonucu verir. Sonuç teslim edilene kadar `bekleyen` kalır (yeni bir `tur`
  isteği gelirse düşer ve günlüğe yazılır — köprü açılışta önce `bagla` dener).
- Aynı anda tek tur (KaliciOturum tek turlu); meşgulken `tur` → {"hata":"mesgul"}.
- Ortam/ikili köprüyle aynı kaynaktan: motor.CLAUDE, CLAUDE_ARGS, ORTAM (secrets alt sürecin ortamında), cwd BASE.
  GHOST_OZERK yok → kimlik servisi bu süreci özerk saymaz (sohbet = sahibin ağzı, köprüyle aynı).
- Köprü tarafı: ghost/sohbet_istemci.KaliciIstemci (aynı arayüz) — sunucu yoksa eski yol (süreç köprünün çocuğu).

CLI: python3 -m ghost.sohbet_sunucu kos | durum
"""
import json
import os
import signal
import socket
import sys
import threading
import time
import uuid

from ghost import config, kalici_oturum

OLAY_TAVAN = 400          # bağlantısız turda bellekte tutulan olay satırı (son N; sonuç ayrı)
GEC_SONUC_ARALIK = 5      # sn: boşta geç sonuç süpürme
_log = None


def log(msg):
    if _log:
        try:
            _log(msg)
        except Exception:
            pass


def soket_yolu():
    if os.environ.get("GHOST_SOHBET_SOKET"):
        return os.environ["GHOST_SOHBET_SOKET"]
    if os.environ.get("GHOST_OPS"):
        return os.path.join(config.OPS, "sohbet.sock")
    kok = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    if not os.path.isdir(kok):
        kok = os.path.join(os.path.expanduser("~"), ".ghost")
        os.makedirs(kok, exist_ok=True)
    return os.path.join(kok, "ghost-sohbet.sock")


class Tur:
    """Bir sohbet turu: olaylar aboneliklere dağıtılır; sonuç teslim edilene kadar saklanır."""

    def __init__(self, tur_id, prompt):
        self.id = tur_id or uuid.uuid4().hex
        self.ozet = (prompt or "")[:120]
        self.basladi = time.time()
        self.olaylar = []            # bağlantısız anlarda biriken satırlar (son OLAY_TAVAN)
        self.aboneler = []           # [(kilit, liste, olay)] — her istemci kendi kuyruğu
        self.kilit = threading.Lock()
        self.son = None              # [sonuc, kuyruk, err, zaman_asimi, oldu]
        self.bitti = threading.Event()
        self.teslim = False

    def abone_ol(self):
        q = {"satirlar": [], "olay": threading.Event()}
        with self.kilit:
            q["satirlar"].extend(self.olaylar)      # bağlantısızken birikenler önce
            self.olaylar = []
            self.aboneler.append(q)
        return q

    def abone_birak(self, q):
        with self.kilit:
            if q in self.aboneler:
                self.aboneler.remove(q)

    def olay(self, satir):
        with self.kilit:
            if self.aboneler:
                for q in self.aboneler:
                    q["satirlar"].append(satir)
                    q["olay"].set()
            else:
                self.olaylar.append(satir)
                del self.olaylar[:-OLAY_TAVAN]

    def bitir(self, son):
        with self.kilit:
            self.son = list(son)
            for q in self.aboneler:
                q["olay"].set()
        self.bitti.set()


class SohbetSunucu:
    def __init__(self, claude_bin=None, ekstra_args=None, cwd=None, env=None, bosta_sn=None, log_fn=None):
        global _log
        _log = log_fn
        from ghost import motor
        kw = {}
        if bosta_sn is not None:
            kw["bosta_sn"] = bosta_sn
        self.kalici = kalici_oturum.KaliciOturum(
            claude_bin or motor.CLAUDE, list(ekstra_args if ekstra_args is not None else motor.CLAUDE_ARGS),
            cwd=cwd or config.BASE, env=env if env is not None else motor.ORTAM, log=log, **kw)
        self.kalici.on_gec_sonuc = self._gec_sonuc
        self.gec_sonuclar = []
        self.tur = None               # süren ya da teslim bekleyen Tur
        self.kilit = threading.Lock()
        self.dur = threading.Event()
        self.srv = None
        self.basladi = time.time()

    # ---- durum ----
    def _gec_sonuc(self, satir):
        with self.kilit:
            self.gec_sonuclar.append(satir)

    def durum(self):
        t = self.tur
        return {"canli": self.kalici.canli(), "model": self.kalici.model, "sid": self.kalici.sid,
                "mesgul": bool(t and not t.bitti.is_set()),
                "tur_id": t.id if t else None, "tur_ozet": t.ozet if t else None,
                "bekleyen": bool(t and t.bitti.is_set() and not t.teslim),
                "gec_sonuc": len(self.gec_sonuclar), "pid": os.getpid(), "acilis": int(self.basladi)}

    # ---- istekler ----
    def _isle(self, istek, gonder):
        op = istek.get("op")
        if op == "durum":
            return gonder(self.durum())
        if op == "uygun":
            return gonder({"uygun": self.kalici.uygun(istek.get("model"), istek.get("sid"))})
        if op == "baslat":
            with self.kilit:
                if self.tur and not self.tur.bitti.is_set():
                    return gonder({"hata": "mesgul", "tur_id": self.tur.id})
            try:
                self.kalici.baslat(istek.get("model"), istek.get("sid"), bool(istek.get("resume")),
                                   istek.get("mcp_paths") or ())
            except Exception as e:
                return gonder({"hata": "baslat: %r" % e})
            return gonder({"ok": True})
        if op == "kapat":
            self.kalici.kapat(str(istek.get("neden") or ""))
            return gonder({"ok": True})
        if op == "bosta_oku":
            self.kalici.bosta_oku()
            with self.kilit:
                s, self.gec_sonuclar = self.gec_sonuclar, []
            return gonder({"sonuclar": s})
        if op == "tur":
            return self._tur(istek, gonder)
        if op == "bagla":
            return self._bagla(istek, gonder)
        return gonder({"hata": "bilinmeyen op: %r" % op})

    def _tur(self, istek, gonder):
        with self.kilit:
            if self.tur and not self.tur.bitti.is_set():
                return gonder({"hata": "mesgul", "tur_id": self.tur.id})
            if self.tur and not self.tur.teslim:
                log("teslim edilmemiş sonuç düştü (tur %s): yeni tur istendi" % self.tur.id)
            t = Tur(istek.get("tur_id"), istek.get("prompt") or "")
            self.tur = t
        limit = float(istek.get("limit") or 1800)

        def _kos():
            try:
                son = self.kalici.tur(istek.get("prompt") or "", limit, on_event=t.olay)
            except Exception as e:   # noqa: BLE001
                son = (None, "", "sunucu: %r" % e, False, True)
            t.bitir(son)
        threading.Thread(target=_kos, name="tur-" + t.id[:8], daemon=True).start()
        return self._akit(t, gonder, limit)

    def _bagla(self, istek, gonder):
        t = self.tur
        tid = istek.get("tur_id")
        if not t or (tid and t.id != tid) or t.teslim:
            return gonder({"son": None, "tur_id": t.id if t else None})
        return self._akit(t, gonder, float(istek.get("limit") or 1800))

    def _akit(self, t, gonder, limit):
        """Olayları akıt, sonucu teslim et. İstemci koparsa sessizce çık (tur sürer)."""
        q = t.abone_ol()
        try:
            son_an = time.time() + limit + 120
            while True:
                satirlar = None
                with t.kilit:
                    if q["satirlar"]:
                        satirlar, q["satirlar"] = q["satirlar"], []
                    q["olay"].clear()
                    bitti = t.son is not None
                if satirlar:
                    for s in satirlar:
                        if not gonder({"olay": s}):
                            return None          # istemci koptu
                if bitti:
                    if gonder({"son": t.son, "tur_id": t.id}):
                        t.teslim = True
                    return None
                if time.time() > son_an:
                    gonder({"hata": "sunucu: tur sınırı aşıldı", "tur_id": t.id})
                    return None
                q["olay"].wait(2)
        finally:
            t.abone_birak(q)

    # ---- soket ----
    def _baglanti(self, c):
        c.settimeout(None)
        f = c.makefile("rwb")

        def gonder(obj):
            try:
                f.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                f.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False
        try:
            satir = f.readline()
            if not satir:
                return
            try:
                istek = json.loads(satir.decode("utf-8"))
            except ValueError:
                gonder({"hata": "json değil"})
                return
            self._isle(istek, gonder)
        except Exception as e:   # noqa: BLE001
            log("bağlantı hatası: %r" % e)
            try:
                gonder({"hata": "sunucu: %r" % e})
            except Exception:
                pass
        finally:
            try:
                f.close()
            except Exception:
                pass
            try:
                c.close()
            except Exception:
                pass

    def _gec_sonuc_dongusu(self):
        while not self.dur.wait(GEC_SONUC_ARALIK):
            try:
                self.kalici.bosta_oku()
            except Exception as e:   # noqa: BLE001
                log("geç sonuç süpürme hatası: %r" % e)

    def servis(self, yol=None):
        yol = yol or soket_yolu()
        try:
            os.unlink(yol)
        except FileNotFoundError:
            pass
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(yol)
        os.chmod(yol, 0o600)
        self.srv.listen(8)
        self.srv.settimeout(1.0)
        threading.Thread(target=self._gec_sonuc_dongusu, daemon=True).start()
        log("sohbet sunucusu dinliyor: %s (pid %d)" % (yol, os.getpid()))
        try:
            while not self.dur.is_set():
                try:
                    c, _ = self.srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(target=self._baglanti, args=(c,), daemon=True).start()
        finally:
            try:
                self.srv.close()
            except Exception:
                pass
            try:
                os.unlink(yol)
            except OSError:
                pass
            log("sohbet sunucusu durdu")

    def durdur(self, surec_kapat=True):
        self.dur.set()
        if surec_kapat:
            self.kalici.kapat("sunucu kapanıyor")


def kos(log_fn=None):
    from ghost import gunluk
    log_fn = log_fn or (lambda m: gunluk.yaz("sohbet", m))
    s = SohbetSunucu(log_fn=log_fn)

    def _dur(sig, frm):
        # systemd stop: süreç kapanmak üzere; claude çocuğu da aynı cgroup'ta ölür — temiz kapat.
        s.durdur(surec_kapat=True)
    for sg in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sg, _dur)
        except (ValueError, OSError):
            pass
    s.servis()


def _main(argv):
    argv = list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[-1].strip())
        return 0
    if argv[0] == "kos":
        kos()
        return 0
    if argv[0] == "durum":
        from ghost import sohbet_istemci
        d = sohbet_istemci.sunucu_durumu()
        print(json.dumps(d, ensure_ascii=False) if d else "sohbet sunucusu YOK (%s)" % soket_yolu())
        return 0 if d else 1
    print("bilinmeyen komut: %s" % argv[0])
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
