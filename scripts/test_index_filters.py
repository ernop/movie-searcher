#!/usr/bin/env python3
"""Unit tests for incomplete-download detection, duplicate identity, and screenshot names."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scanning import (
    is_incomplete_download,
    release_identity,
    ffprobe_error_is_unplayable,
    media_kind_from_probe,
)
from video.screenshot import sanitize_screenshot_stem, screenshot_filename_for


class TestIncompleteDownload(unittest.TestCase):
    def test_part_suffix(self):
        self.assertTrue(is_incomplete_download("/movies/Foo.mkv.part"))

    def test_qbittorrent_suffix(self):
        self.assertTrue(is_incomplete_download("/movies/Foo.mkv.!qB"))

    def test_incomplete_directory(self):
        self.assertTrue(is_incomplete_download("/mnt/disk/movies/incomplete/Foo.mkv"))

    def test_finished_video(self):
        self.assertFalse(is_incomplete_download("/mnt/disk/movies/Foo.mkv"))

    def test_aria2_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "Movie.mkv"
            video.write_bytes(b"x")
            (Path(str(video) + ".aria2")).write_text("active")
            self.assertTrue(is_incomplete_download(video))


class TestReleaseIdentity(unittest.TestCase):
    def test_same_movie_different_groups_match(self):
        a = "/m/Apocalypse.Now.1979.Redux.REMASTERED.1080p.BluRay.x265-RARBG.mp4"
        b = "/m/Apocalypse.Now.1979.Redux.REMASTERED.1080p.BluRay.x265-RBG.mp4"
        self.assertEqual(release_identity(a), release_identity(b))

    def test_different_episodes_do_not_match(self):
        a = "/m/[KoR] Takeshi's Castle - 110 [h264].mkv"
        b = "/m/[KoR] Takeshi's Castle - 101 [h264].mkv"
        self.assertNotEqual(release_identity(a), release_identity(b))

    def test_generic_extras_keep_parent_folder(self):
        a = "/m/Show Season 8/Deleted Scenes.mkv"
        b = "/m/Show Season 9/Deleted Scenes.mkv"
        self.assertNotEqual(release_identity(a), release_identity(b))

    def test_different_years_do_not_match(self):
        a = "/m/Leviathan.1989.1080p.BluRay.x264.mp4"
        b = "/m/Leviathan.2014.1080p.BluRay.x264.mp4"
        self.assertNotEqual(release_identity(a), release_identity(b))


class TestScreenshotFilenames(unittest.TestCase):
    def test_unique_name_includes_movie_id(self):
        name = screenshot_filename_for("Heat", 300, movie_id=42)
        self.assertEqual(name, "Heat_42_screenshot300s.jpg")

    def test_legacy_name_omits_id(self):
        name = screenshot_filename_for("Heat", 300)
        self.assertEqual(name, "Heat_screenshot300s.jpg")

    def test_sanitize_strips_path_chars(self):
        self.assertNotIn("/", sanitize_screenshot_stem("A/B:C"))


class TestUnplayableMedia(unittest.TestCase):
    def test_moov_atom_marker(self):
        self.assertTrue(ffprobe_error_is_unplayable("moov atom not found"))

    def test_invalid_data_marker(self):
        self.assertTrue(ffprobe_error_is_unplayable("Invalid data found when processing input"))

    def test_clean_stderr_is_playable(self):
        self.assertFalse(ffprobe_error_is_unplayable(""))

    def test_video_stream_is_video(self):
        stdout = '{"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}'
        self.assertEqual(media_kind_from_probe(stdout, "", 0), "video")

    def test_audio_only(self):
        stdout = '{"streams": [{"codec_type": "audio"}]}'
        self.assertEqual(media_kind_from_probe(stdout, "", 0), "audio_only")

    def test_broken_container(self):
        self.assertEqual(
            media_kind_from_probe("{}", "moov atom not found", 1),
            "unreadable",
        )


if __name__ == "__main__":
    unittest.main()
