from __future__ import annotations

import html
import json
import math
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .ozon_schema import OzonOpenApi, canonical_json, sha256_json


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    return re.sub(r"[^0-9a-zа-я]+", " ", text).strip()


def _plain_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_values(item))
        return result
    if isinstance(value, dict):
        for key in ("value", "name", "title"):
            if key in value:
                return _values(value[key])
        return []
    text = _plain_text(value)
    return [text] if text else []


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "" and value != [] and value != {}:
            return value
    return None


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _card_score(card: dict[str, Any]) -> int:
    keys = {
        "nmID": 10,
        "vendorCode": 4,
        "title": 4,
        "description": 8,
        "characteristics": 10,
        "dimensions": 8,
        "photos": 8,
        "mediaFiles": 5,
        "sizes": 5,
        "subjectID": 3,
        "subject": 3,
        "subjectName": 3,
    }
    return sum(weight for key, weight in keys.items() if card.get(key))


def _media_urls(card: dict[str, Any]) -> list[str]:
    result: list[tuple[int, str]] = []
    photos = card.get("photos")
    if isinstance(photos, list):
        for index, photo in enumerate(photos):
            if isinstance(photo, dict):
                value = _first(
                    photo.get("big"),
                    photo.get("c900x1200"),
                    photo.get("c516x688"),
                    photo.get("value"),
                )
            else:
                value = photo
            if isinstance(value, str) and value.startswith("https://"):
                result.append((index, value))
    media = card.get("mediaFiles")
    if isinstance(media, dict):
        media = list(media.values())
    if isinstance(media, list):
        for index, item in enumerate(media):
            value = item.get("value") if isinstance(item, dict) else item
            if isinstance(value, str) and value.startswith("https://"):
                result.append((index, value))
    seen: set[str] = set()
    ordered: list[str] = []
    for _, url in sorted(result):
        clean = url.split("?", 1)[0]
        if clean not in seen:
            seen.add(clean)
            ordered.append(clean)
    return ordered[:30]


def _characteristics(card: dict[str, Any]) -> list[dict[str, Any]]:
    source = card.get("characteristics") or card.get("characteristicsData") or []
    result: list[dict[str, Any]] = []
    if isinstance(source, dict):
        source = [{"name": key, "value": value} for key, value in source.items()]
    if isinstance(source, list):
        for item in source:
            if not isinstance(item, dict):
                continue
            name = _first(item.get("name"), item.get("title"))
            value = _first(item.get("value"), item.get("values"))
            if name and _values(value):
                result.append(
                    {
                        "source_id": _first(item.get("id"), item.get("charcID")),
                        "name": _plain_text(name),
                        "values": _values(value),
                        "unit": _first(item.get("unitName"), item.get("unit")),
                    }
                )
    return result


def extract_wb_products(
    document: dict[str, Any],
    *,
    source_path: Path,
    nm_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    candidates: dict[int, list[dict[str, Any]]] = {}
    for item in _walk(document):
        raw_id = _first(item.get("nmID"), item.get("nmId"))
        try:
            nm_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if not item.get("title") and not item.get("vendorCode"):
            continue
        if nm_ids and nm_id not in nm_ids:
            continue
        candidates.setdefault(nm_id, []).append(item)

    products: list[dict[str, Any]] = []
    source_hash = __import__("hashlib").sha256(source_path.read_bytes()).hexdigest()
    for nm_id, cards in sorted(candidates.items()):
        card = max(cards, key=_card_score)
        all_cards = sorted(cards, key=_card_score, reverse=True)

        def from_cards(key: str) -> Any:
            return _first(*(candidate.get(key) for candidate in all_cards))

        sizes = from_cards("sizes") or []
        barcodes: list[str] = []
        prices: list[float] = []
        if isinstance(sizes, list):
            for size in sizes:
                if not isinstance(size, dict):
                    continue
                for barcode in _values(_first(size.get("skus"), size.get("barcodes"))):
                    if barcode not in barcodes:
                        barcodes.append(barcode)
                price = _first(size.get("currentPrice"), size.get("price"))
                try:
                    if price is not None:
                        prices.append(float(price))
                except (TypeError, ValueError):
                    pass

        dimensions = from_cards("dimensions") or {}
        if not isinstance(dimensions, dict):
            dimensions = {}
        title = _plain_text(from_cards("title"))
        vendor_code = _plain_text(from_cards("vendorCode")) or f"WB-{nm_id}"
        offer_id = re.sub(r"\s+", "-", vendor_code.strip())[:50] or f"WB-{nm_id}"
        characteristics = _characteristics(card)
        if not characteristics:
            for candidate in all_cards:
                characteristics = _characteristics(candidate)
                if characteristics:
                    break
        colors = _values(from_cards("colors"))
        if colors:
            characteristics.append(
                {"source_id": None, "name": "Цвет", "values": colors, "unit": None}
            )
        brand = _plain_text(_first(from_cards("brand"), from_cards("brandRaw")))
        if brand:
            characteristics.append(
                {"source_id": None, "name": "Бренд", "values": [brand], "unit": None}
            )

        products.append(
            {
                "schema_version": 1,
                "product_uid": f"wb:{nm_id}",
                "source": {
                    "marketplace": "wildberries",
                    "external_id": nm_id,
                    "imt_id": from_cards("imtID"),
                    "source_file": str(source_path),
                    "source_sha256": source_hash,
                },
                "identity": {
                    "offer_id": offer_id,
                    "vendor_code": vendor_code,
                    "barcodes": barcodes,
                    "brand": brand or None,
                },
                "content": {
                    "name": title,
                    "description": _plain_text(from_cards("description")),
                    "source_category": _plain_text(
                        _first(from_cards("subjectName"), from_cards("subject"))
                    ),
                    "source_category_id": _first(
                        from_cards("subjectID"), from_cards("subjectId")
                    ),
                    "attributes": characteristics,
                },
                "physical": {
                    "length": _first(dimensions.get("length"), from_cards("length")),
                    "width": _first(dimensions.get("width"), from_cards("width")),
                    "height": _first(dimensions.get("height"), from_cards("height")),
                    "weight": _first(
                        dimensions.get("weightBrutto"),
                        dimensions.get("weight"),
                        from_cards("weightBrutto"),
                    ),
                    "dimension_unit": "cm",
                    "weight_unit": "kg",
                },
                "media": [
                    {
                        "kind": "image",
                        "position": index,
                        "source_url": url,
                    }
                    for index, url in enumerate(_media_urls(card))
                ],
                "commercial": {
                    "currency": "RUB",
                    "price": min(prices) if prices else None,
                    "vat": None,
                },
                "compliance": {
                    "manual_review": bool(from_cards("certificate")),
                    "source_certificate": from_cards("certificate"),
                    "need_kiz": from_cards("needKiz"),
                    "kiz_marked": from_cards("kizMarked"),
                    "reasons": [],
                },
            }
        )
    if nm_ids:
        missing = sorted(nm_ids - set(candidates))
        if missing:
            raise ValueError(f"WB cards were not found in the source file: {missing}")
    if not products:
        raise ValueError("No WB product cards were found in the source file")
    return products


def merge_canonical_products(
    product_sets: Iterable[list[dict[str, Any]]],
    *,
    nm_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Merge seller API, public card, and capture-derived canonical records."""

    merged: dict[int, dict[str, Any]] = {}
    for products in product_sets:
        for product in products:
            nm_id = int(product["source"]["external_id"])
            if nm_ids and nm_id not in nm_ids:
                continue
            if nm_id not in merged:
                merged[nm_id] = json.loads(json.dumps(product))
                merged[nm_id]["source"]["source_files"] = [
                    merged[nm_id]["source"].pop("source_file", "")
                ]
                continue
            target = merged[nm_id]
            source_file = product["source"].get("source_file")
            if source_file and source_file not in target["source"]["source_files"]:
                target["source"]["source_files"].append(source_file)
            for section in ("identity", "content", "physical", "commercial", "compliance"):
                for key, value in (product.get(section) or {}).items():
                    current = target.setdefault(section, {}).get(key)
                    if current in (None, "", [], {}):
                        target[section][key] = value
            existing_attributes = {
                (_norm(item.get("name")), tuple(_values(item.get("values"))))
                for item in target["content"].get("attributes") or []
            }
            for attribute in product["content"].get("attributes") or []:
                key = (_norm(attribute.get("name")), tuple(_values(attribute.get("values"))))
                if key not in existing_attributes:
                    target["content"].setdefault("attributes", []).append(attribute)
                    existing_attributes.add(key)
            existing_media = {
                str(item.get("source_url")) for item in target.get("media") or []
            }
            for media in product.get("media") or []:
                if str(media.get("source_url")) not in existing_media:
                    target.setdefault("media", []).append(media)
                    existing_media.add(str(media.get("source_url")))
    if nm_ids:
        missing = sorted(nm_ids - set(merged))
        if missing:
            raise ValueError(f"WB cards were not found in any source file: {missing}")
    return [merged[nm_id] for nm_id in sorted(merged)]


def flatten_ozon_categories(tree_response: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def visit(
        item: dict[str, Any],
        parents: list[str],
        inherited_category_id: int | None = None,
        inherited_category_name: str = "",
    ) -> None:
        name = _plain_text(item.get("category_name"))
        path = [*parents, name] if name else parents
        own_category_id = item.get("description_category_id")
        category_id = own_category_id or inherited_category_id
        category_name = name if own_category_id else inherited_category_name
        type_id = item.get("type_id")
        type_name = _plain_text(item.get("type_name"))
        if category_id and type_id and not item.get("disabled"):
            result.append(
                {
                    "description_category_id": int(category_id),
                    "type_id": int(type_id),
                    "category_name": category_name,
                    "type_name": type_name,
                    "category_path": path,
                    "search_text": " ".join([*path, type_name]).strip(),
                }
            )
        for child in item.get("children") or []:
            if isinstance(child, dict):
                visit(
                    child,
                    path,
                    int(category_id) if category_id else None,
                    category_name,
                )

    for root in tree_response.get("result") or []:
        if isinstance(root, dict):
            visit(root, [])
    return result


def suggest_ozon_categories(
    product: dict[str, Any],
    categories: list[dict[str, Any]],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    source = " ".join(
        [
            str(product.get("content", {}).get("source_category") or ""),
            str(product.get("content", {}).get("name") or ""),
        ]
    )
    source_norm = _norm(source)
    source_tokens = set(source_norm.split())
    scored: list[tuple[float, dict[str, Any]]] = []
    for category in categories:
        target = _norm(category.get("search_text"))
        target_tokens = set(target.split())
        overlap = len(source_tokens & target_tokens) / max(1, len(source_tokens | target_tokens))
        sequence = SequenceMatcher(None, source_norm, target).ratio()
        score = round(overlap * 0.65 + sequence * 0.35, 6)
        scored.append((score, category))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{**category, "score": score} for score, category in scored[:limit]]


class OzonSeoAdapter:
    def __init__(self, policy: dict[str, Any]) -> None:
        self.policy = policy
        self.forbidden = [
            re.compile(rf"(?iu)\b{re.escape(phrase)}\b")
            for phrase in policy.get("forbidden_phrases") or []
        ]

    def clean(self, text: str) -> tuple[str, list[str]]:
        value = _plain_text(text)
        removed: list[str] = []
        value = re.sub(r"https?://\S+|www\.\S+", " ", value, flags=re.I)
        value = re.sub(r"\b(?:тел|телефон|whatsapp|telegram)\b[^.;,\n]*", " ", value, flags=re.I)
        drop_phrases = [
            _norm(item)
            for item in self.policy.get("drop_sentences_containing") or []
            if _norm(item)
        ]
        if drop_phrases:
            kept: list[str] = []
            for sentence in re.split(r"(?<=[.!?])\s+", value):
                normalized = _norm(sentence)
                matched = [phrase for phrase in drop_phrases if phrase in normalized]
                if matched:
                    removed.extend(f"sentence:{phrase}" for phrase in matched)
                else:
                    kept.append(sentence)
            value = " ".join(kept)
        for pattern in self.forbidden:
            if pattern.search(value):
                removed.append(pattern.pattern)
                value = pattern.sub(" ", value)
        value = re.sub(r"\s+([,.;:])", r"\1", value)
        value = re.sub(r"([!?.,])\1+", r"\1", value)
        return re.sub(r"\s+", " ", value).strip(" ,.;-"), removed

    def adapt(self, product: dict[str, Any], target_type_name: str) -> dict[str, Any]:
        original_name = str(product["content"].get("name") or "")
        clean_name, removed_name = self.clean(original_name)
        brand = _plain_text(product["identity"].get("brand"))
        if _norm(brand) in {"нет бренда", "без бренда", "no brand"}:
            brand = ""
        if target_type_name and _norm(target_type_name) not in _norm(clean_name):
            # Add the marketplace product type only when it is not already
            # semantically present. All other words come from the source.
            clean_name = f"{target_type_name} {clean_name}".strip()
        if brand and _norm(brand) not in _norm(clean_name):
            clean_name = f"{brand} {clean_name}".strip()
        max_title = int(self.policy.get("title_max_length", 200))
        if len(clean_name) > max_title:
            clean_name = clean_name[:max_title].rsplit(" ", 1)[0].rstrip(" ,.;-")

        original_description = str(product["content"].get("description") or "")
        clean_description, removed_description = self.clean(original_description)
        attribute_lines: list[str] = []
        excluded = {
            _norm(item)
            for item in self.policy.get("description_excluded_attributes") or []
        }
        for attribute in product["content"].get("attributes") or []:
            name = _plain_text(attribute.get("name"))
            values = ", ".join(_values(attribute.get("values")))
            if name and values and _norm(name) not in excluded:
                attribute_lines.append(f"— {name}: {values}.")
        if not clean_description:
            clean_description = clean_name + "."
        if attribute_lines:
            clean_description = (
                clean_description.rstrip(".")
                + ".\n\nХарактеристики:\n"
                + "\n".join(attribute_lines[:15])
            )
        max_description = int(self.policy.get("description_max_length", 6000))
        clean_description = clean_description[:max_description].rstrip()

        tokens = re.findall(r"[0-9A-Za-zА-Яа-яЁё-]{3,}", f"{clean_name} {clean_description}")
        stop = {
            "для",
            "или",
            "при",
            "это",
            "как",
            "товар",
            "характеристики",
            "изделие",
        }
        keywords: list[str] = []
        for token in tokens:
            normalized = _norm(token)
            if normalized not in stop and normalized not in keywords:
                keywords.append(normalized)
        keywords = keywords[: int(self.policy.get("keyword_limit", 12))]
        return {
            "name": clean_name,
            "description": clean_description,
            "keywords": keywords,
            "report": {
                "source_name": original_name,
                "removed_patterns": sorted(set(removed_name + removed_description)),
                "invented_claims": False,
            },
        }


def _source_attribute_map(
    product: dict[str, Any], seo: dict[str, Any]
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for attribute in product["content"].get("attributes") or []:
        key = _norm(attribute.get("name"))
        if key:
            result[key] = _values(attribute.get("values"))
    virtual = {
        "бренд": _values(product["identity"].get("brand")),
        "описание": _values(seo.get("description")),
        "аннотация": _values(seo.get("description")),
        "модель": _values(product["identity"].get("vendor_code")),
        "артикул продавца": _values(product["identity"].get("vendor_code")),
        "код продавца": _values(product["identity"].get("vendor_code")),
        "нужен код маркировки": (
            ["true"]
            if product.get("compliance", {}).get("need_kiz") is True
            else ["false"]
            if product.get("compliance", {}).get("need_kiz") is False
            else []
        ),
    }
    for key, value in virtual.items():
        if value:
            # Canonical identity and explicit overrides are authoritative over
            # duplicated source characteristics such as the WB brand field.
            result[key] = value
    return result


def _find_source_values(
    target_name: str,
    source: dict[str, list[str]],
    aliases: dict[str, list[str]],
) -> tuple[list[str], str | None, float]:
    target = _norm(target_name)
    candidate_names = [target, *[_norm(item) for item in aliases.get(target, [])]]
    for name in candidate_names:
        if name in source:
            return source[name], name, 1.0
    best_name = None
    best_score = 0.0
    for source_name in source:
        score = max(
            SequenceMatcher(None, candidate, source_name).ratio()
            for candidate in candidate_names
        )
        if score > best_score:
            best_name, best_score = source_name, score
    if best_name and best_score >= 0.88:
        return source[best_name], best_name, best_score
    return [], None, best_score


def _dictionary_value(
    value: str, dictionary: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, float]:
    target = _norm(value)
    exact = [item for item in dictionary if _norm(item.get("value")) == target]
    if exact:
        return exact[0], 1.0
    best = None
    best_score = 0.0
    for item in dictionary:
        score = SequenceMatcher(None, target, _norm(item.get("value"))).ratio()
        if score > best_score:
            best, best_score = item, score
    return (best, best_score) if best_score >= 0.94 else (None, best_score)


def map_ozon_attributes(
    product: dict[str, Any],
    attribute_snapshot: dict[str, Any],
    seo: dict[str, Any],
    alias_config: dict[str, Any],
    overrides: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
    source = _source_attribute_map(product, seo)
    aliases = {
        _norm(name): [str(value) for value in values]
        for name, values in (alias_config.get("aliases") or {}).items()
    }
    dictionaries = attribute_snapshot.get("dictionaries") or {}
    overrides = overrides or {}
    mapped: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    for target in attribute_snapshot.get("attributes") or []:
        attribute_id = int(target.get("id") or 0)
        if not attribute_id:
            continue
        required = bool(target.get("is_required"))
        if attribute_id == 8229:
            # type_id in the root payload has priority over this legacy attribute.
            continue
        override = overrides.get(str(attribute_id))
        if override is not None:
            values = _values(override)
            source_name = "manual_override"
            confidence = 1.0
        else:
            values, source_name, confidence = _find_source_values(
                str(target.get("name") or ""), source, aliases
            )
        if not values:
            message = f"attribute {attribute_id} ({target.get('name')}): no source value"
            (errors if required else warnings).append(message)
            report.append(
                {
                    "attribute_id": attribute_id,
                    "target_name": target.get("name"),
                    "required": required,
                    "status": "missing",
                    "confidence": confidence,
                }
            )
            continue

        dictionary = dictionaries.get(str(attribute_id)) or []
        mapped_values: list[dict[str, Any]] = []
        dictionary_confidence = 1.0
        for value in values[: int(target.get("max_value_count") or len(values) or 1)]:
            if dictionary:
                match, score = _dictionary_value(value, dictionary)
                dictionary_confidence = min(dictionary_confidence, score)
                if not match:
                    message = (
                        f"attribute {attribute_id} ({target.get('name')}): "
                        f"dictionary value not found for {value!r}"
                    )
                    (errors if required else warnings).append(message)
                    continue
                mapped_values.append(
                    {
                        "dictionary_value_id": int(match["id"]),
                        "value": str(match.get("value") or value),
                    }
                )
            else:
                mapped_values.append({"value": value})
        if not mapped_values:
            continue
        mapped.append(
            {
                "complex_id": int(target.get("attribute_complex_id") or 0),
                "id": attribute_id,
                "values": mapped_values,
            }
        )
        report.append(
            {
                "attribute_id": attribute_id,
                "target_name": target.get("name"),
                "source_name": source_name,
                "required": required,
                "status": "mapped",
                "confidence": min(confidence, dictionary_confidence),
            }
        )
    return mapped, report, errors, warnings


def _positive_int(value: Any, multiplier: float = 1.0) -> int | None:
    try:
        number = float(value) * multiplier
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return max(1, int(math.ceil(number)))


def _load_media_map(document: dict[str, Any] | None, nm_id: int) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in (document or {}).get("items") or []:
        if item.get("wb_nm_id") == nm_id and item.get("source_url") and item.get(
            "ozon_public_url"
        ):
            result[str(item["source_url"]).split("?", 1)[0]] = str(
                item["ozon_public_url"]
            )
    return result


def _ozon_media_urls(
    product: dict[str, Any], media_map: dict[str, Any] | None
) -> tuple[list[str], list[str]]:
    nm_id = int(product["source"]["external_id"])
    mapped = _load_media_map(media_map, nm_id)
    urls: list[str] = []
    errors: list[str] = []
    for media in product.get("media") or []:
        if media.get("kind") != "image":
            continue
        source = str(media.get("source_url") or "").split("?", 1)[0]
        target = mapped.get(source, source)
        parsed = urlparse(target)
        if parsed.scheme != "https" or not parsed.netloc:
            errors.append(f"image is not a public HTTPS URL: {source}")
            continue
        if not re.search(r"\.(?:jpe?g|png)$", parsed.path, re.I):
            errors.append(
                f"image must be converted and mapped to JPG/PNG for Ozon: {source}"
            )
            continue
        if target not in urls:
            urls.append(target)
    if not urls:
        errors.append("at least one Ozon-compatible image is required")
    return urls[:30], errors


@dataclass(frozen=True)
class MigrationPlanResult:
    plan: dict[str, Any]
    approval_confirmation: str
    upload_confirmation: str


def build_migration_plan(
    *,
    product: dict[str, Any],
    category_id: int,
    type_id: int,
    type_name: str,
    attribute_snapshot: dict[str, Any],
    schema: OzonOpenApi,
    seo_policy: dict[str, Any],
    alias_config: dict[str, Any],
    media_map: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
    source_account_id: str = "main",
    target_account_id: str = "main",
) -> MigrationPlanResult:
    overrides = overrides or {}
    product = json.loads(json.dumps(product))
    for section in ("identity", "content", "physical", "commercial", "compliance"):
        if isinstance(overrides.get(section), dict):
            product.setdefault(section, {}).update(overrides[section])

    seo = OzonSeoAdapter(seo_policy).adapt(product, type_name)
    attributes, mapping_report, attr_errors, warnings = map_ozon_attributes(
        product,
        attribute_snapshot,
        seo,
        alias_config,
        overrides.get("attribute_values"),
    )
    physical = product["physical"]
    dimension_unit = physical.get("dimension_unit") or "cm"
    if dimension_unit not in {"mm", "cm", "in"}:
        dimension_unit = "cm"
    depth = _positive_int(physical.get("length"))
    width = _positive_int(physical.get("width"))
    height = _positive_int(physical.get("height"))
    weight_unit = physical.get("weight_unit") or "kg"
    weight_multiplier = 1000.0 if weight_unit == "kg" else 1.0
    weight = _positive_int(physical.get("weight"), weight_multiplier)
    normalized_weight_unit = "g" if weight_unit == "kg" else weight_unit
    errors = list(attr_errors)
    for field_name, value in (
        ("depth", depth),
        ("width", width),
        ("height", height),
        ("weight", weight),
    ):
        if not value:
            errors.append(f"{field_name}: real packaged value is required")

    image_urls, image_errors = _ozon_media_urls(product, media_map)
    errors.extend(image_errors)
    price = product["commercial"].get("price")
    try:
        price_text = f"{float(price):.2f}"
        if float(price_text) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        price_text = ""
        errors.append("price: positive value is required by /v3/product/import")
    vat = str(product["commercial"].get("vat") or "")
    if vat not in {"0", "0.05", "0.07", "0.1", "0.2", "0.22"}:
        errors.append("vat: choose one of 0, 0.05, 0.07, 0.1, 0.2, 0.22")
    offer_id = str(product["identity"].get("offer_id") or "")[:50]
    if not offer_id:
        errors.append("offer_id is required")

    item: dict[str, Any] = {
        "attributes": attributes,
        "barcode": (product["identity"].get("barcodes") or [""])[0],
        "currency_code": str(product["commercial"].get("currency") or "RUB"),
        "depth": depth or 0,
        "description_category_id": int(category_id),
        "dimension_unit": dimension_unit,
        "height": height or 0,
        "images": image_urls[1:] if image_urls else [],
        "name": seo["name"],
        "offer_id": offer_id,
        "price": price_text,
        "primary_image": image_urls[0] if image_urls else "",
        "type_id": int(type_id),
        "vat": vat,
        "weight": weight or 0,
        "weight_unit": normalized_weight_unit,
        "width": width or 0,
    }
    if not item["barcode"]:
        item.pop("barcode")
    payload = {"items": [item]}
    errors.extend(schema.validate_request("/v3/product/import", payload))
    payload_hash = sha256_json(payload)
    plan_id = (
        datetime.now(timezone.utc).strftime("ozon-%Y%m%dT%H%M%SZ-")
        + uuid.uuid4().hex[:8]
    )
    validation_status = "blocked" if errors else (
        "manual_review"
        if product.get("compliance", {}).get("manual_review")
        else "passed"
    )
    plan = {
        "schema_version": 1,
        "plan_id": plan_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_account_id": source_account_id,
        "target_account_id": target_account_id,
        "official_api_schema_hash": schema.sha256,
        "official_api_schema_version": schema.version,
        "mode": "dry_run",
        "approved": False,
        "override_provenance": overrides.get("provenance") or {},
        "source_product": product,
        "target": {
            "description_category_id": int(category_id),
            "type_id": int(type_id),
            "type_name": type_name,
        },
        "seo": seo,
        "attribute_mapping": mapping_report,
        "payload": payload,
        "payload_hash": payload_hash,
        "items": [
            {
                "product_uid": product["product_uid"],
                "offer_id": offer_id,
                "operation": "create_or_update",
                "payload_hash": payload_hash,
                "validation_status": validation_status,
                "blocking_errors": sorted(set(errors)),
                "warnings": sorted(set(warnings)),
            }
        ],
    }
    prefix = payload_hash[:12]
    return MigrationPlanResult(
        plan=plan,
        approval_confirmation=f"APPROVE:{plan_id}:{prefix}",
        upload_confirmation=f"UPLOAD:{plan_id}:{prefix}",
    )


def save_plan(result: MigrationPlanResult, data_root: Path) -> Path:
    directory = data_root / "operations" / "migrations" / result.plan["plan_id"]
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "migration-plan.json"
    path.write_bytes(canonical_json(result.plan) + b"\n")
    return path


def approve_plan(plan_path: Path, confirmation: str) -> Path:
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    expected = f"APPROVE:{plan['plan_id']}:{plan['payload_hash'][:12]}"
    if confirmation != expected:
        raise PermissionError(f"Confirmation mismatch; expected {expected}")
    blocked = [
        item
        for item in plan.get("items") or []
        if item.get("validation_status") != "passed"
    ]
    if blocked:
        raise ValueError("Plan is blocked or still requires manual review")
    approval = {
        "schema_version": 1,
        "plan_id": plan["plan_id"],
        "plan_sha256": __import__("hashlib").sha256(plan_bytes).hexdigest(),
        "payload_hash": plan["payload_hash"],
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "confirmation": expected,
    }
    path = plan_path.with_name("approval.json")
    if path.exists():
        raise FileExistsError("Approval already exists; create a new plan instead")
    path.write_bytes(canonical_json(approval) + b"\n")
    return path


def verify_approval(plan_path: Path, approval_path: Path) -> tuple[dict[str, Any], str]:
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    approval = json.loads(approval_path.read_bytes())
    actual_plan_hash = __import__("hashlib").sha256(plan_bytes).hexdigest()
    if approval.get("plan_sha256") != actual_plan_hash:
        raise PermissionError("Migration plan changed after approval")
    if approval.get("payload_hash") != plan.get("payload_hash"):
        raise PermissionError("Approved payload hash does not match the plan")
    expected = f"UPLOAD:{plan['plan_id']}:{plan['payload_hash'][:12]}"
    return plan, expected
