# Developer Notes

## Agent Communication

### No Reflexive Praise

Don't open with "great question" or "profound insight." If something is good, show it through engagement, not by declaring it. Unearned praise destroys credibility.

### Banned Terminology

**Infinite ban** on:
- "Code smell"
- Rules named after celebrities or aggressive figures (Beyoncé Rule, Bezos Rule, etc.)
- Any engineering jargon that imports cultural baggage or confrontational framing

Describe concepts plainly without branded names.

### Memory and This File

Anything added to agent memory **must also be added here**. This file is what the user sees and controls. Memory is supplemental; this file is authoritative.

---

## Documentation Voice

**Before rewriting user-facing docs, read [WRITING_GUIDE.md](WRITING_GUIDE.md).**

Humble, explanatory tone—not promotional. Explain what we built and why. Acknowledge limitations. This is for documentary, experimental, and archival films—not mainstream Hollywood.

---

## Architecture

FastAPI backend with static HTML frontend. SQLite database (`movie_searcher.db`).

### Components

- `main.py`: FastAPI application, API endpoints, business logic
- `server.py`: Uvicorn server configuration and startup
- `start.py`: Cross-platform startup script with auto-setup (ffmpeg, VLC)
- `run.bat`: Windows launcher (creates venv, installs deps, runs start.py)
- `stop.py`: Cross-platform server stop script
- `index.html`: Frontend interface, search UI, autocomplete
- `database.py`: Database setup, migrations, utilities
- `models.py`: SQLAlchemy database models (table definitions)
- `core/models.py`: Pydantic models for API request/response validation
- `scanning.py`: Directory scanning and movie indexing
- `video_processing.py`: Video processing, screenshot extraction, ffmpeg integration
- `vlc_integration.py`: VLC player integration and launch management
- `screenshot_sync.py`: Screenshot database synchronization
- `config.py`: Configuration management (uses `settings.json` for API keys)
- `ffmpeg_setup.py`: FFmpeg detection and configuration

### Database

SQLite with SQLAlchemy ORM. Schema version tracked in `schema_version` table (current: 12). Automatic migrations on startup.

### State Management

- **Database**: Library data—movies, ratings, watch status, playlists, history, screenshots
- **settings.json**: Machine config—folder paths, UI preferences, API keys (gitignored)

### Design Principles

- No fallback/retry logic—if something fails, it's a bug to fix, not mask
- Single source of truth for each piece of data
- Hash-based change detection to avoid unnecessary re-scanning

---

## JavaScript Style

**No `/* */` block comments.** Use `//` only. Comments explain *why*, not *what*—function names should be self-documenting.

ESLint enforces this automatically. Agents see lint errors before finalizing edits; no manual commands needed. Custom rule: `static/js/eslint-rules/no-block-comments.js`.

---

## Tooling (PowerShell, Windows)

Use full venv paths to avoid module resolution issues:
- `.\venv\Scripts\python.exe -m pip install <pkg> --disable-pip-version-check --no-cache-dir --timeout 120`
- `.\venv\Scripts\python.exe -m ruff check .`

Avoid piping long installs through filters (`| Select-Object ...`)—it can swallow prompts and make interrupts look like failures. If installs were interrupted, rerun the full command.

**Lint commands:**
- Python: `.\venv\Scripts\python.exe -m ruff check .` (from repo root)
- JS: `npm run lint` (from `static/js`)
- HTML: `npm run lint:html` (from `static/js`, after `npm install`)
