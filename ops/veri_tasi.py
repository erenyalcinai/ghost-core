#!/usr/bin/env python3
"""Kullanıcı verisi dizinlerini ops/ altından ~/.ghost köküne taşı (Faz 3.1; denetim 03 4c + 6c, 2026-10-30).

NEDEN: repo dizini kod + yapılandırma olsun; sahibin Masa ekleri (masa_dosya), döküm medyası (kokpit_medya),
Share Extension paylaşımları (kokpit_paylasim) ve Chromium profili (browser-profile, 471 MB) repo içinde duruyordu.
Kod artık `config.veri_dizin(ad)` okur (~/.ghost/<ad>). Bu betik BİR KEZ koşar, tekrar koşarsa dokunmaz.

NE YAPAR (her ad için, sırayla):
  1. ops/<ad> gerçek dizinse ve ~/.ghost/<ad> yoksa → shutil.move (aynı disk: anlık rename).
  2. ops/<ad> yerine ~/.ghost/<ad>'a symlink bırakır — DB'deki eski mutlak yollar (kokpit_medya satırları,
     masa_dosya kayıtları, çalışan eski süreçler) kırılmaz. Symlink kalıcıdır (geri taşıma yok).
  3. mcp-playwright.json (gitignore'da, yerel) içindeki --user-data-dir yolunu yeni köke çevirir.
Hiçbir şey silmez. `--kuru` yalnız planı yazar. Çıktı: her satır ne yapıldı / neden atlandı.
"""
import json
import os
import shutil
import sys

from ghost import config


def plan(kok=None, ops=None):
    """[(ad, kaynak, hedef, eylem)] — eylem: tasi | link | tamam | yok | catisma."""
    kok = kok or config.veri_kok()
    ops = ops or os.path.join(config.BASE, "ops")
    out = []
    for ad in config.VERI_DIZINLERI:
        kaynak, hedef = os.path.join(ops, ad), os.path.join(kok, ad)
        if os.path.islink(kaynak):
            out.append((ad, kaynak, hedef, "tamam" if os.path.realpath(kaynak) == os.path.realpath(hedef) else "catisma"))
        elif os.path.isdir(kaynak):
            out.append((ad, kaynak, hedef, "catisma" if os.path.isdir(hedef) and os.listdir(hedef) else "tasi"))
        elif os.path.isdir(hedef):
            out.append((ad, kaynak, hedef, "link"))
        else:
            out.append((ad, kaynak, hedef, "yok"))
    return out


def uygula(kok=None, ops=None, kuru=False, mcp_json=None):
    kok = kok or config.veri_kok()
    satirlar = []
    os.makedirs(kok, exist_ok=True)
    for ad, kaynak, hedef, eylem in plan(kok, ops):
        if eylem == "tamam":
            satirlar.append("= %s zaten ~/.ghost'ta (symlink yerinde)" % ad)
        elif eylem == "catisma":
            satirlar.append("! %s ÇATIŞMA: hem %s hem %s dolu — elle birleştir, dokunmadım" % (ad, kaynak, hedef))
        elif eylem == "yok":
            satirlar.append("- %s yok (ne ops'ta ne ~/.ghost'ta) — kod ilk kullanımda ~/.ghost'ta açar" % ad)
        elif eylem == "tasi":
            if not kuru:
                if os.path.isdir(hedef):      # boş hedef (plan 'tasi' dediyse boştur)
                    os.rmdir(hedef)
                shutil.move(kaynak, hedef)
                os.symlink(hedef, kaynak)
            satirlar.append("→ %s taşındı: %s → %s (+symlink)" % (ad, kaynak, hedef))
        elif eylem == "link":
            if not kuru:
                os.symlink(hedef, kaynak)
            satirlar.append("↪ %s symlink kuruldu: %s → %s" % (ad, kaynak, hedef))
    mcp_json = mcp_json or os.path.join(config.BASE, "mcp-playwright.json")
    satirlar.append(_mcp_duzelt(mcp_json, kok, kuru))
    return satirlar


def _mcp_duzelt(yol, kok, kuru):
    if not os.path.isfile(yol):
        return "- mcp-playwright.json yok, atlandı"
    try:
        with open(yol, encoding="utf-8") as f:
            d = json.load(f)
        args = d["mcpServers"]["playwright"]["args"]
        i = args.index("--user-data-dir")
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        return "! mcp-playwright.json okunamadı/--user-data-dir yok: %r" % e
    yeni = os.path.join(kok, "browser-profile")
    if args[i + 1] == yeni:
        return "= mcp-playwright.json zaten %s" % yeni
    if not kuru:
        args[i + 1] = yeni
        with open(yol, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return "→ mcp-playwright.json --user-data-dir: %s (yeni oturumlar bu profili açar)" % yeni


def main(argv):
    kuru = "--kuru" in argv
    for s in uygula(kuru=kuru):
        print(("[kuru] " if kuru else "") + s)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
