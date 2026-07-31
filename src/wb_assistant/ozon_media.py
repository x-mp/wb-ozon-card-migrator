from __future__ import annotations

import hashlib
import io
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


class OzonMediaPreparer:
    """Download WB images, normalize them to JPEG, and build a public URL map."""

    def __init__(self, data_root: Path, *, timeout: float = 30) -> None:
        self.data_root = data_root
        self.timeout = timeout

    def prepare(
        self,
        product: dict[str, Any],
        *,
        public_base_url: str | None = None,
        jpeg_quality: int = 92,
    ) -> dict[str, Any]:
        try:
            from PIL import Image, ImageOps
        except ImportError as exc:
            raise RuntimeError(
                "Image conversion requires Pillow: pip install -e '.[media]'"
            ) from exc
        nm_id = int(product["source"]["external_id"])
        target = (
            self.data_root
            / "marts"
            / "ozon_migration"
            / "media"
            / str(nm_id)
            / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        )
        target.mkdir(parents=True, exist_ok=False)
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        for media in product.get("media") or []:
            if media.get("kind") != "image":
                continue
            source_url = str(media.get("source_url") or "")
            position = int(media.get("position") or 0) + 1
            try:
                body = self._download(source_url)
                with Image.open(io.BytesIO(body)) as image:
                    image = ImageOps.exif_transpose(image)
                    if image.mode not in {"RGB", "L"}:
                        background = Image.new("RGB", image.size, "white")
                        if "A" in image.getbands():
                            background.paste(image, mask=image.getchannel("A"))
                        else:
                            background.paste(image.convert("RGB"))
                        image = background
                    else:
                        image = image.convert("RGB")
                    output = io.BytesIO()
                    image.save(
                        output,
                        format="JPEG",
                        quality=jpeg_quality,
                        optimize=True,
                        progressive=True,
                    )
                    jpeg = output.getvalue()
                    width, height = image.size
                digest = hashlib.sha256(jpeg).hexdigest()
                filename = f"{position:02d}-{digest[:16]}.jpg"
                path = target / filename
                path.write_bytes(jpeg)
                public_url = None
                if public_base_url:
                    public_url = (
                        public_base_url.rstrip("/")
                        + "/"
                        + quote(str(nm_id))
                        + "/"
                        + quote(target.name)
                        + "/"
                        + quote(filename)
                    )
                items.append(
                    {
                        "wb_nm_id": nm_id,
                        "position": position,
                        "source_url": source_url,
                        "local_file": str(path),
                        "ozon_public_url": public_url,
                        "sha256": digest,
                        "content_type": "image/jpeg",
                        "width": width,
                        "height": height,
                        "size_bytes": len(jpeg),
                    }
                )
            except (OSError, RuntimeError, urllib.error.URLError) as exc:
                errors.append(
                    {
                        "source_url": source_url,
                        "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                    }
                )
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "wb_nm_id": nm_id,
            "public_base_url": public_base_url,
            "items": items,
            "errors": errors,
            "ready_for_ozon": bool(items)
            and not errors
            and all(item["ozon_public_url"] for item in items),
            "note": (
                "Upload this directory to the matching public_base_url. "
                "Ozon must be able to download every URL without authorization."
            ),
        }
        manifest_path = target / "media-url-map.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest["manifest_file"] = str(manifest_path)
        return manifest

    def _download(self, url: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*",
                "Referer": "https://www.wildberries.ru/",
                "User-Agent": os.getenv(
                    "OZON_USER_AGENT", "wb-assistant-ozon-migration/0.1"
                ),
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content_type = response.headers.get_content_type()
                body = response.read(50 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from exc
        if not content_type.startswith("image/"):
            raise RuntimeError(f"Unexpected content type: {content_type}")
        if not body:
            raise RuntimeError("Empty image")
        if len(body) > 50 * 1024 * 1024:
            raise RuntimeError("Image exceeds 50 MiB local safety limit")
        return body
