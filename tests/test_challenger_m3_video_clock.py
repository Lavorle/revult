"""Empirical Challenger 2 tests for VideoClock and renpysound_host integration (Milestone 3)."""

import unittest
import types
import sys


class TestRenpysoundHostVideoIntegration(unittest.TestCase):
    def setUp(self):
        import renpy.audio.renpysound_host as rsh
        self.rsh = rsh

    def test_maybe_arm_clock_when_uninitialized(self):
        # Verify that _maybe_arm_clock does not raise UnboundLocalError or crash
        # when _inited is False.
        self.rsh._channels[99] = {
            'video': True,
            'playing': True,
            'media_path': '/fake/test.webm',
            'clock_armed': False,
        }
        try:
            # Should not raise exception
            self.rsh._maybe_arm_clock(99)
        finally:
            self.rsh._channels.pop(99, None)

    def test_audio_clock_binding_respects_inited_flag(self):
        # If _inited is False, _maybe_arm_clock must not attempt audio bind
        self.rsh._inited = False
        self.rsh._channels[98] = {
            'video': True,
            'playing': True,
            'media_path': None,
            'clock_armed': True,
        }
        try:
            self.rsh._maybe_arm_clock(98)
            self.assertFalse(self.rsh._channels[98].get('_audio_clock_bound', False))
        finally:
            self.rsh._channels.pop(98, None)
