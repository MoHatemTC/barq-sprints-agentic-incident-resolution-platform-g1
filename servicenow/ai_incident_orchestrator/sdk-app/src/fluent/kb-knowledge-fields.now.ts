import { StringColumn, Table } from '@servicenow/sdk/core'

/**
 * S1.4 scoped columns added to the global Knowledge (kb_knowledge) table.
 *
 * Declared the same way S1.1 augments Incident, so the columns ship with the app. The
 * application prefix keeps them out of the Global scope. Names and lengths must match
 * servicenow/README.md and src/app/publishing/payload.py; see
 * tests/repo/test_kb_fields_match_publisher.py.
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
