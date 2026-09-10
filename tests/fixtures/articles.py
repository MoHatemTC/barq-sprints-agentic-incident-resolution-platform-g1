"""Sample raw article dictionaries for test fixtures.

Contains realistic technical tokens and code blocks that simulate articles
in the knowledge corpus for schema validation, chunking, and embedding tests.
"""

from textwrap import dedent

POSTGRES_V2 = {
    "base_id": "KB-DB-001",
    "version": "2.0",
    "article_id": "KB-DB-001-v2.0",
    "title": "PostgreSQL 16 max_connections Exhausted Under Pooling",
    "short_description": "Resolve FATAL 53300 too many connections on PostgreSQL 16 pools.",
    "category": "database",
    "service": "postgresql",
    "workflow_state": "published",
    "security_level": "internal",
    "content": dedent(
        """\
        ## Symptom

        Application logs show `FATAL: 53300 too many connections for role "svc_app"`
        and new connections are rejected on PostgreSQL 16.

        ## Root Cause

        The `svc_app` role exhausts its per-role connection limit before PgBouncer
        pooling absorbs the burst.

        ## Resolution

        Raise the role limit and verify pooled usage:

        ```sql
        ALTER ROLE svc_app CONNECTION LIMIT 200;
        SELECT count(*) FROM pg_stat_activity WHERE usename = 'svc_app';
        ```

        Reload configuration without restarting:

        ```bash
        psql -U postgres -c 'SELECT pg_reload_conf();'
        ```
        """
    ),
}

POSTGRES_V1 = {
    "base_id": "KB-DB-001",
    "version": "1.0",
    "article_id": "KB-DB-001-v1.0",
    "title": "PostgreSQL 14 max_connections Exhausted",
    "short_description": "Resolve FATAL 53300 too many connections on PostgreSQL 14 instances.",
    "category": "database",
    "service": "postgresql",
    "workflow_state": "published",
    "security_level": "internal",
    "content": dedent(
        """\
        ## Symptom

        PostgreSQL 14 rejects connections with `FATAL: 53300 too many connections`.

        ## Resolution

        Edit `/etc/postgresql/14/main/postgresql.conf` and raise `max_connections`:

        ```bash
        sed -i 's/max_connections = 100/max_connections = 300/' \
            /etc/postgresql/14/main/postgresql.conf
        systemctl restart postgresql@14-main
        ```
        """
    ),
}

REDIS_DRAFT = {
    "base_id": "KB-CACHE-001",
    "version": "1.1",
    "article_id": "KB-CACHE-001-v1.1",
    "title": "Redis Memory Fragmentation Evicts Celery Workers",
    "short_description": "Diagnose Redis fragmentation above 1.5 evicting Celery broker keys.",
    "category": "caching",
    "service": "redis",
    "workflow_state": "draft",
    "security_level": "public",
    "content": dedent(
        """\
        ## Symptom

        Celery workers lose their broker connection while `INFO memory` reports
        `mem_fragmentation_ratio:1.87`.

        ## Resolution

        Enable active defragmentation on Redis 7:

        ```bash
        redis-cli CONFIG SET activedefrag yes
        redis-cli CONFIG SET active-defrag-ignore-bytes 100mb
        ```
        """
    ),
}
