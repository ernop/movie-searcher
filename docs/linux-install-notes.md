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
box (`silver@192.168.1.140:/mnt/seagate16/movies` via the `tvnik` ssh alias, ~5.3 TB, systemd
`tvnik-movies.service`). Scanning reads only; it never writes to the source tree.
Details: `mybrowser/config/tvnik-htpc-setup.md`.

## tvnik's own instance

tvnik runs its own movie-searcher (systemd user unit `movie-searcher.service`,
launched via `movie-utils/start.py`), listening on tvnik's `127.0.0.1:8002` only.
From PC, open it at **http://tvnik-movies.localhost** ("movie-searcher (tvnik)" on the
`http://localhost` dashboard and new-tab Local Services; registered in
`mybrowser/utilities/caddy/projects.json`) or directly at http://localhost:8012 — a
persistent SSH tunnel, user unit
`~/.config/systemd/user/tvnik-movie-searcher-tunnel.service` (`ssh -N -L
8012:127.0.0.1:8002 tvnik`, auto-reconnects). Manage with
`systemctl --user {status,restart,stop} tvnik-movie-searcher-tunnel`.

## Note

`.cursorrules` is Cursor-only and Windows/PowerShell-oriented (`.\venv\Scripts\...`).
On this Linux box use `./venv/bin/python` and `./venv/bin/ruff check .` instead.
