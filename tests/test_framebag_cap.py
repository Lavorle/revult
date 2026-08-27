"""Unit tests for FrameBag byte-cap and seek index (M2 T3).

 Covers byte-budget eviction, monotonic _abs_total, seek_index probes,
 env override RENPY_HOST_VIDEO_CAP_MB, and list compatibility.
 """

import os
import sys
import types

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Stub renpy_host so renpysound_host can be imported in SDL-tree CI (no host build)
if "renpy_host" not in sys.modules:
    stub = types.ModuleType("renpy_host")
    # minimal attrs used by renpysound_host paths
    stub.audio_set_volume = lambda *a, **k: None  # type: ignore
    stub.audio_queue_pcm_f32 = lambda *a, **k: None  # type: ignore
    stub.video_clock_start = lambda *a, **k: None  # type: ignore
    stub.video_clock_stop = lambda *a, **k: None  # type: ignore
    stub.video_clock_pos = lambda *a, **k: 0.0  # type: ignore
    stub.video_clock_pause = lambda *a, **k: None  # type: ignore
    stub.video_clock_unpause = lambda *a, **k: None  # type: ignore
    stub.video_clock_bind_audio = lambda *a, **k: None  # type: ignore
    stub.video_clock_drift_ms = lambda *a, **k: 0.0  # type: ignore
    stub.video_seek = lambda *a, **k: True  # type: ignore
    stub.audio_sample_rate = lambda: 48000  # type: ignore
    # M2 T2/T4 YUV + host decode probes — keep dual-tree green when stubbed
    stub.create_texture_nv12 = lambda *a, **k: (1, 2)  # type: ignore
    stub.create_texture_yuv420p = lambda *a, **k: (1, 2, 3)  # type: ignore
    stub.create_texture_rgba = lambda *a, **k: 1  # type: ignore
    stub.yuv420p_pipeline = lambda *a, **k: 1  # type: ignore
    stub.nv12_pipeline = lambda *a, **k: 1  # type: ignore
    stub.textured_pipeline = lambda *a, **k: 1  # type: ignore
    stub.video_host_probe = lambda *a, **k: "DecodePool workers=2 cap_bytes=67108864 ffmpeg-host=False backend=Vulkan StagingRing"  # type: ignore
    stub.video_decode_host = lambda *a, **k: False  # type: ignore
    sys.modules["renpy_host"] = stub

import unittest

from renpy.wgpu.video import FrameBag, FfmpegCmdBuilder


class TestByteCapEvictsOldest(unittest.TestCase):
    def test_byte_cap_evicts_oldest(self):
        # cap 10: second 6-byte frame should evict first 6-byte frame (6+6>10)
        bag = FrameBag(cap_bytes=10)
        bag.append_limited(b"a" * 6)
        self.assertEqual(len(bag), 1)
        self.assertEqual(bag.evicted_bytes, 0)
        bag.append_limited(b"b" * 6)
        # After eviction, only the newest frame remains, evicted 6 bytes
        self.assertEqual(len(bag), 1)
        self.assertEqual(bag.evicted_bytes, 6)
        self.assertEqual(bag[0], b"b" * 6)
        # seek_index should have evicted oldest sync -> len 1
        self.assertEqual(len(bag.seek_index), 1)


class TestAbsTotalMonotonic(unittest.TestCase):
    def test_abs_total_monotonic_despite_eviction(self):
        bag = FrameBag(cap_bytes=10)
        for i in range(5):
            bag.append_limited(b"x" * 6)
        # Each append increments _abs_total regardless of eviction
        self.assertEqual(int(getattr(bag, "_abs_total", 0)), 5)
        # len is bounded by cap (only 1 frame of 6 bytes fits in 10)
        self.assertEqual(len(bag), 1)
        # evicted_bytes should be 4*6 = 24 (first 4 evicted)
        self.assertEqual(bag.evicted_bytes, 24)
        # Seek index length tracks retained frames (1) not total
        self.assertEqual(len(bag.seek_index), 1)


class TestSeekIndexAppend(unittest.TestCase):
    def test_seek_index_append(self):
        bag = FrameBag(cap_bytes=1024)
        self.assertEqual(len(bag.seek_index), 0)
        bag.append_limited(b"frame1")
        self.assertEqual(len(bag.seek_index), 1)
        pts0, off0, is_key0 = bag.seek_index[0]
        self.assertIsInstance(pts0, int)
        self.assertIsInstance(off0, int)
        self.assertIsInstance(is_key0, bool)
        bag.append_limited(b"frame2")
        self.assertEqual(len(bag.seek_index), 2)
        # Build seek cmd should append to seek_index and return ffmpeg -ss cmd
        cmd = bag.build_seek_cmd("movie.mp4", 1280, 720, 30.0, 1500)
        self.assertIn("-ss", cmd)
        self.assertIn("1.500", " ".join(cmd))
        self.assertEqual(len(bag.seek_index), 3)
        self.assertEqual(bag.seek_index[-1][0], 1500)
        # FfmpegCmdBuilder probe also returns -ss cmd and reuses helpers
        cmd2 = FfmpegCmdBuilder.build_seek_cmd("movie.mp4", 1920, 1080, 12.0, 2000)
        self.assertIn("-ss", cmd2)
        self.assertIn("2.000", " ".join(cmd2))
        self.assertIn("-vf", cmd2)


class TestCapEnvOverride(unittest.TestCase):
    def test_cap_env_override(self):
        # RENPY_HOST_VIDEO_CAP_MB should control _new_path_entry cap_bytes
        orig = os.environ.get("RENPY_HOST_VIDEO_CAP_MB")
        try:
            os.environ["RENPY_HOST_VIDEO_CAP_MB"] = "16"
            # Need fresh import or directly call _new_path_entry
            from renpy.audio.renpysound_host import _new_path_entry

            entry = _new_path_entry("/tmp/test_cap_env.webm")
            self.assertEqual(entry["cap_bytes"], 16 * 1024 * 1024)
            self.assertIsInstance(entry["frames"], FrameBag)
            self.assertEqual(entry["frames"].cap_bytes, 16 * 1024 * 1024)
            # seek_index and evicted_bytes fields exist
            self.assertIn("seek_index", entry)
            self.assertIn("evicted_bytes", entry)
            self.assertEqual(entry["evicted_bytes"], 0)
            # Test default 64 when env unset
            del os.environ["RENPY_HOST_VIDEO_CAP_MB"]
            entry2 = _new_path_entry("/tmp/test_cap_default.webm")
            self.assertEqual(entry2["cap_bytes"], 64 * 1024 * 1024)
            # Test floor 16: cap 8 should clamp to 16
            os.environ["RENPY_HOST_VIDEO_CAP_MB"] = "8"
            entry3 = _new_path_entry("/tmp/test_cap_floor.webm")
            self.assertEqual(entry3["cap_bytes"], 16 * 1024 * 1024)
        finally:
            if orig is None:
                os.environ.pop("RENPY_HOST_VIDEO_CAP_MB", None)
            else:
                os.environ["RENPY_HOST_VIDEO_CAP_MB"] = orig


class TestListCompat(unittest.TestCase):
    def test_list_compat_len_getitem(self):
        bag = FrameBag([b"a", b"b", b"c"], cap_bytes=1024)
        self.assertEqual(len(bag), 3)
        self.assertEqual(bag[0], b"a")
        self.assertEqual(bag[1], b"b")
        self.assertEqual(bag[2], b"c")
        self.assertEqual(bag[-1], b"c")
        # list subclass isinstance
        self.assertIsInstance(bag, list)
        # extend via append_limited should keep list compat
        bag2 = FrameBag(cap_bytes=10)
        bag2.append_limited(b"1" * 6)
        bag2.append_limited(b"2" * 6)
        # After eviction len 1 but indexing still works
        self.assertEqual(len(bag2), 1)
        self.assertEqual(bag2[0], b"2" * 6)
        # _abs_total preserved
        self.assertTrue(hasattr(bag2, "_abs_total"))
        self.assertEqual(bag2._abs_total, 2)


if __name__ == "__main__":
    unittest.main()
