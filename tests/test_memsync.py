import json
import unittest

from tests import common  # noqa: F401
from ghost import entities, memory, memsync


class TestMemsync(unittest.TestCase):
    def test_export_merge_roundtrip(self):
        memory.remember("senkron testi bir", kind="test")
        blob = memsync.dump_json()
        # ikinci kez birleştirmek yeni kayıt eklememeli (dedup)
        added = memsync.merge_json(blob)
        self.assertEqual(added, 0)

    def test_merge_new_rows_v1_format(self):
        # eski makineden düz-liste (v1) yük hâlâ kabul edilmeli
        before = memory.count()
        foreign = '[["2020-01-01 00:00:00", "uzak", "uzak makineden kayıt", null]]'
        added = memsync.merge_json(foreign)
        self.assertEqual(added, 1)
        self.assertEqual(memory.count(), before + 1)
        # tekrar birleştirince artmaz
        self.assertEqual(memsync.merge_json(foreign), 0)

    def test_graph_travels(self):
        # varlık grafiği yükte taşınmalı ve karşı tarafta birleşmeli
        entities.add("SenkronTestVarlık", "proje", "senkron testi")
        entities.link("SenkronTestVarlık", "test-eder", "SenkronTestHedef")
        blob = memsync.dump_json()
        data = json.loads(blob)
        self.assertIn("entities", data)
        names = [e[0] for e in data["entities"]]
        self.assertIn("SenkronTestVarlık", names)
        self.assertIn(["SenkronTestVarlık", "test-eder", "SenkronTestHedef"],
                      [r[:3] for r in data["relations"]])
        # aynı yükü tekrar birleştirmek hiçbir şey değiştirmemeli (dedup)
        self.assertEqual(memsync.merge_json(blob), 0)

    def test_graph_foreign_merge(self):
        # başka makinede doğmuş varlık + ilişki yerelde yaratılmalı
        foreign = json.dumps({
            "memories": [],
            "entities": [["UzakVarlık", "site", "uzaktan geldi",
                          "2020-01-01 00:00:00"]],
            "relations": [["UzakVarlık", "bağlı", "UzakHedef",
                           "2020-01-01 00:00:00"]]})
        added = memsync.merge_json(foreign)
        # UzakVarlık + ilişki + (ilişkinin yarattığı UzakHedef sayılmaz,
        # merge_rows yalnız ents+rels sayar) → en az 2 değişiklik
        self.assertGreaterEqual(added, 2)
        e = entities.get("UzakVarlık")
        self.assertEqual(e["type"], "site")
        self.assertIn(("bağlı", "UzakHedef"), e["out"])
        # tekrar birleştirince değişmez
        self.assertEqual(memsync.merge_json(foreign), 0)

    def test_graph_no_note_clobber(self):
        # dolu yerel not, karşıdan gelen farklı notla EZİLMEMELİ
        entities.add("NotluVarlık", "proje", "yerel doğru not")
        foreign = json.dumps({"memories": [], "relations": [],
                              "entities": [["NotluVarlık", "proje",
                                            "eski bayat not",
                                            "2020-01-01 00:00:00"]]})
        memsync.merge_json(foreign)
        self.assertEqual(entities.get("NotluVarlık")["notes"],
                         "yerel doğru not")
        # ama BOŞ alan doldurulmalı: 'şey' türü ve NULL not gelenle zenginleşir
        entities.add("BoşVarlık")
        foreign2 = json.dumps({"memories": [], "relations": [],
                               "entities": [["BoşVarlık", "cihaz", "dolu not",
                                             "2020-01-01 00:00:00"]]})
        self.assertEqual(memsync.merge_json(foreign2), 1)
        e = entities.get("BoşVarlık")
        self.assertEqual((e["type"], e["notes"]), ("cihaz", "dolu not"))

    def test_bad_json_safe(self):
        self.assertEqual(memsync.merge_json("bozuk{"), 0)
        self.assertEqual(memsync.merge_json(""), 0)

    def test_delete_propagates_not_resurrect(self):
        # silme senkronla yayılmalı, geri gelmemeli (tombstone)
        memory.remember("silinecek kayıt xyz", kind="test")
        blob = memsync.dump_json()  # silinmeden önceki hali (canlı)
        memory.forget("silinecek kayıt xyz")
        self.assertEqual([r for r in memory.recall("xyz")], [])
        # eski (deleted=0) blob'u tekrar birleştirmek kaydı DİRİLTMEMELİ
        memsync.merge_json(blob)
        self.assertEqual([r for r in memory.recall("xyz")], [])

    def test_tombstone_travels(self):
        memory.remember("uzakta silinecek abc", kind="test")
        # karşı makine bunu deleted=1 ile gönderirse yerelde de silinmeli
        c = memory.export_rows()
        target = [list(r) for r in c if "uzakta silinecek abc" in r[2]][0]
        target[4] = 1  # deleted işaretle
        import json
        memsync.merge_json(json.dumps([target]))
        self.assertEqual([r for r in memory.recall("abc")], [])


if __name__ == "__main__":
    unittest.main()
