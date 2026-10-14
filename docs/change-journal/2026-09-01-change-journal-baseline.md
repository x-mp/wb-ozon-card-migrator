# Adopt the mandatory project change journal

## Date

2026-09-01

## Why

- The repository had a curated changelog and commit-message contract but no
  dated record explaining the reasoning and implementation path behind changes.

## What

- Added the mandatory Markdown change journal for approved WB to Ozon migration plans.

## How

- The journal uses one dated, slugged entry per substantive change and keeps
  engineering intent separate from the release-oriented `CHANGELOG.md`.

## Verification

- The central Marketplace Platform documentation auditor validates the filename
  and required `Date/Why/What/How/Verification/State` headings.

## State

- The journal contract is active for future substantive changes.
- Existing historical commits are not reconstructed or rewritten.

## References

- Task: `TASK-78`; parent: `TASK-61`.
- Plan: `plan-memory-structure-change-journal-20260901`.
