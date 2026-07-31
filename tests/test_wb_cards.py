import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from wb_assistant.errors import classify_http_status
from wb_assistant.storage import ArtifactStore
from wb_assistant.wb_cards import WbCardCollector


class _Executor:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def execute(self, **kwargs):
        self.requests.append(kwargs)
        response = self.responses.pop(0)
        return SimpleNamespace(
            url="https://content-api.wildberries.ru/content/v2/get/cards/list?locale=ru",
            http_status=200,
            content_type="application/json",
            body=json.dumps(response).encode(),
            decision=classify_http_status(200),
        )


class WbCardCollectorTests(unittest.TestCase):
    def test_collects_exact_card_and_archives_request(self) -> None:
        executor = _Executor(
            [{"cards": [{"nmID": 123456789, "title": "Card"}], "cursor": {"total": 1}}]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = WbCardCollector(
                executor=executor,
                store=ArtifactStore(Path(directory)),
            ).collect_nm_id(123456789)
            artifact = result.artifacts[0]
            request = json.loads(
                (Path(directory) / artifact.request_body_file).read_text()
            )
        self.assertEqual(result.exact_card["title"], "Card")
        self.assertEqual(
            request["settings"]["filter"]["textSearch"], "123456789"
        )
        self.assertEqual(artifact.method_id, "content.cards.list")

    def test_paginates_without_overwriting_artifacts(self) -> None:
        first_cards = [{"nmID": index + 1} for index in range(100)]
        executor = _Executor(
            [
                {
                    "cards": first_cards,
                    "cursor": {
                        "total": 100,
                        "updatedAt": "2026-07-31T00:00:00Z",
                        "nmID": 100,
                    },
                },
                {"cards": [{"nmID": 123456789}], "cursor": {"total": 1}},
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = WbCardCollector(
                executor=executor,
                store=ArtifactStore(Path(directory)),
            ).collect_nm_id(123456789)
        self.assertEqual(len(result.artifacts), 2)
        self.assertNotEqual(result.artifacts[0].run_id, result.artifacts[1].run_id)
        self.assertEqual(
            executor.requests[1]["body"]["settings"]["cursor"]["nmID"], 100
        )


if __name__ == "__main__":
    unittest.main()
