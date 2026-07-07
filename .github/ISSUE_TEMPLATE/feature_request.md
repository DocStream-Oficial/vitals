---
name: Feature request
about: Suggest an idea, integration, or improvement
title: "[Feature] "
labels: enhancement
assignees: ""
---

## What problem does this solve?

A clear description of the problem or gap. ("I'm always frustrated when...")

## Proposed solution

What you'd like to see happen. If it touches scoring (`app/scoring.py`,
`app/bodyage.py`) or another algorithm, see `docs/ALGORITHMS.md` first — changes
there need a regenerated golden fixture and regression tests.

## Alternatives considered

Any other approaches you thought about, and why you prefer this one.

## Scope check

- [ ] This fits the self-hosted, single/household-user model (not a
      multi-tenant SaaS feature)
- [ ] This doesn't require a new mandatory runtime dependency (or I've noted
      why it's worth the trade-off)
- [ ] If this is a new UI string, I understand it needs all 4 locales
      (ES/EN/FR/PT) — see `CONTRIBUTING.md#i18n-audit-gates`

## Additional context

Mockups, links to similar features in other apps, related issues, etc.
