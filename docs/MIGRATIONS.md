# Database Migrations

## Overview

This project uses a simple schema version tracking system. Migrations run automatically at server startup, but only when needed (based on schema version).

## How It Works

- **Schema Version Tracking**: A `schema_version` table tracks the current database schema version
- **Auto-Migration**: Migrations run automatically at startup only if the database version is outdated
- **Version Constant**: `CURRENT_SCHEMA_VERSION` in `main.py` defines the target schema version

## Adding Schema Changes (Future)

When you need to add a new column, table, or modify the schema:

1. **Update the SQLAlchemy models** in `main.py` (e.g., add a column to `Movie`)

2. **Increment `CURRENT_SCHEMA_VERSION`**:
   ```python
   CURRENT_SCHEMA_VERSION = 2  # Was 1, now 2
   ```

3. **Add migration code** in `migrate_db_schema()` function, before the old schema migration:
   ```python
   # Handle version upgrades (future schema changes)
   if current_version < 2:
       logger.info("Migrating to schema version 2: adding 'description' column...")
       with engine.begin() as conn:
           conn.execute(text("ALTER TABLE movies ADD COLUMN description TEXT"))
       set_schema_version(2, "Added 'description' column to movies table")
       current_version = 2
   ```

4. **That's it!** The migration will run automatically on next startup if needed.

## Example: Adding a New Column

```python
# 1. Update model
class Movie(Base):
    # ... existing columns ...
    description = Column(Text, nullable=True)  # NEW COLUMN

# 2. Increment version
CURRENT_SCHEMA_VERSION = 2

# 3. Add migration in migrate_db_schema():
if current_version and current_version < 2:
    logger.info("Migrating to schema version 2...")
    existing_columns = {col['name']: col for col in inspector.get_columns("movies")}
    if "description" not in existing_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE movies ADD COLUMN description TEXT"))
    set_schema_version(2, "Added 'description' column to movies table")
```

## Why Not Alembic?

For a simple single-file SQLite project, Alembic adds unnecessary complexity:
- Extra files and directories
- Command-line setup
- More moving parts

This simple approach:
- Keeps everything in one place
- Auto-runs when needed
- Version tracks to avoid unnecessary checks
- Won't distract from product development

## Current Schema Version

Schema version **1** (initial version with `id` PK and all current tables/columns).

