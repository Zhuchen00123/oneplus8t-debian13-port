#!/usr/bin/env python3
"""Pure mode/keyboard logic shared by the mobile UI and regression checks."""
MODES = ('terminal', 'panel', 'desktop')
SHIFT = dict(zip('1234567890-=[];\'\\,./`', '!@#$%^&*()_+{}:"|<>?~'))
SPECIAL = {'Esc': b'\x1b', 'Tab': b'\t', 'Enter': b'\r', 'Space': b' ',
           'Backspace': b'\x7f', 'Up': b'\x1b[A', 'Down': b'\x1b[B',
           'Right': b'\x1b[C', 'Left': b'\x1b[D', 'Home': b'\x1b[H', 'End': b'\x1b[F'}


def key_bytes(key, ctrl=False, alt=False, shift=False):
    if key in SPECIAL:
        value = SPECIAL[key]
        if key in ('Up', 'Down', 'Right', 'Left', 'Home', 'End') and (ctrl or alt or shift):
            modifier = 1 + bool(shift) + 2 * bool(alt) + 4 * bool(ctrl)
            return f'\x1b[1;{modifier}{chr(value[-1])}'.encode()
        if key == 'Tab' and shift:
            return b'\x1b[Z'
        if key == 'Space' and ctrl:
            value = b'\0'
    elif len(key) == 1 and 32 <= ord(key) <= 126:
        key = SHIFT.get(key, key.upper()) if shift else key
        if ctrl:
            code = ord(key.upper())
            if not 64 <= code <= 95:
                return b''
            value = bytes([code & 31])
        else:
            value = key.encode()
    else:
        raise ValueError('Unsupported terminal key')
    return b'\x1b' + value if alt else value


class ModeGate:
    def __init__(self, hold=0.7):
        self.hold = hold
        self.mode = None
        self.pending = None
        self.since = None
        self.last_count = None

    def accept(self, snapshot, now):
        valid = (isinstance(snapshot, dict) and snapshot.get('active') is True
                 and snapshot.get('owned') is True and snapshot.get('error') == 0
                 and type(snapshot.get('age_ms')) is int and 0 <= snapshot['age_ms'] <= 500
                 and type(snapshot.get('profile')) is int and 0 <= snapshot['profile'] <= 2
                 and snapshot.get('profile') == snapshot.get('reported')
                 and type(snapshot.get('count')) is int and snapshot['count'] >= 3)
        if not valid:
            self.pending = self.since = None
            return None
        count = snapshot['count']
        if self.last_count is not None and count < self.last_count:
            self.pending = self.since = None
        if count == self.last_count:
            return None
        self.last_count = count
        candidate = MODES[snapshot['profile']]
        if candidate != self.pending:
            self.pending, self.since = candidate, now
        if now - self.since < self.hold or candidate == self.mode:
            return None
        self.mode = candidate
        return candidate


def keyboard_capable(bus, words, bits_per_word=64):
    """Accept physical USB/BT keyboards, excluding power/volume/touch/slider."""
    if bus not in (3, 5):
        return False
    try:
        fields = words.split()
        mask = sum(int(part, 16) << (bits_per_word * i)
                   for i, part in enumerate(reversed(fields)))
    except (ValueError, TypeError, AttributeError):
        return False
    return all(mask & (1 << bit) for bit in (14, 16, 28, 30, 57))
