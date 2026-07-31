# Credentials and permissions

Keep all real values in a local `.env`; never commit that file.

## Wildberries

Create a separate token in the seller cabinet's API/integration settings.
Grant only:

- **Content** to read the complete card;
- **Prices and discounts** if the migration must read the current price.

WB is only a source in this project. Do not grant card, price, or stock write
permissions.

```dotenv
WB_API_TOKEN=
WB_API_AUTH_SCHEME=
WB_ACCOUNT_ID=main
```

WB expects the token itself in the `Authorization` header, so the scheme is
normally empty.

## Ozon Seller API

For one seller's own cabinet, use the Seller `Client ID` and an `Api-Key`;
OAuth is unnecessary. Both values must come from the same Ozon Seller cabinet.
The Client ID is the cabinet identifier; the API key is secret.

Create a read-only key first. It must allow role inspection, category trees,
category attributes and dictionaries, product lookup, and import task status.

```dotenv
OZON_CLIENT_ID=
OZON_API_KEY=
OZON_ACCOUNT_ID=main
```

Validate without writing:

```bash
PYTHONPATH=src python scripts/ozon_read.py auth-check
```

For the final upload, create or switch to a separate key that also allows
`POST /v3/product/import`. Run `auth-check` again; `can_import_products` must be
`true`.

OAuth is for an application connected to multiple or third-party seller
companies. An OAuth application `client_id/client_secret` is not the same as a
Seller `Client ID/Api-Key`.

Ozon Performance advertising credentials are also a different API. A client
identifier ending in `@advertising.performance.ozon.ru` together with a
`Client Secret` is used for advertising automation; it cannot authorize Seller
API product import and must not be put into `OZON_CLIENT_ID/OZON_API_KEY`.

## Images

Ozon imports images from public HTTPS JPG/PNG URLs. The project downloads WB
WebP images, verifies them, and converts them to JPEG, but you must place the
converted files in your own public S3/CDN/object storage.
