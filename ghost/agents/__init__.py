"""Ajan organizasyonu — departmanlar (VISION.md: 'ajandan kuruma').

departments.yaml'i minimal parser'la okur (stdlib-only; PyYAML gerektirmez —
sınırlı ama bizim şemamıza yeter). route(text) → en uygun departman.
"""
import os
import re

_DIR = os.path.dirname(os.path.abspath(__file__))
_YAML = os.path.join(_DIR, "departments.yaml")


def _parse_departments(path=_YAML):
    """departments.yaml → {anahtar: {ad, anahtar[], persona, araclar[]}}.
    Şemaya özel basit ayrıştırıcı: 2-boşluk girinti, 'anahtar: [..]' listeleri,
    '>' katlanmış bloklar."""
    deps, cur, field = {}, None, None
    buf = []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return {}

    def flush_persona():
        if cur and buf:
            deps[cur]["persona"] = " ".join(x.strip() for x in buf).strip()

    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and line.rstrip().endswith(":"):
            flush_persona()
            buf = []
            cur = line.strip()[:-1]
            deps[cur] = {"ad": cur, "anahtar": [], "persona": "", "araclar": []}
            field = None
            continue
        if cur is None:
            continue
        m = re.match(r"\s+(\w+):\s*(.*)$", line)
        if m and indent == 2:
            flush_persona() if field == "persona" else None
            if field == "persona":
                buf = []
            field = m.group(1)
            val = m.group(2).strip()
            if field in ("anahtar", "araclar"):
                inner = val.strip("[]")
                deps[cur][field] = [x.strip() for x in inner.split(",") if x.strip()]
                field = None
            elif val and val != ">":
                deps[cur][field] = val
                field = None
            continue
        if field == "persona":
            buf.append(line)
    flush_persona()
    return deps


DEPARTMENTS = _parse_departments()


def _matches(kw, text):
    """Kelime sınırı eşleşmesi — 'bug' artık 'bugün' içinde tutmaz.
    Türkçe harfleri de sınır sayar (\\b latin-only olduğu için elle)."""
    return re.search(r"(?<![0-9a-zçğıöşü])%s(?![0-9a-zçğıöşü])"
                     % re.escape(kw), text) is not None


def route(text):
    """(departman_anahtarı, departman_dict) — anahtar kelime skoruyla en iyi
    eşleşme; hiçbiri tutmazsa None (genel işlem)."""
    t = (text or "").lower()
    best, best_score = None, 0
    for key, dep in DEPARTMENTS.items():
        score = sum(1 for kw in dep["anahtar"] if _matches(kw.lower(), t))
        if score > best_score:
            best, best_score = key, score
    return (best, DEPARTMENTS[best]) if best else (None, None)


def system_prefix(text, dep=None):
    """İşe iliştirilecek departman yönergesi (yoksa boş). dep önceden
    route() ile hesaplandıysa geçilir → çift route çağrısı önlenir.

    NOT: araçlar KISIT değil, ÖNCELİK — MCP'nin tamamı zaten yüklü; bu satır
    departmanın hangi araçlara odaklanacağını söyler (yanıltıcı 'erişim' değil)."""
    if dep is None:
        _key, dep = route(text)
    if not dep:
        return ""
    return ("[Ghost %s departmanı olarak çalış. %s Öncelikli araçların: %s]\n\n"
            % (dep["ad"], dep["persona"], ", ".join(dep["araclar"])))
