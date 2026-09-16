import { StringColumn, Table } from '@servicenow/sdk/core'

/**
 * S1.4 scoped columns added to the global Knowledge (kb_knowledge) table.
 *
 * #90: these five columns existed only as a manual step in servicenow/README.md §2.
 * Nothing in the repository created them, so a clean PDI could not run KB publishing
 * until someone recreated them by hand from a table in a README — which contradicts the
 * Sprint 1 definition of done, "the application exports cleanly as an update set".
 *
 * Declared here the same way S1.1 augments the Incident table, so the columns ship with
 * the scoped application instead of being reproduced by hand. Every column name carries
 * the generated application prefix, which is what keeps them out of the Global scope
 * (see tests/repo/test_no_new_global_scope_in_exports.py and #48).
 *
 * Types and max lengths match servicenow/README.md §1 exactly. `src/app/publishing/
 * payload.py` writes these element names, and `ServiceNowProvisioner.ensure_schema`
 * checks they exist before publishing — tests/repo/test_kb_fields_match_publisher.py
 * asserts the three stay in step.
 */
export const kb_knowledge = Table({
    augments: 'kb_knowledge',
    schema: {
        x_2215032_ai_inc_0_source_id: StringColumn({
            label: 'Source ID',
            maxLength: 40,
            active: true,
            hint: 'Deterministic lookup key (for example KB0001-v2.0) that makes publishing idempotent.',
        }),
        x_2215032_ai_inc_0_service: StringColumn({
            label: 'Service',
            maxLength: 50,
            active: true,
            hint: 'Associated BARQ microservice or technical domain.',
        }),
        x_2215032_ai_inc_0_version: StringColumn({
            label: 'Corpus Version',
            maxLength: 20,
            active: true,
            hint: 'Semantic version of the corpus article, for example 1.0 or 2.0.',
        }),
        x_2215032_ai_inc_0_security_level: StringColumn({
            label: 'Security Level',
            maxLength: 50,
            active: true,
            hint: 'Access classification: internal, restricted or public. Retrieval filters on this.',
        }),
        x_2215032_ai_inc_0_article_number: StringColumn({
            label: 'Article Number',
            maxLength: 20,
            active: true,
            hint: 'Canonical BARQ article number, for example KB0001, without the version suffix.',
        }),
    },
})
