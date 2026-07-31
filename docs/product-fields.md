# Brand, VAT, country of manufacture, and TN VED

These fields describe different facts and must not be inferred from each
other.

## Brand

Use Ozon's dictionary value `Нет бренда` only when the product and packaging
really carry no brand. A brand present in the source card must not be removed
automatically. Confirm the product, packaging, and the seller's right to use
the brand.

## VAT

VAT belongs to the seller's tax treatment, not to the product's country of
origin. In an override, `commercial.vat` accepts the values currently supported
by the importer:

- `"0"` — without VAT;
- `"0.05"`, `"0.07"`, `"0.1"`, `"0.2"`, or `"0.22"` — applicable rate.

The seller or accountant must confirm the value. Do not copy it from another
cabinet or infer it from the tax regime name alone.

## Country of manufacture

Enter the country printed on the product or its packaging and supported by
supplier/manufacturer documents. A Russian manufacturer can be selected as
`Россия`; this does not remove a category's TN VED requirement.

## TN VED EAEU

TN VED is a ten-digit classification of the product type. It is not a flag
that means “imported” or “exported”. If Ozon marks this category attribute as
required, the value `нет` cannot be substituted.

Use the code from the supplier/manufacturer documents, declaration or
certificate when present. For a mixed kit, classification can depend on the
components, material, purpose, and the component that gives the set its
essential character. If the documents contain no code, obtain a written
classification from the manufacturer, a customs representative, or the
customs authority. The migrator deliberately blocks upload instead of guessing.

Before approving a plan, verify:

1. The product type and Ozon category are correct.
2. The brand value matches the product and packaging.
3. The country of manufacture matches the label and documents.
4. The seller's VAT treatment is confirmed.
5. The exact TN VED value exists in Ozon's current category dictionary.
