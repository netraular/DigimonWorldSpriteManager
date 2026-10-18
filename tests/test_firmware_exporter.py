import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.firmware_exporter import _portrait, export_assets
from core.layout import ALL_DIRS


class FirmwareExporterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.destination = self.root / "export"
        sheet = Image.new("RGBA", (12, 10))
        for row in range(5):
            for col in range(3):
                for y in range(2):
                    for x in range(4):
                        sheet.putpixel((col * 4 + x, row * 2 + y),
                                       (row * 40, col * 60, x * 30 + y, 255))
        sheet.save(self.source / "001.png")
        rows = {"down": 0, "left": 2, "right": 1, "up": 3,
                "down_left": 0, "down_right": 1, "up_left": 2, "up_right": 3}
        self.layout = {
            "style": "explicit", "cols": 3, "rows": 5, "tick_ms": 33,
            "walk_style": "stride", "walk_durations": [6, 6, 6],
            "walk": {d: [{"col": c, "row": rows[d]} for c in range(3)]
                     for d in ALL_DIRS},
            "idle": {d: [{"col": 0, "row": 4}] for d in ALL_DIRS},
        }
        self.write_layout()
        (self.source / "pet_001.json").write_text(json.dumps({
            "id": 1, "name": "Test creature", "type": "Data",
            "stage": 1, "evolutions": [],
        }))

    def write_layout(self):
        (self.source / "001.json").write_text(json.dumps(self.layout))

    def test_export_preserves_frames_timing_and_source(self):
        original = (self.source / "001.json").read_bytes()
        manifest = export_assets(self.source, self.destination)
        graphics = self.destination / "services/pet/assets/graphics/species/digimon"
        layout = json.loads((graphics / "1001.json").read_text())
        self.assertEqual(layout["sheet_split"], "clips")
        self.assertEqual(layout["walk_durations"], [6, 6, 6])
        self.assertEqual(layout["walk"], self.layout["walk"])
        self.assertEqual(layout["idle"], self.layout["idle"])
        self.assertEqual(manifest["creatures"][0]["asset_id"], 1001)
        self.assertEqual((self.source / "001.json").read_bytes(), original)
        self.assertEqual((graphics / "1001.png").read_bytes(),
                         (self.source / "001.png").read_bytes())
        with Image.open(self.source / "001.png") as source:
            for clip in ("walk", "idle"):
                for cells in layout[clip].values():
                    for cell in cells:
                        row, col = cell["row"], cell["col"]
                        with Image.open(graphics / "1001" / f"{layout['clip_files'][row]}.png") as strip:
                            expected = source.crop((col * 4, row * 2,
                                                    (col + 1) * 4, (row + 1) * 2))
                            actual = strip.crop((col * 4, 0, (col + 1) * 4, 2))
                            self.assertEqual(actual.tobytes(), expected.tobytes())
        with Image.open(graphics / "1001/idle.png") as idle:
            self.assertEqual(idle.size, (4, 2))
        for kind, size in (("small", 32), ("full", 64)):
            path = self.destination / f"services/creatures/assets/graphics/portraits/{kind}/1001.png"
            with Image.open(path) as portrait:
                self.assertEqual(portrait.size, (size, size))
                self.assertEqual(portrait.getchannel("A").getbbox()[3], size)

    def test_existing_destination_is_not_overwritten(self):
        self.destination.mkdir()
        marker = self.destination / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(FileExistsError):
            export_assets(self.source, self.destination)
        self.assertEqual(marker.read_text(), "keep")

    def test_portrait_alignment_after_sparse_feet_are_downsampled(self):
        frame = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        frame.paste((255, 0, 0, 255), (0, 0, 64, 63))
        frame.putpixel((0, 63), (0, 255, 0, 255))
        portrait = _portrait(frame, 32)
        self.assertEqual(portrait.getchannel("A").getbbox()[3], 32)

    def test_colliding_ids_rejected_before_writing(self):
        with self.assertRaisesRegex(ValueError, "collides"):
            export_assets(self.source, self.destination, id_offset=0)
        self.assertFalse(self.destination.exists())

    def test_invalid_cell_rejected_before_writing(self):
        self.layout["walk"]["down"][0]["row"] = 5
        self.write_layout()
        with self.assertRaisesRegex(ValueError, "outside grid"):
            export_assets(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_excess_frames_rejected_before_writing(self):
        self.layout["idle"]["down"] *= 5
        self.write_layout()
        with self.assertRaisesRegex(ValueError, "exceeds 4"):
            export_assets(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_sleep_is_preserved_when_present(self):
        self.layout["sleep"] = [{"col": 0, "row": 4}]
        self.layout["sleep_durations"] = [10]
        self.write_layout()
        export_assets(self.source, self.destination)
        path = self.destination / "services/pet/assets/graphics/species/digimon/1001.json"
        layout = json.loads(path.read_text())
        self.assertEqual(layout["sleep"], self.layout["sleep"])
        self.assertEqual(layout["sleep_durations"], [10])

    def test_legacy_shared_layout_is_explicitly_opt_in(self):
        manifest = export_assets(self.source, self.destination, legacy_shared=True)
        self.assertEqual(manifest["runtime_root"], "shared")
        self.assertTrue((self.destination / "shared/services/pet/assets/graphics/species/digimon/1001.json").is_file())
        self.assertFalse((self.destination / "services").exists())


if __name__ == "__main__":
    unittest.main()
