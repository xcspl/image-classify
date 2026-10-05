"""End-to-end against a fake OpenAI-compatible server: no network, no key.
Run: python3 -m unittest discover -s tests -v"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_okf import model as m  # noqa: E402
from image_okf.pipeline import Options, run  # noqa: E402
from image_okf.search import search  # noqa: E402

REPLIES = {
    "beach.jpg": {"title": "Sunset over a sandy beach", "description": "An orange sunset over the sea and a sandy beach.",
                  "details": "Waves roll in under an orange sky.", "category": "Landscape",
                  "tags": ["Sunset", "Beaches", "sea", "orange sky"], "people_count": 0, "text_in_image": "",
                  "setting": "outdoor", "sensitive": False},
    "receipt.png": {"title": "Grocery receipt", "description": "A printed grocery receipt with a total.",
                    "details": "Line items and a total.", "category": "receipt", "tags": ["receipt", "grocery", "paper"],
                    "people_count": 0, "text_in_image": "FRESH MART TOTAL 42.50", "setting": "indoor",
                    "sensitive": "true", "sensitive_reason": "financial data"},
    "dog.webp": {"title": "Dog on a beach", "description": "A brown dog running on a beach.", "details": "",
                 "category": "animal", "tags": ["dog", "beach", "running"], "people_count": 0, "setting": "outdoor"},
}


class FakeLLM(BaseHTTPRequestHandler):
    calls: list = []
    paths: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLLM.calls.append(body)
        FakeLLM.paths.append(self.path)
        anthropic = self.path.endswith("/messages")
        content = body["messages"][0]["content"]
        if isinstance(content, str):  # the tag-merge pass
            reply = '<think>plural</think>```json\n{"beaches": "beach", "sunset": "sunset"}\n```'
        else:
            text = next(p["text"] for p in content if p["type"] == "text")
            if anthropic:
                assert self.headers["anthropic-version"] and content[0]["source"]["media_type"] == "image/jpeg"
            else:
                assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
            name = next(n for n in REPLIES if f"file name: {n.split('.')[0]}" in text)
            reply = "<think>looking</think>Here you go: " + json.dumps(REPLIES[name])
        if anthropic:
            out = json.dumps({"type": "message", "content": [{"type": "thinking", "thinking": "{\"no\": 1}"},
                                                            {"type": "text", "text": reply}]}).encode()
        else:
            out = json.dumps({"choices": [{"message": {"role": "assistant", "content": reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def make_library(d: Path) -> None:
    (d / "trips" / "goa").mkdir(parents=True)
    (d / "docs").mkdir()
    img = Image.new("RGB", (1600, 900), (250, 120, 30))
    exif = Image.Exif()
    exif[0x0132] = "2024:12:24 18:05:00"  # DateTime
    exif[0x010F], exif[0x0110] = "Canon", "Canon EOS R6"
    img.save(d / "trips" / "goa" / "beach.jpg", exif=exif)
    shutil.copy(d / "trips" / "goa" / "beach.jpg", d / "beach-copy.jpg")  # duplicate content
    Image.new("RGBA", (400, 800), (255, 255, 255, 0)).save(d / "docs" / "receipt.png")
    Image.new("RGB", (300, 300), (120, 80, 40)).save(d / "dog.webp")
    (d / "broken.jpg").write_bytes(b"not an image")


class ImageOkfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeLLM.calls.clear()
        FakeLLM.paths.clear()
        self.tmp = Path(tempfile.mkdtemp())
        make_library(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def opts(self, **kw):
        cfg = m.ModelConfig(base_url=self.base, model="fake-vl", retries=0)
        return Options(root=self.tmp, bundle=self.tmp / "okf", cfg=cfg, workers=2, log=lambda *_: None, **kw)

    def read(self, rel):
        return (self.tmp / "okf" / rel).read_text()

    def test_full_run_and_rerun(self):
        res = run(self.opts())
        self.assertEqual(res["distinct"], 3)  # beach + copy share a hash
        self.assertEqual(res["catalogued"], 3)
        self.assertEqual(res["unreadable"], 1)
        image_calls = [c for c in FakeLLM.calls if not isinstance(c["messages"][0]["content"], str)]
        self.assertEqual(len(image_calls), 3)

        beach = next((self.tmp / "okf" / "landscape").glob("beach-*.md")).read_text()
        self.assertIn("resource: beach-copy.jpg", beach)  # first path alphabetically
        self.assertIn("- trips/goa/beach.jpg", beach)  # the copy is recorded
        self.assertIn("taken_at: '2024-12-24T18:05:00'", beach)
        self.assertIn("camera: Canon EOS R6", beach)
        self.assertIn("\n- beach\n", beach)  # 'Beaches' merged to 'beach' by the merge pass
        self.assertNotIn("beaches", beach)
        self.assertIn("\n- orange-sky\n", beach)

        receipt = next((self.tmp / "okf" / "receipt").glob("receipt-*.md")).read_text()
        self.assertIn("sensitive: true", receipt)
        self.assertIn("FRESH MART TOTAL 42.50", receipt)
        self.assertIn("(<../../docs/receipt.png>)", receipt)  # body link resolves from the doc

        index = self.read("index.md")
        self.assertIn("house_version: '0.3'", index)
        self.assertIn("(landscape/index.md)", index)
        self.assertIn("broken.jpg", self.read("catalog.md"))
        self.assertIn("## beach (2)", self.read("tags.md"))

        # second run: nothing new, no model calls, nothing rewritten
        FakeLLM.calls.clear()
        res2 = run(self.opts())
        self.assertEqual(FakeLLM.calls, [])
        self.assertEqual(res2["docs_written"], 0)

    def test_move_and_delete(self):
        run(self.opts())
        before = sorted(p.name for p in (self.tmp / "okf").rglob("dog-*.md"))
        (self.tmp / "pets").mkdir()
        (self.tmp / "dog.webp").rename(self.tmp / "pets" / "dog.webp")
        (self.tmp / "docs" / "receipt.png").unlink()
        FakeLLM.calls.clear()
        run(self.opts())
        self.assertEqual(FakeLLM.calls, [])  # moved by hash, not re-described
        self.assertEqual(before, sorted(p.name for p in (self.tmp / "okf").rglob("dog-*.md")))
        self.assertIn("resource: pets/dog.webp", next((self.tmp / "okf").rglob("dog-*.md")).read_text())
        self.assertFalse((self.tmp / "okf" / "receipt").exists())  # its doc and folder are gone

    def test_hand_written_notes_survive(self):
        run(self.opts())
        note = self.tmp / "okf" / "about-this-library.md"
        note.write_text("---\ntype: Note\ntitle: About\ndescription: Who took these.\ntimestamp: '2026-10-05'\nstatus: active\n---\n\nHi.\n")
        run(self.opts())
        self.assertTrue(note.exists())
        self.assertIn("[About](about-this-library.md)", self.read("index.md"))

    def test_search(self):
        run(self.opts())
        bundle = self.tmp / "okf"
        hits = search(bundle, "sunset beach")
        self.assertEqual(hits[0].resource, "beach-copy.jpg")
        self.assertEqual({h.resource for h in hits}, {"beach-copy.jpg", "dog.webp"})
        self.assertEqual([h.resource for h in search(bundle, "", tags=["dog"])], ["dog.webp"])
        self.assertEqual([h.resource for h in search(bundle, "fresh mart")], ["docs/receipt.png"])  # visible text
        self.assertEqual([h.resource for h in search(bundle, "", sensitive=True)], ["docs/receipt.png"])

    def test_anthropic_format(self):
        o = self.opts()
        o.cfg.api_format = "anthropic"
        res = run(o)
        self.assertEqual(res["catalogued"], 3)
        self.assertTrue(FakeLLM.paths and all(p == "/v1/messages" for p in FakeLLM.paths))
        self.assertIn("\n- beach\n", next((self.tmp / "okf" / "landscape").glob("beach-*.md")).read_text())

    def test_dry_run_sends_nothing(self):
        res = run(self.opts(dry_run=True))
        self.assertEqual(res["to_describe"], 3)
        self.assertEqual(FakeLLM.calls, [])
        self.assertFalse((self.tmp / "okf").exists())

    def test_validates_against_house_okf_standard(self):
        validator = Path(os.environ.get("OKF_VALIDATE", Path.home() / "local/xyno-okf-guide/okf-validate.py"))
        if not validator.exists():
            self.skipTest("okf-validate.py not found (set OKF_VALIDATE)")
        run(self.opts())
        r = subprocess.run([sys.executable, str(validator), str(self.tmp / "okf")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class UnitTest(unittest.TestCase):
    def test_extract_json(self):
        self.assertEqual(m.extract_json('<think>{"no": 1}</think> ok {"a": "}"} trailing'), {"a": "}"})
        self.assertEqual(m.extract_json('```json\n{"a": {"b": 2}}\n```'), {"a": {"b": 2}})
        with self.assertRaises(ValueError):
            m.extract_json("no json here")

    def test_clean_analysis(self):
        a = m.clean_analysis({"category": "Nope", "tags": ["Street Food", "street food", "a/b"], "people_count": "x",
                              "sensitive": "false"}, m.DEFAULT_CATEGORIES)
        self.assertEqual(a["category"], "other")
        self.assertEqual(a["tags"], ["street-food", "a-b"])
        self.assertEqual(a["people_count"], 0)
        self.assertFalse(a["sensitive"])
        self.assertEqual(a["title"], "Untitled image")


if __name__ == "__main__":
    unittest.main()
