# Database Schema

## Tables and Foreign Keys

### 1. `movies` (Root Table)
- **Primary Key:** `id` (Integer, autoincrement)
- **Foreign Keys:** None (root table)
- **Columns:** id, path, name, year, length, size, hash, images, screenshots, created, updated

### 2. `ratings`
- **Primary Key:** `id` (Integer, autoincrement)
- **Foreign Keys:** 
  - `movie_id` → `movies.id` (CASCADE DELETE)
- **Columns:** id, movie_id, rating, created, updated

### 3. `watch_history`
- **Primary Key:** `id` (Integer, autoincrement)
- **Foreign Keys:**
  - `movie_id` → `movies.id` (CASCADE DELETE)
- **Columns:** id, movie_id, watch_status (Boolean), created, updated

### 4. `launch_history`
- **Primary Key:** `id` (Integer, autoincrement)
- **Foreign Keys:**
  - `movie_id` → `movies.id` (CASCADE DELETE)
- **Columns:** id, movie_id, subtitle, created, updated

### 5. `movie_frames`
- **Primary Key:** `id` (Integer, autoincrement)
- **Foreign Keys:**
  - `movie_id` → `movies.id` (CASCADE DELETE)
- **Columns:** id, movie_id, path, created, updated

### 6. `search_history`
- **Primary Key:** `id` (Integer, autoincrement)
- **Foreign Keys:** None
- **Columns:** id, query, results_count, created, updated

### 7. `indexed_paths`
- **Primary Key:** `id` (Integer, autoincrement)
- **Foreign Keys:** None
- **Columns:** id, path, created, updated

### 8. `config`
- **Primary Key:** `id` (Integer, autoincrement)
- **Foreign Keys:** None
- **Columns:** id, key, value, created, updated

---

## Entity Relationship Diagram

```
┌─────────────────┐
│     movies      │
│  (PK: id)       │
└────────┬────────┘
         │
         │ (1:N relationships)
         │
    ┌────┴────┬──────────┬──────────────┐
    │         │          │               │
    │         │          │               │
┌───▼────┐ ┌──▼──────┐ ┌─▼──────────┐ ┌─▼──────────┐
│ratings │ │watch_   │ │launch_     │ │movie_      │
│        │ │history  │ │history     │ │frames      │
├────────┤ ├─────────┤ ├────────────┤ ├────────────┤
│id (PK) │ │id (PK)  │ │id (PK)     │ │id (PK)     │
│movie_id│ │movie_id │ │movie_id    │ │movie_id    │
│(FK,UNQ)│ │(FK)     │ │(FK)        │ │(FK)        │
│rating  │ │watch_   │ │subtitle    │ │path        │
│created │ │status   │ │created     │ │created     │
│updated │ │created  │ │updated     │ │updated     │
│        │ │updated  │ │            │ │            │
└────────┘ └─────────┘ └────────────┘ └────────────┘

┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ search_history  │  │ indexed_paths   │  │     config      │
│  (PK: id)       │  │  (PK: id)       │  │  (PK: id)       │
├─────────────────┤  ├─────────────────┤  ├─────────────────┤
│ id              │  │ id              │  │ id              │
│ query           │  │ path (UNIQUE)   │  │ key (UNIQUE)    │
│ results_count   │  │ created         │  │ value           │
│ created         │  │ updated         │  │ created         │
│ updated         │  │                 │  │ updated         │
└─────────────────┘  └─────────────────┘  └─────────────────┘
     (No FKs)            (No FKs)            (No FKs)
```

## Relationships Summary

- **movies** → **ratings**: One-to-One (movie_id is UNIQUE in ratings)
- **movies** → **watch_history**: One-to-Many (multiple watch entries per movie)
- **movies** → **launch_history**: One-to-Many (multiple launches per movie)
- **movies** → **movie_frames**: One-to-Many (multiple frames per movie)

All foreign keys have `CASCADE DELETE` enabled, meaning:
- When a movie is deleted, all related ratings, watch history, launch history, and frames are automatically deleted.

## Schema Rules

- Every table has an autoincrement integer `id` as primary key (NOT NULL)
- Every table has `created` and `updated` DateTime fields that auto-update
- All foreign keys reference `movies.id` (not `movies.path`)
- `watch_status` in `watch_history` is Boolean (NULL = unknown, True = watched, False = not watched)
