# Local Setup — Linux (PC)

Setup notes for running Movie Searcher on the Ubuntu PC box. Windows is the
primary target (see `installation.md`); this captures the Linux specifics.

## Environment

- **Host:** PC (Ubuntu 26.04, Ryzen 5950X)
- **Python:** 3.14.4 (`/usr/bin/python3`)
- **Project:** `~/proj/movie-searcher`
- **Server URL:** http://localhost:8002

## What was installed

### System packages (apt)

```bash
sudo apt-get install -y ffmpeg vlc
```

- ffmpeg 8.0.1 → `/usr/bin/ffmpeg`, `/usr/bin/ffprobe`
- VLC 3.0.23 → `/usr/bin/vlc`

(The app's `start.py` only auto-installs these on Windows via winget. On Linux
they must be installed manually, which is done above.)

### Python venv

```bash
cd ~/proj/movie-searcher
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

All requirements built cleanly against Python 3.14 (fastapi, uvicorn,
sqlalchemy, pydantic, openai, anthropic, faster-whisper, etc.).

## Configuration

`settings.json` (gitignored, per-machine). The ffmpeg/vlc/ffprobe paths are
written automatically by `setup/setup_ffmpeg.py` + `setup/setup_vlc.py` on first
start. Current contents:

```json
{
  "movies_folder": "/home/ef/proj/movie-searcher/movies",
  "ffmpeg_path": "/usr/bin/ffmpeg",
  "vlc_path": "/usr/bin/vlc",
  "ffprobe_path": "/usr/bin/ffprobe"
}
```

> `movies_folder` is currently a local seed folder. The plan is to repoint it at
> the tvnik big disk once that's mounted (see below).

Seed folders created: `movies/`, `screenshots/`, `frames/` (latter two are
gitignored and auto-managed by the app).

To enable AI search, add `"AnthropicApiKey"` or `"OpenAIApiKey"` to
`settings.json`.

## Run / stop

```bash
cd ~/proj/movie-searcher
./venv/bin/python start.py        # starts server on :8002, opens browser
./venv/bin/python stop.py         # or Ctrl+C in the server window
```

## Verification (done)

- `GET /` → 200, title "Movie Searcher"
- `/api/config`, `/api/stats`, `/api/playlists` → 200
- Database initialized at schema v19
- ffmpeg/ffprobe/VLC all reported "fully operational" at startup

## tvnik movie storage

The actual movie library lives on the big disk on **tvnik**, accessed over the
LAN. PC mounts it and scans in place — nothing is copied.

### Connection

| Field | Value |
| --- | --- |
| Host | tvnik (`192.168.1.219`) |
| **Login user** | **`silver`** (not `ef`) |
| SSH key | `~/.ssh/id_ed25519` (installed on tvnik `2026-07-03`) |
| SSH alias | `ssh tvnik` (configured in `~/.ssh/config`) |

> The bare hostname `tvnik` does not resolve via the OS resolver on PC; use the
> IP `192.168.1.219`, or the `ssh tvnik` alias which pins the IP + user + key.

### The disk

- **`/mnt/seagate16`** — 15TB Seagate, **NTFS** (mounted on tvnik via ntfs-3g),
  89% full (13T used, ~1.7T free).
- Movie library: **`/mnt/seagate16/movies`** — ~2,000 titles, ~3,100 video
  files, **5.3TB**.

### Mount plan (PC side)

1. Mount `/mnt/seagate16/movies` on PC, **read-only**, at a stable mountpoint
   (e.g. `/mnt/tvnik-movies`) via sshfs:
   ```bash
   sshfs -o ro,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3 \
       tvnik:/mnt/seagate16/movies /mnt/tvnik-movies
   ```
   (Read-only is enough — the app only reads the movie files; screenshots/frames
   and the DB are written locally on PC.)
2. Repoint `movies_folder` in `settings.json` at the mount.
3. Scan from the web UI.

sshfs uses the SSH key already set up. If heavier IO / a permanent share is
wanted later (or the Windows fleet needs the same store), graduate to NFS or
Samba on tvnik without changing the app — only the mount + `movies_folder`.

### Current state (2026-07-03)

- Mounted: `tvnik:/mnt/seagate16/movies` → **`/mnt/tvnik-movies`** (read-only,
  sshfs, `reconnect`). Mountpoint owned by `ef`.
- `settings.json` `movies_folder` → `/mnt/tvnik-movies` (server picks this up
  live, no restart needed).
- **Not yet scanned** — trigger the first scan from the web UI. It's ~2,000
  titles, so the initial scan/metadata pass will take a while.
- **Persistent across reboots** via a systemd service:
  `/etc/systemd/system/tvnik-movies.service` (runs `sshfs -f` as `ef`,
  `Restart=on-failure`, `WantedBy=multi-user.target`, cleans stale mounts on
  restart). Manage with `systemctl {status,restart,stop} tvnik-movies`.
  Manual remount, if ever needed:
  ```bash
  sshfs -o ro,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,idmap=user \
      tvnik:/mnt/seagate16/movies /mnt/tvnik-movies
  ```

> tvnik disks: the movies are on the **16TB Seagate** (`/mnt/seagate16`). tvnik is
> also configured for a **6TB WD** (`/mnt/wd6`, `nofail`) but that disk is not
> currently attached — reconnect it there if any movies are expected on it.

Also live: the Caddy port manager (`caddy-proxy.service`) — reach the app at
**http://movie-searcher.localhost**.
