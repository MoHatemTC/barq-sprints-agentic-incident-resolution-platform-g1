# Manual KB manifest — review record

Status: **DRAFT — pending KB owner sign-off.** No manifest decision below is
approved until a named human KB owner records their sign-off on this file
(approver, date, and the reviewed hash). AI review supplements this and is not
recorded as approval. Code and synthetic tests may progress while review is
pending; publishing (plan step 8) and any corpus activation may not.

## Reviewed artifact

- Manifest: `data/corpus/manual_kb_manifest.json`
- Draft under review (SHA-256): `6a210c41ee5841767928e33265ab3b2c944bcd4948368c18deb0fa16605215cb`
- Source PDF: `data/barq-system-kb.pdf`, SHA-256
  `c535243f00547211c2a7076bc5dc91c80d19a4960b94dc7932bbca546db7d9d4` (pinned in
  `data/corpus/manual_source.json` and re-verified by preflight)
- Extraction: `data/corpus/manual_sections.json` (75 sections; coverage contract
  in `data/corpus/manual_coverage.json`)

Re-verify at any time:

```bash
uv run pytest tests/retrieval/manual/test_manifest.py tests/retrieval/manual/test_section_adapter.py
```

Preflight re-checks, on every adapter call, that the manifest still matches the
committed extraction (per-section content hashes) and the corpus (alias targets
at pinned versions).

## Already locked by the KB owner (2026-09-27, before this draft)

These decisions were locked during planning and are implemented, not re-decided:

- **6.13** is the only reviewed exception: the live procedure maps to the
  existing **KB0010 v2.0** (alias); **KB0010 v1.0 remains retired** and excluded
  by the lifecycle filter.
- **6.4–6.12** mirror the existing corpus metadata exactly (KB0001–KB0009 at
  their current corpus versions); no new articles, no metadata copied into the
  manifest.
- **6.1** (article index) aliases the existing articles it indexes — no new
  article, no KB2xxx allocation.
- **6.2** (symptom finder) is one new reference unit — published, internal.
- **6.3** (archived KB0005 scan) is a historical/archival unit that must not
  mirror KB0005's metadata and is excluded from current-procedure evidence.
- Lifecycle precedence order: historical/retired handling first, restricted
  inheritance second, defaults last.

## Decisions requiring sign-off in this draft

1. **Category/service vocabulary.** Seven new categories and eighteen services
   are declared in the manifest's `vocabulary` block (e.g. `service-desk`,
   `knowledge-management`, `automation` / `service-operations`,
   `incident-management`, `ai-assist`). Every new unit's mapping is checked
   against this table by preflight. Confirm the slugs read sensibly in
   ServiceNow's KB UI.
2. **Restricted inheritance calls.** Sections that reproduce restricted article
   content inherit `restricted`: **7.3** (works KB0004), **7.5** (applies
   KB0010 v2), **9.2 / 9.5 / 9.6** (tell the retired KB0010 v1 story) and the
   warning article **KB2065**. Confirm each; a section that only *mentions* an
   article (5.2, 6.1, 8.3, 10.2) was deliberately not marked as deriving from it.
3. **6.3 lifecycle.** The archived scan is published nowhere: it becomes a
   `retired` article (KB2022) with its own category/service — it can never pass
   the mandatory retrieval filter and never substitutes for KB0005 v4.0.
4. **Warning article KB2065** (the "at most one" exception for 6.13). Body is
   strictly sourced from sections 9.2/9.5/9.6 and carries no executable steps:

   > KB0010 version 1.0 is retired. Its first step instructed a restart of the
   > order service application server; during major incident MIR-2026-03 that
   > restart dropped in-flight orders and 47 orders were unrecoverable (see the
   > incident report, section 9.2 of the BARQ Operations Manual).
   >
   > The article was correct when published and dangerous by the time it was
   > applied: the 2025 migration changed the deployment it described, and no
   > review was triggered (section 9.5). Version 1 was retired and version 2.0
   > was published with the restart explicitly ruled out and a change-controlled
   > drain path substituted (MIR-2026-03 action 1).
   >
   > Copies of version 1 outside the knowledge base — saved PDFs, pinned
   > messages, wiki pages — must be replaced with a link to version 2.0 and
   > reported to the knowledge manager (action 6 of the report, still open at
   > the time of writing).

   It is `published/restricted`, `purpose=warning`, derives from KB0010.
   Removing this unit is a one-row manifest deletion if the owner declines it.
5. **Stray section "4"** (page 41, "Scan the text for secrets before writing") is
   a callout the extractor recovered as a numbered section; it is kept as its
   own reference unit (KB2050, `automation/ai-assist`) rather than dropped, so
   the coverage contract stays exhaustive.
6. **Supersession entries are deliberately empty.** Per plan step 6a, stressor
   supersession is filled only after a verified inventory of actual stressor
   records (the extractor module is absent from this checkout, so their KB
   numbers cannot be established from code). No guessed deletions.

## Full unit table (generated from the draft manifest)

| unit_id | source | kind | article | purpose | category/service | state/security | title override |
| --- | --- | --- | --- | --- | --- | --- | --- |
| section-1.1 | 1.1 | new | KB2001 | reference | documentation/operations-manual | published/internal | |
| section-1.2 | 1.2 | new | KB2002 | reference | documentation/operations-manual | published/internal | |
| section-1.3 | 1.3 | new | KB2003 | reference | documentation/operations-manual | published/internal | |
| section-2.1 | 2.1 | new | KB2004 | reference | service-desk/service-operations | published/internal | |
| section-2.2 | 2.2 | new | KB2005 | reference | service-desk/service-operations | published/internal | |
| section-2.3 | 2.3 | new | KB2006 | reference | service-desk/service-operations | published/internal | |
| section-2.4 | 2.4 | new | KB2007 | reference | service-desk/service-operations | published/internal | |
| section-3.1 | 3.1 | new | KB2008 | reference | service-desk/incident-management | published/internal | |
| section-3.2 | 3.2 | new | KB2009 | reference | service-desk/incident-management | published/internal | |
| section-3.3 | 3.3 | new | KB2010 | reference | service-desk/incident-management | published/internal | |
| section-3.4 | 3.4 | new | KB2011 | reference | service-desk/incident-management | published/internal | |
| section-3.5 | 3.5 | new | KB2012 | reference | service-desk/incident-management | published/internal | |
| section-3.6 | 3.6 | new | KB2013 | reference | service-desk/incident-management | published/internal | |
| section-4.1 | 4.1 | new | KB2014 | reference | service-desk/escalation | published/internal | |
| section-4.2 | 4.2 | new | KB2015 | reference | service-desk/escalation | published/internal | |
| section-4.3 | 4.3 | new | KB2016 | reference | service-desk/escalation | published/internal | |
| section-5.1 | 5.1 | new | KB2017 | reference | reference/incident-catalogue | published/internal | |
| section-5.2 | 5.2 | new | KB2018 | reference | reference/incident-catalogue | published/internal | |
| section-5.3 | 5.3 | new | KB2019 | reference | reference/incident-catalogue | published/internal | |
| section-6 | 6 | new | KB2020 | reference | knowledge-management/knowledge-base | published/internal | |
| section-6.1 | 6.1 | index_alias | KB0001–KB0010 | reference | — | — | |
| section-6.2 | 6.2 | new | KB2021 | reference | reference/knowledge-base | published/internal | |
| section-6.3 | 6.3 | new | KB2022 | archival | knowledge-management/knowledge-base | retired/internal | |
| section-6.4 | 6.4 | alias | KB0001 v2.0 | current_procedure | mirrors corpus | mirrors corpus | |
| section-6.5 | 6.5 | alias | KB0002 v3.0 | current_procedure | mirrors corpus | mirrors corpus | |
| section-6.6 | 6.6 | alias | KB0003 v2.0 | current_procedure | mirrors corpus | mirrors corpus | |
| section-6.7 | 6.7 | alias | KB0004 v1.0 | current_procedure | mirrors corpus | mirrors corpus | |
| section-6.8 | 6.8 | alias | KB0005 v4.0 | current_procedure | mirrors corpus | mirrors corpus | |
| section-6.9 | 6.9 | alias | KB0006 v3.0 | current_procedure | mirrors corpus | mirrors corpus | |
| section-6.10 | 6.10 | alias | KB0007 v2.0 | current_procedure | mirrors corpus | mirrors corpus | |
| section-6.11 | 6.11 | alias | KB0008 v1.0 | current_procedure | mirrors corpus | mirrors corpus | |
| section-6.12 | 6.12 | alias | KB0009 v2.0 | current_procedure | mirrors corpus | mirrors corpus | |
| section-6.13 | 6.13 | alias | KB0010 v2.0 | current_procedure | mirrors corpus | mirrors corpus | |
| section-7 | 7 | new | KB2023 | reference | reference/incident-records | published/internal | |
| section-7.1 | 7.1 | new | KB2024 | reference | reference/incident-records | published/internal | |
| section-7.2 | 7.2 | new | KB2025 | reference | reference/incident-records | published/internal | |
| section-7.3 | 7.3 | new | KB2026 | reference | reference/incident-records | published/restricted | |
| section-7.4 | 7.4 | new | KB2027 | reference | reference/incident-records | published/internal | |
| section-7.5 | 7.5 | new | KB2028 | reference | reference/incident-records | published/restricted | |
| section-8.1 | 8.1 | new | KB2029 | reference | reference/problem-management | published/internal | |
| section-8.2 | 8.2 | new | KB2030 | reference | reference/problem-management | published/internal | |
| section-8.3 | 8.3 | new | KB2031 | reference | reference/problem-management | published/internal | |
| section-9 | 9 | new | KB2032 | historical | reporting/major-incidents | published/internal | |
| section-9.1 | 9.1 | new | KB2033 | historical | reporting/major-incidents | published/internal | |
| section-9.2 | 9.2 | new | KB2034 | historical | reporting/major-incidents | published/restricted | |
| section-9.3 | 9.3 | new | KB2035 | historical | reporting/major-incidents | published/internal | |
| section-9.4 | 9.4 | new | KB2036 | historical | reporting/major-incidents | published/internal | |
| section-9.5 | 9.5 | new | KB2037 | historical | reporting/major-incidents | published/restricted | |
| section-9.6 | 9.6 | new | KB2038 | historical | reporting/major-incidents | published/restricted | |
| section-10.1 | 10.1 | new | KB2039 | reference | change-management/change-control | published/internal | |
| section-10.2 | 10.2 | new | KB2040 | reference | change-management/change-control | published/internal | |
| section-10.3 | 10.3 | new | KB2041 | reference | change-management/change-control | published/internal | |
| section-10.4 | 10.4 | new | KB2042 | reference | change-management/change-control | published/internal | |
| section-11 | 11 | new | KB2043 | reference | automation/ai-assist | published/internal | |
| section-11.1 | 11.1 | new | KB2044 | reference | automation/ai-assist | published/internal | |
| section-11.2 | 11.2 | new | KB2045 | reference | automation/ai-assist | published/internal | |
| section-11.3 | 11.3 | new | KB2046 | reference | automation/ai-assist | published/internal | |
| section-11.4 | 11.4 | new | KB2047 | reference | automation/ai-assist | published/internal | |
| section-11.5 | 11.5 | new | KB2048 | reference | automation/ai-assist | published/internal | |
| section-11.6 | 11.6 | new | KB2049 | reference | automation/ai-assist | published/internal | |
| section-4 | 4 | new | KB2050 | reference | automation/ai-assist | published/internal | |
| section-11.7 | 11.7 | new | KB2051 | reference | automation/ai-assist | published/internal | |
| section-11.8 | 11.8 | new | KB2052 | reference | automation/ai-assist | published/internal | |
| section-11.9 | 11.9 | new | KB2053 | reference | automation/ai-assist | published/internal | |
| section-12.1 | 12.1 | new | KB2054 | reference | reporting/service-review | published/internal | |
| section-12.2 | 12.2 | new | KB2055 | reference | reporting/service-review | published/internal | |
| section-12.3 | 12.3 | new | KB2056 | reference | reporting/service-review | published/internal | |
| section-A | A | new | KB2057 | reference | reference/glossary | published/internal | |
| section-B.1 | B.1 | new | KB2058 | reference | reference/templates | published/internal | |
| section-B.2 | B.2 | new | KB2059 | reference | reference/templates | published/internal | |
| section-B.3 | B.3 | new | KB2060 | reference | reference/templates | published/internal | |
| section-B.4 | B.4 | new | KB2061 | reference | knowledge-management/knowledge-capture | published/internal | |
| section-C | C | new | KB2062 | reference | reference/impact-urgency | published/internal | |
| section-D | D | new | KB2063 | reference | reference/directory | published/internal | |
| section-E | E | new | KB2064 | reference | reference/identifier-index | published/internal | |
| warning-kb0010-v1-retirement | grounded: 9.2, 9.5, 9.6 | new | KB2065 | warning | knowledge-management/knowledge-base | published/restricted | KB0010 version 1.0 is retired - use version 2.0 |

Warnings are attached (in provenance, carried into chunking by step 6) to
9.2, 9.5 and 9.6; each states that the text narrates retired instructions and
is not an executable procedure.

## Sign-off

| Field | Value |
| --- | --- |
| Reviewed hash (SHA-256 of `data/corpus/manual_kb_manifest.json`) | _pending_ |
| Decision on vocabulary (item 1) | _pending_ |
| Decision on restricted inheritance (item 2) | _pending_ |
| Decision on 6.3 lifecycle (item 3) | _pending_ |
| Decision on warning article KB2065 (item 4) | _pending_ |
| Decision on stray section 4 (item 5) | _pending_ |
| Approver (name, role) | _pending_ |
| Date | _pending_ |

On approval: set `manifest_version` to `1.0`, clear `review_status`, record the
approved hash here, and commit both in the same change.
