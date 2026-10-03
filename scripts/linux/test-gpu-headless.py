#!/usr/bin/env python3
"""Headless EGL/GLES triangle + pixel readback; no display or camera access."""
import ctypes as C
import sys

E = C.CDLL('libEGL.so.1')
G = C.CDLL('libGLESv2.so.2')
P, I, U, F = C.c_void_p, C.c_int, C.c_uint, C.c_float

def bind(lib, name, result, *args):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = result, args
    return fn

def checked(value, operation):
    if not value:
        raise RuntimeError(f'{operation}: EGL error {E.eglGetError():#x}')
    return value

bind(E, 'eglGetError', I)
bind(E, 'eglGetPlatformDisplay', P, U, P, C.POINTER(I))
bind(E, 'eglInitialize', U, P, C.POINTER(I), C.POINTER(I))
bind(E, 'eglBindAPI', U, U)
bind(E, 'eglChooseConfig', U, P, C.POINTER(I), C.POINTER(P), I, C.POINTER(I))
bind(E, 'eglCreatePbufferSurface', P, P, P, C.POINTER(I))
bind(E, 'eglCreateContext', P, P, P, P, C.POINTER(I))
bind(E, 'eglMakeCurrent', U, P, P, P, P)
bind(E, 'eglDestroyContext', U, P, P)
bind(E, 'eglDestroySurface', U, P, P)
bind(E, 'eglTerminate', U, P)
bind(G, 'glGetString', C.c_char_p, U)
bind(G, 'glGetError', U)
bind(G, 'glCreateShader', U, U)
bind(G, 'glShaderSource', None, U, I, C.POINTER(C.c_char_p), C.POINTER(I))
bind(G, 'glCompileShader', None, U)
bind(G, 'glGetShaderiv', None, U, U, C.POINTER(I))
bind(G, 'glGetShaderInfoLog', None, U, I, C.POINTER(I), C.c_char_p)
bind(G, 'glCreateProgram', U)
bind(G, 'glAttachShader', None, U, U)
bind(G, 'glBindAttribLocation', None, U, U, C.c_char_p)
bind(G, 'glLinkProgram', None, U)
bind(G, 'glGetProgramiv', None, U, U, C.POINTER(I))
bind(G, 'glGetProgramInfoLog', None, U, I, C.POINTER(I), C.c_char_p)
bind(G, 'glUseProgram', None, U)
bind(G, 'glViewport', None, I, I, I, I)
bind(G, 'glClearColor', None, F, F, F, F)
bind(G, 'glClear', None, U)
bind(G, 'glVertexAttribPointer', None, U, I, U, U, I, P)
bind(G, 'glEnableVertexAttribArray', None, U)
bind(G, 'glDrawArrays', None, U, I, I)
bind(G, 'glFinish', None)
bind(G, 'glReadPixels', None, I, I, I, I, U, U, P)

display = checked(E.eglGetPlatformDisplay(0x31DD, None, None), 'surfaceless display')
major, minor = I(), I()
checked(E.eglInitialize(display, C.byref(major), C.byref(minor)), 'initialize')
surface = context = None
try:
    checked(E.eglBindAPI(0x30A0), 'bind GLES')
    attributes = (I * 15)(0x3033, 1, 0x3040, 4, 0x3024, 8, 0x3023, 8,
                              0x3022, 8, 0x3021, 8, 0x3025, 0, 0x3038)
    config, count = P(), I()
    checked(E.eglChooseConfig(display, attributes, C.byref(config), 1, C.byref(count)), 'config')
    checked(count.value, 'no matching config')
    surface = checked(E.eglCreatePbufferSurface(display, config,
                      (I * 5)(0x3057, 64, 0x3056, 64, 0x3038)), 'pbuffer')
    context = checked(E.eglCreateContext(display, config, None,
                      (I * 3)(0x3098, 2, 0x3038)), 'GLES context')
    checked(E.eglMakeCurrent(display, surface, surface, context), 'make current')
    renderer = G.glGetString(0x1F01).decode()
    print(f'EGL {major.value}.{minor.value}; renderer={renderer}; GLES={G.glGetString(0x1F02).decode()}')
    if 'FD650' not in renderer and 'Adreno (TM) 650' not in renderer:
        raise RuntimeError('Not the expected KB2000 hardware renderer (software fallback is not a pass)')
    shaders = []
    for kind, source in [(0x8B31, b'attribute vec2 pos; void main(){gl_Position=vec4(pos,0.0,1.0);}'),
                         (0x8B30, b'precision mediump float; void main(){gl_FragColor=vec4(1.0,0.0,0.0,1.0);}')]:
        shader = G.glCreateShader(kind)
        sources = (C.c_char_p * 1)(source)
        G.glShaderSource(shader, 1, sources, None)
        G.glCompileShader(shader)
        ok = I()
        G.glGetShaderiv(shader, 0x8B81, C.byref(ok))
        if not ok.value:
            buf = C.create_string_buffer(4096)
            G.glGetShaderInfoLog(shader, len(buf), None, buf)
            raise RuntimeError(buf.value.decode())
        shaders.append(shader)
    program = G.glCreateProgram()
    for shader in shaders:
        G.glAttachShader(program, shader)
    G.glBindAttribLocation(program, 0, b'pos')
    G.glLinkProgram(program)
    ok = I()
    G.glGetProgramiv(program, 0x8B82, C.byref(ok))
    if not ok.value:
        buf = C.create_string_buffer(4096)
        G.glGetProgramInfoLog(program, len(buf), None, buf)
        raise RuntimeError(buf.value.decode())
    G.glUseProgram(program)
    G.glViewport(0, 0, 64, 64)
    G.glClearColor(0, 0, 0, 1)
    G.glClear(0x4000)
    vertices = (F * 6)(-0.8, -0.8, 0.8, -0.8, 0, 0.8)
    G.glVertexAttribPointer(0, 2, 0x1406, 0, 0, C.cast(vertices, P))
    G.glEnableVertexAttribArray(0)
    G.glDrawArrays(4, 0, 3)
    G.glFinish()
    def pixel(x, y):
        data = (C.c_ubyte * 4)()
        G.glReadPixels(x, y, 1, 1, 0x1908, 0x1401, data)
        return tuple(data)
    center, corner = pixel(32, 32), pixel(1, 63)
    error = G.glGetError()
    if center != (255, 0, 0, 255) or corner != (0, 0, 0, 255) or error:
        raise RuntimeError(f'Pixel mismatch: center={center}, corner={corner}, error={error:#x}')
    print(f'PASS: shader triangle rendered and read back; center={center}; corner={corner}')
finally:
    E.eglMakeCurrent(display, None, None, None)
    if context:
        E.eglDestroyContext(display, context)
    if surface:
        E.eglDestroySurface(display, surface)
    E.eglTerminate(display)
