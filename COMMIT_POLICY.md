# Commit policy

Every commit has a specific imperative subject and a body with `Why`, `What`,
`Verification`, and `State`. Describe affected migration stages, plan or schema
changes, fixtures/canary and remaining safety limitations. Messages like `fix`
or `update` without context are forbidden. Never commit credentials, product
payloads, images or private API responses. Update the changelog and recovery
instructions with behavior or approval-flow changes.

Every substantive change also adds a dated
`docs/change-journal/YYYY-MM-DD-short-slug.md` entry with `Date`, `Why`,
`What`, `How`, `Verification` and `State`. The journal records intent,
implementation choices, evidence, limitations and next steps; `CHANGELOG.md`
remains the curated release history. Never copy secrets, private data, full chat
or tool transcripts into the journal.
