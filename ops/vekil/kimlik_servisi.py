#!/usr/bin/python3 -I
"""Ghost oturum kimliği — vekile "bu TCP bağlantısı gözetimsiz (özerk) bir oturumdan mı?" cevabını verir.

NEDEN VAR (2026-10-28, 2. tur denetim bulgu 2): özerk işler köprüde GHOST_OZERK=1 ile koşar ve kapı geri
alınmaz eylemi araç düzeyinde keser; ama tüm API'ler artık 127.0.0.1:8797 vekilinde ve vekil isteğin hangi
oturumdan geldiğini bilmiyordu. Vekil `ghost-vekil` kullanıcısıdır: `ghost` süreçlerinin /proc/<pid>/fd ve
environ'una bakamaz (bilinçli; vekile ptrace yetkisi vermiyoruz). Bu küçük servis `ghost` kullanıcısıyla koşar
(aynı uid → /proc okuması serbest; Yama ptrace_scope=1 yalnız ATTACH'ı keser), kodu root sahiplidir
(/usr/local/lib/ghost-vekil/), ve yalnız vekile cevap verir (SO_PEERCRED uid).

Karar: bağlantının yerel portu → /proc/net/tcp → soket inode → sahibi süreç → süreç, ata zinciri ve oturum
lideri. Bunlardan birinin BAŞLANGIÇ ortamında GHOST_OZERK=1 (ya da GHOST_KUM=1, sabah kum havuzu) varsa özerk.
Claude süreci köprü tarafından bu ortamla başlatılır; modelin Bash'i atasının /proc/<pid>/environ'unu
değiştiremez. Bilinçli sınır: `env -i` + `setsid` + yetimleşme birlikte zinciri koparabilir — o kalıplar kapıda
özerkte izlenir. Süreç bulunamazsa cevap null (vekil yazmayı reddeder, fail-closed).

Protokol: tek satır JSON {"port": N, "hedef": 8797} → {"ok": true, "ozerk": bool|null, "pid": N, "neden": "..."}.
"""
import json
import os
import pwd
import socket
import socketserver
import struct
import sys

SOKET = "/run/ghost-kimlik/kimlik.sock"
ISARET = (b"GHOST_OZERK=1", b"GHOST_KUM=1")
VEKIL_KULLANICI = "ghost-vekil"


def _hex_port(adres):
    ip, port = adres.split(":")
    return ip, int(port, 16)


def soket_inode(port, hedef=8797, net="/proc/net/tcp"):
    """127.0.0.1:port → 127.0.0.1:hedef bağlantısının (inode, uid) (yoksa (None, None)).
    uid = /proc/net/tcp sahip sütunu: bağlantının istemci ucunu açan sürecin kullanıcısı."""
    try:
        with open(net, encoding="ascii") as f:
            next(f)
            for satir in f:
                p = satir.split()
                if len(p) < 10:
                    continue
                (lip, lport), (rip, rport) = _hex_port(p[1]), _hex_port(p[2])
                if lport == port and rport == hedef and lip == rip == "0100007F":
                    return int(p[9]), int(p[7])
    except OSError:
        pass
    return None, None


def inode_sureci(inode, proc="/proc"):
    hedef = "socket:[%d]" % inode
    for ad in os.listdir(proc):
        if not ad.isdigit():
            continue
        fd = os.path.join(proc, ad, "fd")
        try:
            for f in os.listdir(fd):
                try:
                    if os.readlink(os.path.join(fd, f)) == hedef:
                        return int(ad)
                except OSError:
                    continue
        except OSError:
            continue
    return None


def _stat(pid, proc="/proc"):
    """→ (ppid, sid) ya da None. comm parantez içinde boşluk taşıyabilir: son ')' sonrası bölünür."""
    try:
        with open("%s/%d/stat" % (proc, pid), "rb") as f:
            s = f.read()
        alan = s[s.rindex(b")") + 2:].split()
        return int(alan[1]), int(alan[3])
    except (OSError, ValueError, IndexError):
        return None


def _isaretli(pid, proc="/proc"):
    try:
        with open("%s/%d/environ" % (proc, pid), "rb") as f:
            return any(x in ISARET for x in f.read().split(b"\0"))
    except OSError:
        return False


def ozerk_mi(pid, proc="/proc"):
    """→ (bool, neden). Süreç, ataları (PID 1'e kadar) ve oturum lideri taranır."""
    goruldu, x = set(), pid
    st = _stat(pid, proc)
    lider = st[1] if st else 0
    while x and x > 1 and x not in goruldu and len(goruldu) < 64:
        goruldu.add(x)
        if _isaretli(x, proc):
            return True, "ata %d özerk" % x
        s = _stat(x, proc)
        x = s[0] if s else 0
    if lider > 1 and lider not in goruldu and _isaretli(lider, proc):
        return True, "oturum lideri %d özerk" % lider
    return False, "zincir temiz (%d süreç)" % len(goruldu)


def cevapla(istek, proc="/proc", net="/proc/net/tcp"):
    try:
        port, hedef = int(istek.get("port")), int(istek.get("hedef") or 8797)
    except (TypeError, ValueError):
        return {"ok": False, "hata": "port"}
    inode, sok_uid = soket_inode(port, hedef, net)
    if not inode:
        return {"ok": True, "ozerk": None, "neden": "bağlantı yok"}
    pid = inode_sureci(inode, proc)
    if not pid:
        # İstemci ucunu açan süreç bizim (ghost) /proc'umuzda okunamıyor. İki durum:
        #  (a) soketin sahibi BİZ (ghost uid) isek: süreç var ama okuma yarışı kaçırdı → None (fail-closed).
        #  (b) sahip BAŞKA uid (sshd forward = Mac vekil tüneli, ghost olmayan): yerel ghost saldırganı bu
        #      yola düşemez (kendi soketi hep okunur), tek kaynak Mac tüneli → GÖZETİMLİ (yazma serbest).
        # Mac tüneli artık Unix sokette (vekil UnixSunucu + ssh zinciri); TCP'de kimliği okunamayan süreç
        # gözetimli SAYILMAZ (4. tur: ileride eklenecek bir root/servis süreci otomatik gözetimli olmasın).
        return {"ok": True, "ozerk": None, "neden": "süreç bulunamadı (uid=%s)" % sok_uid}
    oz, neden = ozerk_mi(pid, proc)
    return {"ok": True, "ozerk": oz, "pid": pid, "neden": neden}


class _Isleyici(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            cred = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            uid = struct.unpack("3i", cred)[1]
            if uid not in self.server.izinli:
                cevap = {"ok": False, "hata": "izinsiz"}
            else:
                cevap = cevapla(json.loads(self.rfile.readline(4096) or b"{}"))
        except Exception as e:
            cevap = {"ok": False, "hata": type(e).__name__}
        try:
            self.wfile.write((json.dumps(cevap) + "\n").encode())
        except OSError:
            pass


class Sunucu(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, yol):
        try:
            os.unlink(yol)
        except FileNotFoundError:
            pass
        super().__init__(yol, _Isleyici)
        os.chmod(yol, 0o666)
        self.izinli = {0}
        try:
            self.izinli.add(pwd.getpwnam(VEKIL_KULLANICI).pw_uid)
        except KeyError:
            pass


def main(argv):
    if argv[:1] == ["dene"] and len(argv) > 1:
        print(json.dumps(cevapla({"port": int(argv[1])})))
        return 0
    Sunucu(SOKET).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
