"""Kalıcı sohbet süreci — bir `claude` süreci, çok tur.

NEDEN (2026-09-07, Finans ölçümü): köprü her sohbet turunu AYRI bir
`claude -p --resume` süreciyle koşturuyordu. Her yeni süreç sistem istemini
(git durumu + son commit listesi + MEMORY.md) yeniden üretir; araya bir commit
ya da yeni bir dosya girince istem prefix'i değişir ve prompt cache'in TAMAMI
yeniden yazılır. Ölçüm: commit denk gelen turlarda %87 cache kaçırması,
14 günde 11,5M token boşa yazım. Terminaldeki Claude Code oturumu bunu
yaşamaz — süreç bir kez açılır, sistem istemi oturum boyunca sabittir.

Deney (haiku, ~/jarvis): ayrı süreç + untracked dosya → cache_creation 20k
(kaçırma); AYNI süreç + aynı değişiklik → cache_creation 73 (isabet).

Bu modül terminal davranışını köprüye taşır: `claude -p --input-format
stream-json --output-format stream-json` bir kez açılır, her tur stdin'e bir
`user` satırı yazılır, `result` satırı gelene kadar stdout okunur.

Kurallar:
- Süreç yalnız sohbet şeridi içindir (tek iş parçacığı kullanır). İş havuzu
  eskisi gibi tek atımlık süreç açar.
- Model ya da oturum kimliği değişirse (/model, /new, /tazele) süreç kapanır,
  yenisi açılır — `uygun()` bunu söyler.
- Tur zaman aşımına uğrarsa süreç öldürülür; sonraki tur `--resume` ile yeni
  süreç açar (transcript diskte, kayıp yok).
- Boşta kalınca (varsayılan 60 dk = cache ömrü) kendini kapatır: RAM boşalır,
  CLAUDE.md/hafıza değişiklikleri bir sonraki açılışta yüklenir.
- GEÇ SONUÇ (2026-09-26): Ghost bir turda arka plan görevi bırakıp turu bitirirse,
  görev bitince harness aynı süreçte KENDİ turunu açar ve ikinci bir `result` basar.
  Köprü o sırada boştadır; satır kuyrukta bekler ve bir sonraki mesajın cevabı
  sanılırdı (sahibe 6 cevap 3 mesaj gecikmeli gitti). Artık `bosta_oku()` boşta
  kuyruğu süpürür ve `result` satırlarını `on_gec_sonuc` geri-çağrısına verir;
  `tur()` de prompt yazmadan önce aynı süpürmeyi yapar. Geç sonuç bir cevaptır,
  atılmaz — zamanında gider.
- Durumunu `ops/kalici_oturum.json`a yazar: selfcheck köprünün BOŞTA olup
  olmadığına bakarken bu çocuk süreci meşgul saymasın (bayat kod yenilemesi).
"""
import json
import os
import queue
import signal
import subprocess
import threading
import time

from ghost import config

DURUM_DOSYASI = os.path.join(config.OPS, "kalici_oturum.json")
BOSTA_KAPAT_SN = int(os.environ.get("JARVIS_KALICI_BOSTA", "3600"))
EOF = object()


def _durum_yaz(pid, mesgul, model="", sid=""):
    try:
        tmp = DURUM_DOSYASI + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "mesgul": bool(mesgul), "model": model,
                       "sid": sid, "ts": int(time.time())}, f)
        os.replace(tmp, DURUM_DOSYASI)
    except Exception:
        pass


def durum_oku():
    """selfcheck için: {pid, mesgul, ts} ya da {}."""
    try:
        with open(DURUM_DOSYASI, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class KaliciOturum:
    def __init__(self, claude_bin, ekstra_args=(), cwd=None, env=None, log=None,
                 bosta_sn=BOSTA_KAPAT_SN):
        self.claude_bin = claude_bin
        self.ekstra_args = list(ekstra_args)
        self.cwd = cwd
        self.env = env
        self.log = log or (lambda m: None)
        self.bosta_sn = bosta_sn
        self.proc = None
        self.model = None
        self.sid = None
        self.q = None
        self.err = [""]
        self.kilit = threading.Lock()
        self.mesgul = False
        self.son_kullanim = 0
        self._zamanlayici = None
        self.acilis = 0
        # Boşta gelen `result` satırları (harness devam turu) buraya verilir.
        self.on_gec_sonuc = None

    # ---- yaşam döngüsü ----
    def canli(self):
        return self.proc is not None and self.proc.poll() is None

    def uygun(self, model, sid):
        return self.canli() and self.model == model and self.sid == sid

    def baslat(self, model, sid, resume, mcp_paths=()):
        self.kapat()
        cmd = [self.claude_bin, "-p", "--input-format", "stream-json",
               "--output-format", "stream-json", "--verbose",
               "--model", model] + self.ekstra_args
        if mcp_paths:
            cmd += ["--mcp-config"] + list(mcp_paths)
        if sid:
            cmd += ["--resume", sid] if resume else ["--session-id", sid]
        self.proc = subprocess.Popen(
            cmd, cwd=self.cwd, env=self.env, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True)
        self.model, self.sid = model, sid
        self.q = queue.Queue()
        self.err = [""]
        self.acilis = time.time()
        threading.Thread(target=self._oku, args=(self.proc, self.q), daemon=True).start()
        threading.Thread(target=self._oku_err, args=(self.proc,), daemon=True).start()
        _durum_yaz(self.proc.pid, False, model, sid)
        self.log("kalıcı oturum açıldı pid=%s model=%s %s" % (
            self.proc.pid, model, "resume" if resume else "yeni"))

    def _oku(self, proc, q):
        try:
            for satir in proc.stdout:
                satir = satir.rstrip("\n")
                if satir:
                    q.put(satir)
        except Exception:
            pass
        q.put(EOF)

    def _oku_err(self, proc):
        try:
            self.err[0] = (proc.stderr.read() or "")[-4000:]
        except Exception:
            pass

    def kapat(self, neden=""):
        with self.kilit:
            if self._zamanlayici:
                self._zamanlayici.cancel()
                self._zamanlayici = None
            p, self.proc = self.proc, None
        if p is None:
            return
        try:
            if p.poll() is None:
                try:
                    p.stdin.close()
                except Exception:
                    pass
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                except Exception:
                    p.terminate()
                try:
                    p.wait(timeout=10)
                except Exception:
                    try:
                        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                    except Exception:
                        pass
        finally:
            _durum_yaz(None, False)
            self.log("kalıcı oturum kapandı%s" % ((" — " + neden) if neden else ""))

    def _bosta_zamanla(self):
        if self._zamanlayici:
            self._zamanlayici.cancel()
        if self.bosta_sn <= 0:
            return
        t = threading.Timer(self.bosta_sn, self._bosta_kapat)
        t.daemon = True
        t.start()
        self._zamanlayici = t

    def _bosta_kapat(self):
        if self.mesgul or not self.canli():
            return
        if time.time() - self.son_kullanim >= self.bosta_sn - 1:
            self.kapat("%d dk boşta" % (self.bosta_sn // 60))

    # ---- geç sonuç ----
    def bosta_oku(self):
        """Süreç boştayken kuyrukta birikmiş satırları süpür; `result` olanları
        `on_gec_sonuc`a ver ve listele. Tur sırasında çağrılırsa hiçbir şey yapmaz."""
        if self.mesgul or not self.canli() or self.q is None:
            return []
        sonuclar = []
        while True:
            try:
                satir = self.q.get_nowait()
            except queue.Empty:
                break
            if satir is EOF:
                self.q.put(EOF)          # süreç bitti; tur() görsün
                break
            if '"type":"result"' not in satir.replace(" ", ""):
                continue                 # devam turunun ara olayları — cevap değil
            try:
                if json.loads(satir).get("type") != "result":
                    continue
            except Exception:
                continue
            sonuclar.append(satir)
            if self.on_gec_sonuc:
                try:
                    self.on_gec_sonuc(satir)
                except Exception as e:
                    self.log("geç sonuç geri-çağrısı hatası: %r" % e)
        if sonuclar:
            self.son_kullanim = time.time()
            self.log("kalıcı oturum: %d geç sonuç teslim edildi" % len(sonuclar))
        return sonuclar

    # ---- tur ----
    def tur(self, prompt, limit, on_event=None):
        """(sonuc_satiri|None, kuyruk_metni, stderr, zaman_asimi, oldu).
        oldu=True: süreç sonuç vermeden bitti (çöktü/kapandı)."""
        if not self.canli():
            return None, "", self.err[0], False, True
        self.bosta_oku()          # önceki turdan artan sonuç bu turun cevabı sanılmasın
        self.mesgul = True
        _durum_yaz(self.proc.pid, True, self.model, self.sid)
        kuyruk = []
        sonuc = None
        zaman_asimi = oldu = False
        try:
            # Tur başlangıcı: akış ekranı "başladı" olayını `system/init`ten alır;
            # kalıcı süreçte o satır yalnız açılışta gelir → her tur sentetik bas.
            if on_event:
                try:
                    on_event(json.dumps({"type": "system", "subtype": "init",
                                         "model": self.model, "session_id": self.sid}))
                except Exception:
                    pass
            try:
                self.proc.stdin.write(json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": prompt}}) + "\n")
                self.proc.stdin.flush()
            except Exception:
                return None, "", self.err[0], False, True
            son = time.time() + limit
            while True:
                kalan = son - time.time()
                if kalan <= 0:
                    zaman_asimi = True
                    break
                try:
                    satir = self.q.get(timeout=min(kalan, 5))
                except queue.Empty:
                    continue
                if satir is EOF:
                    oldu = True
                    break
                kuyruk.append(satir)
                del kuyruk[:-40]
                if on_event:
                    try:
                        on_event(satir)
                    except Exception:
                        pass
                if '"type":"result"' in satir.replace(" ", ""):
                    try:
                        if json.loads(satir).get("type") == "result":
                            sonuc = satir
                            break
                    except Exception:
                        pass
        finally:
            self.mesgul = False
            self.son_kullanim = time.time()
        if zaman_asimi or oldu:
            self.kapat("zaman aşımı" if zaman_asimi else "süreç bitti")
        else:
            _durum_yaz(self.proc.pid, False, self.model, self.sid)
            self._bosta_zamanla()
        return sonuc, "\n".join(kuyruk), self.err[0], zaman_asimi, oldu
