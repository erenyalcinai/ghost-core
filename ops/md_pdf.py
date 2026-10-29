#!/usr/bin/env python3
"""Markdown → PDF (Faz 1.13b, 2026-10-29; Sahip "masadaki yazıları telefona kaydedemiyorum").

`markdown` (pip --user) ile HTML, Playwright'ın chrome-headless-shell ikilisiyle PDF; Chromium yoksa HTML döner.
Kullanım: python3 ops/md_pdf.py <giriş.md> <çıkış.pdf> | --masa <kart id> <çıkış.pdf>
API: /v1/masa/<id>/pdf (ghost/api.py) → masa_pdf(id) → (bytes, mime)."""
import glob
import html as _html
import os
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CSS = """body{font-family:-apple-system,'Helvetica Neue',Arial,sans-serif;font-size:12.5pt;line-height:1.45;margin:28px 34px;color:#111}
h1{font-size:20pt;margin:0 0 10px}h2{font-size:15pt;margin:18px 0 6px}h3{font-size:13pt}
table{border-collapse:collapse;margin:8px 0;font-size:10.5pt}th,td{border:1px solid #bbb;padding:3px 7px;text-align:left}
th{background:#f0f0f0}code,pre{font-family:Menlo,monospace;font-size:10pt}pre{background:#f6f6f6;padding:8px;white-space:pre-wrap}
blockquote{border-left:3px solid #ccc;margin:6px 0;padding-left:10px;color:#444}.alt{color:#777;font-size:9.5pt;margin-top:22px}"""


def chrome_yolu():
    adaylar = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell")))
    adaylar += sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")))
    return adaylar[-1] if adaylar else None


def html_uret(metin, baslik="", altbilgi="Ghost · Masa"):
    try:
        import markdown
        govde = markdown.markdown(metin or "", extensions=["tables", "fenced_code", "sane_lists"])
    except Exception:
        govde = "<pre>%s</pre>" % _html.escape(metin or "")
    return ("<!doctype html><html><head><meta charset='utf-8'><title>%s</title><style>%s</style></head><body>%s%s"
            "<p class='alt'>%s</p></body></html>"
            % (_html.escape(baslik or ""), CSS, ("<h1>%s</h1>" % _html.escape(baslik)) if baslik else "", govde,
               _html.escape(altbilgi)))


def pdf_uret(metin, baslik="", chrome=None):
    """(bytes, mime): PDF; Chromium yoksa ya da testte HTML (text/html)."""
    h = html_uret(metin, baslik)
    chrome = chrome or chrome_yolu()
    if not chrome or os.environ.get("GHOST_TEST"):
        return h.encode("utf-8"), "text/html; charset=utf-8"
    with tempfile.TemporaryDirectory(prefix="mdpdf-") as d:
        hy, py = os.path.join(d, "g.html"), os.path.join(d, "g.pdf")
        with open(hy, "w", encoding="utf-8") as f:
            f.write(h)
        r = subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                            "--print-to-pdf=" + py, "file://" + hy], capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and os.path.exists(py):
            with open(py, "rb") as f:
                return f.read(), "application/pdf"
    return h.encode("utf-8"), "text/html; charset=utf-8"


def masa_pdf(kid):
    """Masa kartı → (bytes, mime, dosya_adi) ya da None."""
    from ghost import masa
    k = masa.oku(int(kid), icerik=True)
    if not k:
        return None
    metin = k.get("icerik") or ""
    if k.get("tur") not in ("not", "rapor", "sahne", "baglanti") and not metin:
        return None
    ad = (k.get("ad") or "masa-%s" % kid).strip()
    veri, mime = pdf_uret(metin, ad)
    guvenli = "".join(ch if ch.isalnum() or ch in "-_ ." else "_" for ch in ad)[:60] or "masa"
    return veri, mime, guvenli + (".pdf" if mime.startswith("application/pdf") else ".html")


def main(argv):
    if len(argv) >= 3 and argv[0] == "--masa":
        r = masa_pdf(argv[1])
        if not r:
            print("kart yok/boş"); return 1
        veri, mime, ad = r
        with open(argv[2], "wb") as f:
            f.write(veri)
        print(argv[2], mime, len(veri), "B"); return 0
    if len(argv) >= 2:
        with open(argv[0], encoding="utf-8") as f:
            metin = f.read()
        veri, mime = pdf_uret(metin, os.path.basename(argv[0]))
        with open(argv[1], "wb") as f:
            f.write(veri)
        print(argv[1], mime, len(veri), "B"); return 0
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
