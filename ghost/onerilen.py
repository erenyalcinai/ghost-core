"""ÖNERİLEN EYLEM standardı — özerk iş çıktısındaki öneri bloğu → onay-komut kartı (Faz 1.6, 2026-10-29).

NEDEN (bütünsel denetim 04 Ö4): nöbetçi/zamanlayıcı/hedef işleri geri alınmaz eylem yapamaz (doğru),
ama bulduğu şeyi "şunu yapmalısın" diye metne gömüyordu; 30 günde hiç karta dönüşmedi, Sahip okuyup
elle istemek zorundaydı. Standart: iş çıktısında

    ÖNERİLEN EYLEM: <kısa başlık>
    komut: <tek satırlık kabuk komutu, cd öneksiz>
    neden: <bir cümle>

bloğu varsa köprü iş bitiminde `sahne.onay_komut` kartı açar (iş başına en çok 2). Sahip onaylayınca
"[Sahne eylem: onay-…] onayla" sohbete düşer ve sohbet oturumu AYNI komutu onay kimliğiyle koşar
(mevcut onay-komut akışı; kapı yine denetler). Kart AÇMAK komutu koşturmaz."""
import re

_BLOK = re.compile(r"ÖNER[İI]LEN EYLEM\s*:\s*(?P<baslik>[^\n]+)\n\s*komut\s*:\s*(?P<komut>[^\n]+)(?:\n\s*neden\s*:\s*(?P<neden>[^\n]+))?",
                   re.IGNORECASE)
KOMUT_TAVAN = 300
IS_BASI_TAVAN = 2
_YASAK = re.compile(r"(^|[\s;&|])(rm\s+-rf\s+/|mkfs|dd\s+if=|:\(\)\s*\{|shutdown|reboot)(?=\s|$)")


def ayikla(metin):
    """[{baslik, komut, neden}] — boş/bozuk/çok uzun komut atlanır."""
    out = []
    for m in _BLOK.finditer(metin or ""):
        komut = m.group("komut").strip().strip("`")
        if not komut or len(komut) > KOMUT_TAVAN or _YASAK.search(komut):
            continue
        out.append({"baslik": m.group("baslik").strip()[:120], "komut": komut,
                    "neden": (m.group("neden") or "").strip()[:300]})
        if len(out) >= IS_BASI_TAVAN:
            break
    return out


def kart_ac(metin, kaynak="", acici=None):
    """Bloklar için onay-komut kartı aç; açılan kart kimlikleri. acici: testler için (baslik, komut, aciklama) → id."""
    oneriler = ayikla(metin)
    if not oneriler:
        return []
    if acici is None:
        from ghost import sahne
        acici = sahne.onay_komut
    ids = []
    for o in oneriler:
        try:
            eid = acici("%s%s" % (("%s · " % kaynak) if kaynak else "", o["baslik"]), o["komut"],
                        o["neden"] or "Özerk iş önerdi; kart komutun kendisini gösterir.")
            if eid:
                ids.append(eid)
        except Exception:
            continue
    return ids
