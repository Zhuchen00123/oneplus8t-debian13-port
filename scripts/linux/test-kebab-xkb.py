#!/usr/bin/env python3
"""Check the session keymap suppresses XF86PowerOff but preserves typing."""
import ctypes
import ctypes.util

lib = ctypes.CDLL(ctypes.util.find_library('xkbcommon'))
lib.xkb_context_new.argtypes = [ctypes.c_int]
lib.xkb_context_new.restype = ctypes.c_void_p
lib.xkb_keymap_new_from_names.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
lib.xkb_keymap_new_from_names.restype = ctypes.c_void_p
lib.xkb_keymap_key_get_syms_by_level.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
    ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.POINTER(ctypes.c_uint32))]
lib.xkb_keymap_key_get_syms_by_level.restype = ctypes.c_int
lib.xkb_keymap_unref.argtypes = [ctypes.c_void_p]
lib.xkb_context_unref.argtypes = [ctypes.c_void_p]
context = lib.xkb_context_new(0)
keymap = lib.xkb_keymap_new_from_names(context, None, 0)
if not keymap:
    raise SystemExit('Session XKB keymap could not compile')
try:
    for keycode, expected in ((124, 0xffffff), (38, ord('a')), (36, 0xff0d), (111, 0xff52)):
        pointer = ctypes.POINTER(ctypes.c_uint32)()
        count = lib.xkb_keymap_key_get_syms_by_level(keymap, keycode, 0, 0, ctypes.byref(pointer))
        actual = pointer[0] if count else 0
        assert actual == expected, (keycode, actual, expected)
    print('PASS: KEY_POWER suppressed only in compositor map; letters, Return, arrows preserved')
finally:
    lib.xkb_keymap_unref(keymap)
    lib.xkb_context_unref(context)
