"""
Library consistency checker for Movie Searcher.

Diagnoses the "movie shows in search but won't play in VLC" class of problems:
stale database rows whose files no longer exist (renamed/moved on the source
disk, or indexed under an old movies root), duplicate rows for the same title,
and network-mount (sshfs) health.

Read-only by default. Run from the project root with the venv python:

    ./venv/bin/python scripts/check_library_consistency.py            # report only
    ./venv/bin/python scripts/check_library_consistency.py --check-disk  # also walk the mount for unindexed files (slow over sshfs)
    ./venv/bin/python scripts/check_library_consistency.py --remove-missing      # delete rows under the current root whose file is gone
    ./venv/bin/python scripts/check_library_consistency.py --remove-stale-roots  # delete rows OUTSIDE the current movies root

Fix modes refuse to run if the mount looks unhealthy or files are unreadable
(I/O errors), so a dropped sshfs mount can never wipe the library metadata.
"""
import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

# Allow running as `python scripts/check_library_consistency.py` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_movies_folder  # noqa: E402
from database import (  # noqa: E402
    IndexedPath,
    LaunchHistory,
    Movie,
    MovieAudio,
    MovieList,
    MovieListItem,
    MovieStatus,
    PlaylistItem,
    Rating,
    Screenshot,
    SessionLocal,
)

VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', '.3gp'}
MIN_FILE_SIZE_BYTES = 50 * 1024 * 1024


def human_size(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def section(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def check_mount(root: str) -> dict:
    """Check whether the movies root is on a healthy (network) mount."""
    result = {"root": root, "exists": False, "mount_line": None, "fstype": None,
              "listdir_ok": False, "entries": 0, "healthy": False, "error": None}

    # Find the mount entry covering the root (longest matching mountpoint)
    try:
        with open("/proc/mounts") as f:
            best = None
            for line in f:
                parts = line.split()
                if len(parts) >= 3:
                    mnt_point = parts[1]
                    if root == mnt_point or root.startswith(mnt_point.rstrip("/") + "/") or mnt_point == "/":
                        if best is None or len(mnt_point) > len(best[1]):
                            best = (parts[0], mnt_point, parts[2])
            if best:
                result["mount_line"] = f"{best[0]} on {best[1]} type {best[2]}"
                result["fstype"] = best[2]
    except OSError:
        pass  # not Linux or /proc unavailable; existence checks below still apply

    try:
        result["exists"] = os.path.isdir(root)
        if result["exists"]:
            entries = os.listdir(root)
            result["listdir_ok"] = True
            result["entries"] = len(entries)
            result["healthy"] = len(entries) > 0
            if len(entries) == 0:
                result["error"] = "movies root is EMPTY (mount not attached?)"
        else:
            result["error"] = "movies root does not exist or is not a directory"
    except OSError as e:
        result["error"] = f"I/O error reading movies root: {e} (stale/disconnected mount?)"

    return result


def classify_existence(movies):
    """Stat every movie path. Returns (existing, missing, io_errors) lists of Movie."""
    existing, missing, io_errors = [], [], []
    total = len(movies)
    for i, m in enumerate(movies, 1):
        try:
            os.stat(m.path)
            existing.append(m)
        except FileNotFoundError:
            missing.append(m)
        except OSError:
            io_errors.append(m)
        if i % 500 == 0:
            print(f"  ... checked {i}/{total}")
    return existing, missing, io_errors


def dedup_winner(group):
    """Mirror the server's search dedup: largest size wins, ties -> newest id."""
    return max(group, key=lambda m: (m.size or 0, m.id or 0))


def delete_movie_rows(db, movies, label):
    """Delete movie rows plus related records, mirroring the scanner's cleanup."""
    removed = 0
    for movie in movies:
        try:
            for screenshot in db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all():
                if screenshot.shot_path and os.path.exists(screenshot.shot_path):
                    try:
                        os.remove(screenshot.shot_path)
                    except OSError:
                        pass
                db.delete(screenshot)
            db.query(Rating).filter(Rating.movie_id == movie.id).delete()
            db.query(MovieStatus).filter(MovieStatus.movie_id == movie.id).delete()
            db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).delete()
            db.query(PlaylistItem).filter(PlaylistItem.movie_id == movie.id).delete()
            db.query(MovieAudio).filter(MovieAudio.movie_id == movie.id).delete()

            # Unlink from AI movie lists (mark as not-in-library)
            affected_lists = set()
            for item in db.query(MovieListItem).filter(MovieListItem.movie_id == movie.id).all():
                item.is_in_library = False
                item.movie_id = None
                affected_lists.add(item.movie_list_id)
            for list_id in affected_lists:
                movie_list = db.query(MovieList).filter(MovieList.id == list_id).first()
                if movie_list:
                    movie_list.in_library_count = db.query(MovieListItem).filter(
                        MovieListItem.movie_list_id == list_id,
                        MovieListItem.is_in_library == True  # noqa: E712
                    ).count()

            db.delete(movie)
            db.commit()
            removed += 1
        except Exception as e:
            db.rollback()
            print(f"  FAILED to remove [{movie.id}] {movie.name}: {e}")
    print(f"Removed {removed}/{len(movies)} {label} row(s).")
    return removed


def walk_disk(root):
    """Single-pass scandir walk collecting indexable video files (like the scanner)."""
    found = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError as e:
            print(f"  warning: cannot read {current}: {e}")
            continue
        with it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                    if os.path.splitext(entry.name)[1].lower() not in VIDEO_EXTENSIONS:
                        continue
                    if 'sample' in Path(entry.name).stem.lower():
                        continue
                    if entry.stat(follow_symlinks=False).st_size < MIN_FILE_SIZE_BYTES:
                        continue
                except OSError:
                    continue
                found.append(entry.path)
    return found


def main():
    parser = argparse.ArgumentParser(description="Check Movie Searcher library consistency.")
    parser.add_argument("--examples", type=int, default=15, help="max examples to print per finding")
    parser.add_argument("--check-disk", action="store_true",
                        help="walk the movies root to find files missing from the DB (slow over sshfs)")
    parser.add_argument("--remove-missing", action="store_true",
                        help="delete DB rows under the current root whose file no longer exists")
    parser.add_argument("--remove-stale-roots", action="store_true",
                        help="delete DB rows whose path lies OUTSIDE the current movies root")
    parser.add_argument("--yes", action="store_true", help="skip confirmation prompts for fix modes")
    args = parser.parse_args()

    root = get_movies_folder()
    if not root:
        print("ERROR: movies_folder is not set in settings.json")
        return 1
    root = str(Path(root))
    root_prefix = root.rstrip("/\\") + os.sep

    # ------------------------------------------------------------- mount
    section(f"1. Mount / storage health: {root}")
    mount = check_mount(root)
    if mount["mount_line"]:
        print(f"Mount:   {mount['mount_line']}")
    print(f"Exists:  {mount['exists']}   Listable: {mount['listdir_ok']}   Top-level entries: {mount['entries']}")
    if mount["error"]:
        print(f"PROBLEM: {mount['error']}")
        if mount["fstype"] and "sshfs" in (mount["fstype"] or ""):
            print("         Try: systemctl restart tvnik-movies   (then re-run this script)")
    else:
        print("Mount looks healthy.")

    # ------------------------------------------------------------- DB overview
    db = SessionLocal()
    try:
        all_movies = db.query(Movie).all()
        section("2. Database overview")
        under_root = [m for m in all_movies if m.path == root or m.path.startswith(root_prefix)]
        under_ids = {m.id for m in under_root}
        outside_root = [m for m in all_movies if m.id not in under_ids]

        hidden_count = sum(1 for m in all_movies if m.hidden)
        print(f"Total movie rows:        {len(all_movies)}  (hidden: {hidden_count})")
        print(f"Under current root:      {len(under_root)}")
        print(f"OUTSIDE current root:    {len(outside_root)}")

        if outside_root:
            print("\nRows outside the current movies root are never cleaned up by the")
            print("scanner's orphan removal and can shadow working copies in search.")
            by_prefix = defaultdict(int)
            for m in outside_root:
                parts = Path(m.path).parts
                by_prefix[str(Path(*parts[:min(3, len(parts))]))] += 1
            print("Stale root prefixes:")
            for prefix, count in sorted(by_prefix.items(), key=lambda kv: -kv[1]):
                print(f"  {count:5d}  {prefix}")

        indexed_paths = db.query(IndexedPath).all()
        if indexed_paths:
            print("\nIndexed roots recorded in DB:")
            for p in indexed_paths:
                marker = "  (current)" if str(Path(p.path)) == root else "  (STALE?)"
                print(f"  {p.path}{marker}")

        # --------------------------------------------------------- existence
        section("3. File existence audit (rows under current root)")
        if not mount["healthy"]:
            print("SKIPPED: mount unhealthy — existence results would be meaningless.")
            existing, missing, io_errors = [], [], []
            existence_checked = False
        else:
            existing, missing, io_errors = classify_existence(under_root)
            existence_checked = True
            pct = (100.0 * len(missing) / len(under_root)) if under_root else 0.0
            print(f"Exists on disk: {len(existing)}   Missing: {len(missing)} ({pct:.1f}%)   I/O errors: {len(io_errors)}")
            if io_errors:
                print("I/O errors usually mean the sshfs mount is flaky — fix the mount first.")
                for m in io_errors[:args.examples]:
                    print(f"  [io-err] [{m.id}] {m.path}")
            for m in missing[:args.examples]:
                print(f"  [missing] [{m.id}] {m.name}  ->  {m.path}")
            if len(missing) > args.examples:
                print(f"  ... and {len(missing) - args.examples} more")

        # Existence for rows outside root (cheap: usually few, and on local disk)
        outside_missing = []
        if outside_root:
            for m in outside_root:
                try:
                    os.stat(m.path)
                except OSError:
                    outside_missing.append(m)
            print(f"\nOutside-root rows with missing files: {len(outside_missing)}/{len(outside_root)}")

        # --------------------------------------------------------- search visibility
        section("4. Search visibility: 'shows in search but won't play'")
        if not existence_checked:
            print("SKIPPED (needs the existence audit above).")
        else:
            exists_by_id = {m.id: True for m in existing}
            outside_missing_ids = {m.id for m in outside_missing}
            for m in outside_root:
                exists_by_id[m.id] = m.id not in outside_missing_ids
            groups = defaultdict(list)
            for m in all_movies:
                if not m.hidden:
                    groups[m.name.lower()].append(m)

            broken_recoverable = []   # search shows a dead row, but a working copy exists
            broken_no_copy = []       # every row for the title is dead
            true_dupes_on_disk = []   # >1 file actually on disk for the same title
            for group in groups.values():
                winner = dedup_winner(group)
                winner_ok = exists_by_id.get(winner.id, False)
                alive = [m for m in group if exists_by_id.get(m.id, False)]
                if not winner_ok and alive:
                    broken_recoverable.append((winner, alive))
                elif not winner_ok and not alive:
                    broken_no_copy.append(winner)
                if len(alive) > 1:
                    true_dupes_on_disk.append(alive)

            print("Titles where the search result points at a MISSING file but a working")
            print(f"copy exists under another path: {len(broken_recoverable)}")
            for winner, alive in broken_recoverable[:args.examples]:
                print(f"  '{winner.name}'")
                print(f"     dead row : [{winner.id}] {winner.path}")
                print(f"     alive at : [{alive[0].id}] {alive[0].path}")
            if len(broken_recoverable) > args.examples:
                print(f"  ... and {len(broken_recoverable) - args.examples} more")

            print(f"\nTitles with NO working copy at all: {len(broken_no_copy)}")
            for m in broken_no_copy[:args.examples]:
                print(f"  [{m.id}] {m.name}  ->  {m.path}")
            if len(broken_no_copy) > args.examples:
                print(f"  ... and {len(broken_no_copy) - args.examples} more")

            # ----------------------------------------------------- duplicates
            section("5. Duplicates")
            multi_row = [g for g in groups.values() if len(g) > 1]
            shadowed = [g for g in multi_row
                        if any(not exists_by_id.get(m.id, False) for m in g)
                        and any(exists_by_id.get(m.id, False) for m in g)]
            print(f"Titles with multiple DB rows: {len(multi_row)}")
            print(f"  ...of which have dead rows alongside a working copy: {len(shadowed)}")
            print(f"Titles with multiple files actually on disk: {len(true_dupes_on_disk)}")

            # Identical files duplicated on disk: same basename + same size, both exist
            by_sig = defaultdict(list)
            for m in existing:
                by_sig[(Path(m.path).name.lower(), m.size)].append(m)
            identical = [v for v in by_sig.values() if len(v) > 1]
            wasted = sum((len(v) - 1) * (v[0].size or 0) for v in identical)
            print(f"Identical files (same filename+size) at multiple paths: {len(identical)} "
                  f"(~{human_size(wasted)} duplicated)")
            for group in identical[:args.examples]:
                print(f"  {Path(group[0].path).name} ({human_size(group[0].size)}):")
                for m in group:
                    print(f"     [{m.id}] {m.path}")

        # --------------------------------------------------------- disk walk
        if args.check_disk and mount["healthy"]:
            section("6. Disk vs DB (files on disk missing from the index)")
            print("Walking the movies root (this is slow over sshfs)...")
            disk_files = walk_disk(root)
            db_paths = {m.path for m in under_root}
            unindexed = [p for p in disk_files if p not in db_paths]
            print(f"Indexable files on disk: {len(disk_files)}   In DB: {len(under_root)}   Not indexed: {len(unindexed)}")
            for p in unindexed[:args.examples]:
                print(f"  [unindexed] {p}")
            if len(unindexed) > args.examples:
                print(f"  ... and {len(unindexed) - args.examples} more")
            if unindexed:
                print("Run a scan from the web UI to index these.")

        # --------------------------------------------------------- fixes
        if args.remove_missing or args.remove_stale_roots:
            section("Fixes")
            if not mount["healthy"] or (existence_checked and io_errors):
                print("REFUSING to modify the database: mount unhealthy or I/O errors present.")
                return 1

            if args.remove_missing:
                if not existence_checked:
                    print("Cannot --remove-missing without a completed existence audit.")
                elif not missing:
                    print("--remove-missing: nothing to do.")
                elif len(missing) > 0.5 * len(under_root) and not args.yes:
                    print(f"REFUSING: {len(missing)}/{len(under_root)} rows look missing (>50%). "
                          f"That smells like a mount problem. Re-run with --yes to override.")
                else:
                    if args.yes or input(f"Delete {len(missing)} missing-file rows? [y/N] ").lower() == "y":
                        delete_movie_rows(db, missing, "missing-file")

            if args.remove_stale_roots:
                if not outside_root:
                    print("--remove-stale-roots: nothing to do.")
                else:
                    if args.yes or input(f"Delete {len(outside_root)} outside-root rows? [y/N] ").lower() == "y":
                        delete_movie_rows(db, outside_root, "outside-root")
        else:
            section("Next steps")
            print("This was a read-only report. To clean up:")
            print("  --remove-missing       delete rows under the root whose file is gone")
            print("  --remove-stale-roots   delete rows indexed under an old movies root")
            print("Then re-run a scan from the web UI.")

    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
