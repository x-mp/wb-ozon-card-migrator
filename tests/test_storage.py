import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wb_assistant.storage import ArtifactStore


class StorageTests(unittest.TestCase):
    def test_artifact_is_immutable_and_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ArtifactStore(Path(temp_dir) / "data")
            artifact = store.save(
                source="seller_api",
                entity="seller",
                request_url="https://example.test/seller",
                request_params={},
                body=b'{"ok":true}',
                content_type="application/json",
                http_status=200,
                requested_at=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
            )
            data_path = store.root / artifact.data_file
            manifest = json.loads((data_path.parent / "manifest.json").read_text())
            self.assertEqual(data_path.read_bytes(), b'{"ok":true}')
            self.assertEqual(manifest["sha256"], artifact.sha256)
            self.assertEqual(store.recent(1)[0]["run_id"], artifact.run_id)


if __name__ == "__main__":
    unittest.main()
