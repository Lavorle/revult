"""Unit tests for renpy.wgpu.video decoder strategy (T5).

Covers the FfmpegCmdBuilder single-point prefix, the live-capped FrameBag,
and the Decoder Protocol implementations (PipeReader / FilePoller) — no real
ffmpeg binary required.
"""

import os
import sys
import unittest

# Ensure repo root is importable when tests run from anywhere.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from renpy.wgpu.video import (  # noqa: E402
    FfmpegCmdBuilder,
    FrameBag,
    FilePoller,
    PipeReader,
    Decoder,
)


class TestFfmpegCmdBuilder(unittest.TestCase):
    def test_ffmpeg_cmd_builder_pipe(self):
        cmd = FfmpegCmdBuilder.build("movie.mp4", 1280, 720, 30.0, use_file=False)
        self.assertIn("ffmpeg", cmd)
        self.assertIn("-hide_banner", cmd)
        self.assertIn("-threads", cmd)
        self.assertIn("0", cmd)
        self.assertIn("-vf", cmd)
        # pipe mode emits rawvideo format flag
        self.assertIn("-f", cmd)
        self.assertIn("rawvideo", cmd)
        # fps must be present in the -vf chain
        vf = cmd[cmd.index("-vf") + 1]
        self.assertIn("fps=", vf)
        self.assertIn("scale=", vf)

    def test_ffmpeg_cmd_builder_file(self):
        cmd = FfmpegCmdBuilder.build("movie.mp4", 1280, 720, 30.0, use_file=True)
        self.assertIn("-hide_banner", cmd)
        self.assertIn("-threads", cmd)
        # file mode must NOT emit the pipe -f rawvideo flag
        self.assertNotIn("-f", cmd)
        self.assertNotIn("rawvideo", cmd)
        vf = cmd[cmd.index("-vf") + 1]
        self.assertIn("fps=", vf)


class TestFrameBagMaxlen(unittest.TestCase):
    def test_frame_bag_maxlen(self):
        cap = 4
        bag = FrameBag(live_cap=cap)
        self.assertIsInstance(bag, list)
        frames = [f"frame-{i}".encode() for i in range(10)]
        for f in frames:
            bag.append(f)
        # ring never exceeds live_cap
        self.assertEqual(len(bag), cap)
        self.assertEqual(list(bag), frames[-cap:])
        # abs_total still attachable
        self.assertTrue(hasattr(bag, "_abs_total"))

    def test_frame_bag_without_cap(self):
        bag = FrameBag([b"a", b"b"])
        bag.append(b"c")
        self.assertEqual(len(bag), 3)
        self.assertEqual(list(bag), [b"a", b"b", b"c"])


class TestDecoderProtocolNoCrash(unittest.TestCase):
    def _base_kwargs(self):
        return dict(
            raw_size=0,
            width=0,
            height=0,
            fps=0.0,
            path="",
            live_cap=16,
            all_frames=[],
            on_chunk=None,
            kickstart=8,
            publish_every=20,
            timeout=1.0,
            t0=0.0,
            max_frames=0,
        )

    def test_pipe_reader_empty_no_crash(self):
        reader = PipeReader(proc=None, **self._base_kwargs())
        self.assertIsInstance(reader, Decoder)
        # empty/no proc: read_chunk must return None, never raise
        self.assertIsNone(reader.read_chunk())
        self.assertIsNone(reader.read_chunk())
        # publish on nothing must also be safe
        bag = FrameBag(live_cap=16)
        reader.publish(bag)
        self.assertEqual(len(bag), 0)

    def test_file_poller_empty_no_crash(self):
        poller = FilePoller(tmp_path="", proc=None, **self._base_kwargs())
        self.assertIsInstance(poller, Decoder)
        self.assertIsNone(poller.read_chunk())
        self.assertIsNone(poller.read_chunk())
        bag = FrameBag(live_cap=16)
        poller.publish(bag)
        self.assertEqual(len(bag), 0)

    def test_read_one_loop_bounded(self):
        # Even when max_frames==0 the loop logic terminates without hanging.
        reader = PipeReader(proc=None, **self._base_kwargs())
        self.assertTrue(reader.is_done() or reader.read_chunk() is None)


if __name__ == "__main__":
    unittest.main()
