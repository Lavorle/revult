
import renpy_host
import time

renpy_host.video_clock_start(5)
ticks = renpy_host.get_ticks_ms()
print(f'Current process uptime ticks: {ticks} ms ({ticks/1000.0} s)')

# Test setting pos to 0.0 (less than uptime)
renpy_host.video_clock_set_pos(5, 0.0)
pos0 = renpy_host.video_clock_pos(5)
print(f'Set pos to 0.0 -> got {pos0:.3f} s')

# Test setting pos to 10.0 (greater than uptime)
renpy_host.video_clock_set_pos(5, 10.0)
pos10 = renpy_host.video_clock_pos(5)
print(f'Set pos to 10.0 -> got {pos10:.3f} s (EXPECTED 10.0 s)')

# Test setting pos under AudioSample clock
renpy_host.video_clock_bind_audio(5, 48000)
renpy_host.video_clock_set_pos(5, 20.0)
pos20 = renpy_host.video_clock_pos(5)
print(f'Set pos to 20.0 (AudioSample) -> got {pos20:.3f} s (EXPECTED 20.0 s)')

renpy_host.video_clock_stop(5)
renpy_host.request_quit()
