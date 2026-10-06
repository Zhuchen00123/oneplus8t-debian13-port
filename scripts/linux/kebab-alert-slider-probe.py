#!/usr/bin/env python3
"""Read only the documented Hall device-ID register on kebab's verified bus."""
import ctypes
import fcntl
import json
import os
from pathlib import Path

I2C_RDWR = 0x0707
I2C_M_RD = 1


class Message(ctypes.Structure):
    _fields_ = [('addr', ctypes.c_uint16), ('flags', ctypes.c_uint16),
                ('length', ctypes.c_uint16), ('buf', ctypes.c_void_p)]


class Transfer(ctypes.Structure):
    _fields_ = [('messages', ctypes.POINTER(Message)), ('count', ctypes.c_uint32)]


def read_register(fd, address, register, count=1):
    selector = (ctypes.c_uint8 * 1)(register)
    result = (ctypes.c_uint8 * count)()
    messages = (Message * 2)(
        Message(address, 0, 1, ctypes.addressof(selector)),
        Message(address, I2C_M_RD, count, ctypes.addressof(result)))
    transfer = Transfer(messages, 2)
    completed = fcntl.ioctl(fd, I2C_RDWR, transfer)
    if completed != 2:
        raise OSError(f'incomplete I2C transfer: {completed}/2')
    return bytes(result)


def main():
    controller = Path('/sys/bus/i2c/devices/i2c-1').resolve(strict=True)
    if controller.name != 'i2c-1' or controller.parent.name != '984000.i2c':
        raise SystemExit('Unexpected I2C controller; refusing probe')
    candidates = {0x18: ('IST8801 down', 0x81), 0x19: ('IST8801 up', 0x81),
                  0x0c: ('MXM1120 down', 0x9c), 0x0d: ('MXM1120 up', 0x9c)}
    fd = os.open('/dev/i2c-1', os.O_RDWR | os.O_CLOEXEC)
    try:
        for address, (label, expected) in candidates.items():
            if Path(f'/sys/bus/i2c/devices/1-{address:04x}').exists():
                raise SystemExit(f'Address {address:#x} already instantiated; refusing probe')
            record = dict(address=f'0x{address:02x}', candidate=label,
                          register='0x09', expected_id=f'0x{expected:02x}')
            try:
                value = read_register(fd, address, 0x09)[0]
                record.update(device_id=f'0x{value:02x}', matches=value == expected)
            except OSError as error:
                record.update(error=str(error), errno=error.errno)
            print(json.dumps(record), flush=True)
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
