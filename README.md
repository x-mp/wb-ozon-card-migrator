# WB → Ozon card migrator

A safe Python workflow for moving a seller-owned Wildberries card to the
seller's own Ozon cabinet through official APIs.

The project reads and archives the WB source card, retrieves the current Ozon
category schema, adapts fields and media, builds a dry-run plan, requires
explicit approval, uploads one card, and polls the asynchronous Ozon task.
Secrets, seller responses, product data, and media are never part of the
repository.

## Safety model

- WB access is read-only.
- Ozon reads and writes are separate commands.
- No seller-portal cookies or browser automation.
- Every API response is archived as a new immutable run with provenance.
- Upload is disabled unless the plan validates, its hash is approved, the
  exact confirmation is provided, and a temporary write guard is enabled.
- The first pilot sends only one product and does not set stock or delete data.

See [architecture](docs/architecture.md), [credential setup](docs/credentials.md),
and the required [brand/VAT/country/TN VED checks](docs/product-fields.md).

## Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[media]"
cp .env.example .env
chmod 600 .env
```

Fill `.env` with a WB Content token and the Ozon Seller Client ID/API key.

## 1. Read one WB card

```bash
PYTHONPATH=src python scripts/wb_cards.py \
  --nm-id WB_NM_ID \
  --account main \
  --output data/marts/wb-card.json
```

The collector uses the official `POST /content/v2/get/cards/list` endpoint,
paginates safely, and records each raw page. It refuses to overwrite the
requested output file.

## 2. Create a canonical local card

```bash
PYTHONPATH=src python scripts/ozon_prepare.py extract-wb \
  --input data/marts/wb-card.json \
  --nm-id WB_NM_ID \
  --output data/marts/canonical.json
```

## 3. Discover the correct Ozon category

```bash
PYTHONPATH=src python scripts/ozon_read.py auth-check

PYTHONPATH=src python scripts/ozon_read.py categories-fetch \
  --output data/marts/ozon-categories.json

PYTHONPATH=src python scripts/ozon_prepare.py categories-suggest \
  --canonical data/marts/canonical.json \
  --tree data/marts/ozon-categories.json \
  --nm-id WB_NM_ID \
  --limit 10 \
  --output data/marts/category-suggestions.json
```

The suggestion is not an authorization to publish. Confirm the exact Ozon
category and type because they determine mandatory attributes.

## 4. Fetch attributes and dictionaries

```bash
PYTHONPATH=src python scripts/ozon_read.py attributes-fetch \
  --category-id CATEGORY_ID \
  --type-id TYPE_ID \
  --output data/marts/attributes.json
```

Review required attributes, especially brand, country, marking, and TN VED.
Use current dictionary IDs; do not invent values.

## 5. Prepare images

```bash
PYTHONPATH=src python scripts/ozon_prepare.py media-prepare \
  --canonical data/marts/canonical.json \
  --nm-id WB_NM_ID \
  --public-base-url https://cdn.example.com/ozon-migration
```

Upload the generated JPEG directory to the matching public URL, then verify
every URL without authentication.

## 6. Add verified overrides

Copy `config/ozon/product-overrides.example.json` to a file under `data/marts/`
and fill in:

- price and the seller's VAT treatment;
- real packaged dimensions and weight;
- brand (`Нет бренда` only when true);
- country shown on the product/packaging;
- mandatory Ozon attribute dictionary values, including TN VED when required;
- compliance review result.

All files under `data/` are ignored by Git.

## 7. Build and inspect a dry-run

```bash
PYTHONPATH=src python scripts/ozon_prepare.py prepare \
  --canonical data/marts/canonical.json \
  --nm-id WB_NM_ID \
  --attributes data/marts/attributes.json \
  --category-id CATEGORY_ID \
  --type-id TYPE_ID \
  --type-name "CONFIRMED TYPE" \
  --media-map data/marts/media/MEDIA_RUN/media-url-map.json \
  --overrides data/marts/overrides.json
```

The command prints the plan location, blockers, warnings, and the exact
approval string. A blocked or manual-review plan cannot be approved.

## 8. Approve and upload one card

```bash
PYTHONPATH=src python scripts/ozon_prepare.py approve \
  --plan data/marts/PLAN_ID/migration-plan.json \
  --confirm "APPROVE:PLAN_ID:HASH_PREFIX"
```

Switch to the dedicated Ozon product-writer key and run `auth-check`. Only for
the approved command:

```bash
export OZON_ENABLE_WRITES=UPLOAD_APPROVED_PLANS

PYTHONPATH=src python scripts/ozon_upload.py upload \
  --plan data/marts/PLAN_ID/migration-plan.json \
  --approval data/marts/PLAN_ID/approval.json \
  --confirm "UPLOAD:PLAN_ID:HASH_PREFIX"

unset OZON_ENABLE_WRITES
```

Poll the task and inspect moderation:

```bash
PYTHONPATH=src python scripts/ozon_read.py poll --task-id TASK_ID
PYTHONPATH=src python scripts/ozon_read.py product-check --offer-id OFFER_ID
```

## Validation

```bash
python -m unittest discover -s tests -v
```

The repository is public source code but no license to copy, modify, or
redistribute is granted until the owner adds an explicit license.
