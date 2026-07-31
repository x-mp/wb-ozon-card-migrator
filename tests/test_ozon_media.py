import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from wb_assistant.ozon_media import AutoSupplyMediaUploader


class _FakeUploader(AutoSupplyMediaUploader):
    def _upload(self, filename, content):
        digest = hashlib.sha256(content).hexdigest()
        return {
            "sha256": digest,
            "created": True,
            "public_url": f"https://autosupplybot.test/api/v1/media/public/{digest}.jpg",
        }


class AutoSupplyMediaUploaderTests(unittest.TestCase):
    def test_manifest_is_rewritten_with_gateway_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "01.jpg"
            content = b"synthetic-jpeg"
            image.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            source = root / "media-url-map.json"
            source.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "items": [
                            {
                                "local_file": str(image),
                                "source_url": "https://wb.test/1.webp",
                                "sha256": digest,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "uploaded.json"
            result = _FakeUploader(
                "https://autosupplybot.test/api/v1/media/upload/", "secret"
            ).upload_manifest(source, output)
            self.assertTrue(result["ready_for_ozon"])
            self.assertTrue(result["items"][0]["ozon_public_url"].endswith(".jpg"))
            self.assertNotIn("secret", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
