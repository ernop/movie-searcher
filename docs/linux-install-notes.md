# movie-searcher — agent notes

Local FastAPI video-library browser: point it at a folder of videos; it indexes
metadata, generates screenshots, and launches VLC. Sibling of `mybrowser`
(fleet orientation: `~/proj/mybrowser/AGENTS.md`).

## Run (Linux / PC)

- `./venv/bin/python start.py` → serves http://localhost:8002 (also
  http://movie-searcher.localhost via the Caddy port manager). Stop: `stop.py` or Ctrl+C.
- venv is Python 3.14. System deps: **ffmpeg + VLC** (install via apt; `start.py`
  only auto-installs them on Windows). Lint: `./venv/bin/ruff check .`
- Full Linux setup and verification: `docs/local-setup-linux.md`.

## Config

`settings.json` (gitignored) — copy from `settings.example.json`. The
ffmpeg/vlc/ffprobe paths auto-fill on first start. `movies_folder` is the library
root. A blank `local_target_folder` disables the per-movie "Copy to Local" feature
(that feature copies one film at a time to a local disk — it never bulk-copies,
and scanning never copies).

## This install's store

`movies_folder` = `/mnt/tvnik-movies` — a **read-only** sshfs mount of the tvnik
box (`silver@192.168.1.219:/mnt/seagate16/movies`, ~5.3 TB, systemd
`tvnik-movies.service`). Scanning reads only; it never writes to the source tree.
Details: `mybrowser/config/tvnik-htpc-setup.md`.

## Note

`.cursorrules` is Cursor-only and Windows/PowerShell-oriented (`.\venv\Scripts\...`).
On this Linux box use `./venv/bin/python` and `./venv/bin/ruff check .` instead.
