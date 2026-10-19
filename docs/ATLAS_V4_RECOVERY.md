# ATLAS V4 migration and recovery procedure

ATLAS V4 uses Alembic for forward-only schema control. The current adoption revision is `20260820_0001`. It creates only missing SQLModel tables and PostgreSQL price partitions with check-first semantics, so it can adopt the pre-Alembic database without replacing persisted evidence.

## Normal validation

From the delivery root, with PostgreSQL and Redis running:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\validate-migrations.ps1"
```

The script loads the existing loopback PostgreSQL credential without printing it, runs `alembic upgrade head`, and verifies that every Alembic head is current. Backend startup performs the same upgrade before Uvicorn is allowed to bind.

## Backup before a future migration

1. Stop the Atlas backend so no new evidence is written.
2. Create a PostgreSQL custom-format backup inside the existing container:

```powershell
docker compose --env-file .env.infrastructure -f docker-compose.infrastructure.yml exec -T postgres pg_dump -U atlas -d atlas -Fc -f /tmp/atlas-v4-backup.dump
docker compose --env-file .env.infrastructure -f docker-compose.infrastructure.yml cp postgres:/tmp/atlas-v4-backup.dump .\backups\atlas-v4-backup.dump
```

3. Verify the archive before changing the database:

```powershell
docker compose --env-file .env.infrastructure -f docker-compose.infrastructure.yml cp .\backups\atlas-v4-backup.dump postgres:/tmp/atlas-v4-verify.dump
docker compose --env-file .env.infrastructure -f docker-compose.infrastructure.yml exec -T postgres pg_restore --list /tmp/atlas-v4-verify.dump
```

4. Record the current revision with `cd backend; ..\.venv\Scripts\python.exe -m alembic current --check-heads`.

Never include `.env.infrastructure`, database dumps, bridge tokens, or account details in source control or support logs.

## Recovery after a failed migration

- Do not run `alembic downgrade` against Atlas evidence. The baseline downgrade intentionally raises and does not drop tables.
- Keep the backend stopped and preserve the failed database volume for diagnosis.
- Restore the verified backup into a separate recovery database first; run the full backend gate and `validate-migrations.ps1` against that recovery URL.
- Promote the recovered database only after revision, row counts, signal lineage, calibration artifacts, and audit records match the backup manifest.
- Prefer a corrected forward migration over an in-place manual schema edit.

No recovery action enables broker execution. MT5 remains a read-only host dependency and canonical signals remain analytical records.
