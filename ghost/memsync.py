"""Paylaşımlı beyin — Mac↔VPS hafıza senkronu.

Sorun: memory (ghost.db) makineye yereldi; Mac'te öğrenilen VPS'te yoktu.
Çözüm: iki yönlü birleştirme. Taşıma mac_worker'ın zaten kurduğu SSH kanalıyla
(Mac→VPS) yapılır: Mac kendi kayıtlarını VPS'e iter, VPS'inkileri çeker.
(ts, text) çiftiyle dedup → çift işlemez, çakışmaz. Append-mostly olduğundan
silme senkronu yok (kasıtlı: kayıt kaybını önler; memories tombstone taşır).

v2 (2026-08-12): yük artık {"memories", "entities", "relations"} sözlüğü —
varlık grafiği de senkronlanır. merge_json eski düz-liste formatını da kabul
eder (deploy sırası uyumsuzluğu kayıt bozmasın).

export_rows/merge_rows saf ve test edilebilir; SSH tarafı mac_worker'da.
"""
import json

from ghost import entities, memory


def dump_json():
    """Yerel hafıza + varlık grafiği JSON string'e (SSH ile taşınır)."""
    ents, rels = entities.export_rows()
    return json.dumps({"memories": memory.export_rows(),
                       "entities": ents, "relations": rels},
                      ensure_ascii=False)


def merge_json(blob):
    """JSON string'ten kayıtları birleştir. Değişen sayı.
    Dict = v2 (memories+graf); düz liste = v1 (yalnız memories)."""
    try:
        data = json.loads(blob) if blob else []
    except ValueError:
        return 0
    if isinstance(data, list):  # v1 — eski makineden gelen yük
        return memory.merge_rows([tuple(r) for r in data])
    if not isinstance(data, dict):
        return 0
    changed = memory.merge_rows([tuple(r) for r in data.get("memories", [])])
    changed += entities.merge_rows(data.get("entities", []),
                                   data.get("relations", []))
    return changed


# Yetenek işareti: mac_worker push'tan önce bunu yoklar. Eski (v1) VPS'e dict
# yük göndermek merge_rows'ta çöp kayıt üretirdi — işaret yoksa push atlanır.
merge_json_v2 = merge_json
