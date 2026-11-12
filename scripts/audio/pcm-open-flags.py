#!/usr/bin/env python3
"""Which snd_pcm_open() flag does this card reject?

spa.alsa opens with SND_PCM_NONBLOCK|NO_AUTO_RESAMPLE|NO_AUTO_CHANNELS|
NO_AUTO_FORMAT and gets EINVAL where aplay succeeds, so bisect the flags.
"""
import ctypes

lib = ctypes.CDLL("libasound.so.2")
PLAYBACK, CAPTURE = 0, 1
FLAGS = [
    ("plain", 0),
    ("nonblock", 0x0001),
    ("no_auto_resample", 0x10000),
    ("no_auto_channels", 0x20000),
    ("no_auto_format", 0x40000),
    ("no_softvol", 0x80000),
    ("no_auto_resample|channels", 0x10000 | 0x20000),
    ("no_auto_all3", 0x10000 | 0x20000 | 0x40000),
    ("softvol+all3", 0x80000 | 0x10000 | 0x20000 | 0x40000),
    ("nonblock+all3", 0x0001 | 0x10000 | 0x20000 | 0x40000),
    ("spa_default", 0x0001 | 0x10000 | 0x20000 | 0x40000 | 0x80000),
]


def err(rc):
    buf = ctypes.create_string_buffer(256)
    lib.snd_strerror(rc, buf, 256)
    return buf.value.decode()


for dev in ("hw:0,0", "hw:0,1"):
    for direction, dname in ((PLAYBACK, "play"), (CAPTURE, "cap")):
        print("== %s %s ==" % (dev, dname))
        for name, flags in FLAGS:
            h = ctypes.c_void_p()
            rc = lib.snd_pcm_open(ctypes.byref(h), dev.encode(), direction, flags)
            print("   %-28s %s" % (name, "ok" if rc == 0 else "rc=%d %s" % (rc, err(rc))))
            if rc == 0:
                lib.snd_pcm_close(h)
