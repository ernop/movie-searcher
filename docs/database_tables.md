# Database Tables and Foreign Key Relationships

## 1. `movies` (Root Table)
**Primary Key:** `id` (Integer, autoincrement)  
**Foreign Keys:** None

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PRIMARY KEY, AUTOINCREMENT | Unique movie identifier |
| `path` | String | NOT NULL, UNIQUE, INDEXED | File path to the movie |
| `name` | String | NOT NULL, INDEXED | Movie name |
| `year` | Integer | NULLABLE | Release year |
| `length` | Float | NULLABLE | Video length in seconds |
| `size` | Integer | NULLABLE | File size in bytes |
| `hash` | String | NULLABLE, INDEXED | File hash for change detection |
| `images` | Text | NULLABLE | JSON array of image paths (as string) |
| `screenshots` | Text | NULLABLE | JSON array of screenshot paths (as string) |
| `created` | DateTime | DEFAULT now(), NOT NULL | Creation timestamp |
| `updated` | DateTime | DEFAULT now(), ON UPDATE now(), NOT NULL | Last update timestamp |

---

## 2. `ratings`
**Primary Key:** `id` (Integer, autoincrement)  
**Foreign Keys:** 
- `movie_id` → `movies.id` (CASCADE DELETE)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PRIMARY KEY, AUTOINCREMENT | Unique rating entry ID |
| `movie_id` | Integer | NOT NULL, UNIQUE, FK → movies.id, INDEXED | References movie (one rating per movie) |
| `rating` | Float | NOT NULL | Rating value |
| `created` | DateTime | DEFAULT now(), NOT NULL | Creation timestamp |
| `updated` | DateTime | DEFAULT now(), ON UPDATE now(), NOT NULL | Last update timestamp |

**Relationship:** One-to-One with `movies` (one rating per movie)

---

## 3. `watch_history`
**Primary Key:** `id` (Integer, autoincrement)  
**Foreign Keys:**
- `movie_id` → `movies.id` (CASCADE DELETE)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PRIMARY KEY, AUTOINCREMENT | Unique watch history entry ID |
| `movie_id` | Integer | NOT NULL, FK → movies.id, INDEXED | References movie |
| `watch_status` | Boolean | NULLABLE | NULL = unknown, True = watched, False = not watched |
| `created` | DateTime | DEFAULT now(), NOT NULL | When the watch event occurred |
| `updated` | DateTime | DEFAULT now(), ON UPDATE now(), NOT NULL | Last update timestamp |

**Relationship:** One-to-Many with `movies` (multiple watch entries per movie)

---

## 4. `launch_history`
**Primary Key:** `id` (Integer, autoincrement)  
**Foreign Keys:**
- `movie_id` → `movies.id` (CASCADE DELETE)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PRIMARY KEY, AUTOINCREMENT | Unique launch history entry ID |
| `movie_id` | Integer | NOT NULL, FK → movies.id, INDEXED | References movie |
| `subtitle` | String | NULLABLE | Subtitle file path used (if any) |
| `created` | DateTime | DEFAULT now(), NOT NULL | When the movie was launched |
| `updated` | DateTime | DEFAULT now(), ON UPDATE now(), NOT NULL | Last update timestamp |

**Relationship:** One-to-Many with `movies` (multiple launches per movie)

---

## 5. `movie_frames`
**Primary Key:** `id` (Integer, autoincrement)  
**Foreign Keys:**
- `movie_id` → `movies.id` (CASCADE DELETE)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PRIMARY KEY, AUTOINCREMENT | Unique frame entry ID |
| `movie_id` | Integer | NOT NULL, FK → movies.id, INDEXED | References movie |
| `path` | String | NOT NULL | File path to the extracted frame image |
| `created` | DateTime | DEFAULT now(), NOT NULL | When the frame was extracted |
| `updated` | DateTime | DEFAULT now(), ON UPDATE now(), NOT NULL | Last update timestamp |

**Relationship:** One-to-Many with `movies` (multiple frames per movie, though typically one)

---

## 6. `search_history`
**Primary Key:** `id` (Integer, autoincrement)  
**Foreign Keys:** None

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PRIMARY KEY, AUTOINCREMENT | Unique search history entry ID |
| `query` | String | NOT NULL, INDEXED | Search query string |
| `results_count` | Integer | NULLABLE | Number of results returned |
| `created` | DateTime | DEFAULT now(), NOT NULL | When the search was performed |
| `updated` | DateTime | DEFAULT now(), ON UPDATE now(), NOT NULL | Last update timestamp |

**Relationship:** Standalone table (no foreign keys)

---

## 7. `indexed_paths`
**Primary Key:** `id` (Integer, autoincrement)  
**Foreign Keys:** None

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PRIMARY KEY, AUTOINCREMENT | Unique indexed path entry ID |
| `path` | String | NOT NULL, UNIQUE, INDEXED | Directory path that has been indexed |
| `created` | DateTime | DEFAULT now(), NOT NULL | When the path was indexed |
| `updated` | DateTime | DEFAULT now(), ON UPDATE now(), NOT NULL | Last update timestamp |

**Relationship:** Standalone table (no foreign keys)

---

## 8. `config`
**Primary Key:** `id` (Integer, autoincrement)  
**Foreign Keys:** None

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PRIMARY KEY, AUTOINCREMENT | Unique config entry ID |
| `key` | String | NOT NULL, UNIQUE, INDEXED | Configuration key |
| `value` | Text | NULLABLE | Configuration value (JSON or text) |
| `created` | DateTime | DEFAULT now(), NOT NULL | Creation timestamp |
| `updated` | DateTime | DEFAULT now(), ON UPDATE now(), NOT NULL | Last update timestamp |

**Relationship:** Standalone table (no foreign keys)

---

## Foreign Key Summary

All foreign keys use **CASCADE DELETE**, meaning:
- When a movie is deleted, all related records are automatically deleted:
  - All ratings for that movie
  - All watch history entries for that movie
  - All launch history entries for that movie
  - All movie frames for that movie

### Foreign Key Relationships Diagram

```
movies (id)
    │
    ├─── ratings.movie_id (1:1, UNIQUE)
    ├─── watch_history.movie_id (1:N)
    ├─── launch_history.movie_id (1:N)
    └─── movie_frames.movie_id (1:N)

search_history (no FKs)
indexed_paths (no FKs)
config (no FKs)
```

---

## Index Summary

**Indexed Columns:**
- `movies.path` (UNIQUE INDEX)
- `movies.name` (INDEX)
- `movies.hash` (INDEX)
- `ratings.movie_id` (INDEX)
- `watch_history.movie_id` (INDEX)
- `launch_history.movie_id` (INDEX)
- `movie_frames.movie_id` (INDEX)
- `search_history.query` (INDEX)
- `indexed_paths.path` (UNIQUE INDEX)
- `config.key` (UNIQUE INDEX)

