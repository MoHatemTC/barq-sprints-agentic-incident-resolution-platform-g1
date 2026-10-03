#!/usr/bin/env bash
# Run on the shared EC2. Only the uniquely named rehearsal database is mutated.
# Exercises downgrade/upgrade and backup restoration, not a live release rollback.
set -euo pipefail
umask 077

target_revision="${1:-0005_cluster_waiters}"
rehearsal_db="barq_s2_2_test_rollback_$(date -u +%Y%m%d%H%M%S)_$$"
backup_dir="$HOME/barq-backups/$rehearsal_db"
mkdir -p "$backup_dir"

pg() {
  docker exec -i barq-postgres sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" "$@"' sh "$@"
}

echo "Rehearsal database: $rehearsal_db"
docker exec barq-postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "$backup_dir/current.dump"
pg -d postgres -c "CREATE DATABASE $rehearsal_db" > /dev/null
restore_copy() {
  docker exec -i barq-postgres sh -c \
    'exec pg_restore --exit-on-error --clean --if-exists -U "$POSTGRES_USER" -d "$1"' \
    sh "$rehearsal_db" < "$backup_dir/current.dump"
}
restore_copy

# Hash all application rows in a stable order. No row contents are printed.
fingerprint() {
  pg -d "$rehearsal_db" -At <<'SQL'
SELECT format('SELECT %L, count(*), md5(coalesce(string_agg(row_json, %L ORDER BY row_json), %L)) FROM (SELECT row_to_json(t)::text AS row_json FROM public.%I t) rows;',
              tablename, '', '', tablename)
FROM pg_tables WHERE schemaname='public' ORDER BY tablename;
\gexec
SQL
}
fingerprint > "$backup_dir/before.txt"
original_revision="$(pg -d "$rehearsal_db" -Atc 'SELECT version_num FROM alembic_version')"
original_events="$(pg -d "$rehearsal_db" -Atc 'SELECT count(*) FROM events')"

migrate_copy() {
  docker exec -i -e BARQ_REHEARSAL_DB="$rehearsal_db" barq-api python - "$1" "$2" <<'PY'
import os
import sys
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, make_url

database = os.environ['BARQ_REHEARSAL_DB']
assert database.startswith('barq_s2_2_test_rollback_')
override = os.environ.get('BARQ_DATABASE_URL') or os.environ.get('DATABASE_URL')
url = make_url(override) if override else URL.create(
    'postgresql+asyncpg', username=os.environ.get('POSTGRES_USER', 'postgres'),
    password=os.environ['POSTGRES_PASSWORD'], host=os.environ.get('POSTGRES_HOST', 'postgres'),
    port=int(os.environ.get('POSTGRES_PORT', '5432')),
)
os.environ['BARQ_DATABASE_URL'] = url.set(
    database=database, drivername='postgresql+asyncpg'
).render_as_string(hide_password=False)
operation = {'downgrade': command.downgrade, 'upgrade': command.upgrade}[sys.argv[1]]
operation(Config('/app/alembic.ini'), sys.argv[2])
PY
}
migrate_copy downgrade "$target_revision"
test "$(pg -d "$rehearsal_db" -Atc 'SELECT version_num FROM alembic_version')" = "$target_revision"
test "$(pg -d "$rehearsal_db" -Atc 'SELECT count(*) FROM events')" = "$original_events"
echo "Downgrade passed; $original_events event rows retained."
migrate_copy upgrade head
test "$(pg -d "$rehearsal_db" -Atc 'SELECT version_num FROM alembic_version')" = "$original_revision"
restore_copy
fingerprint > "$backup_dir/restored.txt"
cmp "$backup_dir/before.txt" "$backup_dir/restored.txt"
echo "Backup restoration passed: every public table has identical rows."
pg -d postgres -c "DROP DATABASE $rehearsal_db" > /dev/null
echo "Rehearsal copy removed; snapshot and fingerprints retained at $backup_dir"
