#!/usr/bin/env python3
"""Bounded, read-only SSC inventory and effective-registry queries over QRTR.

No preload shim, fixed QRTR port, sensor enable, registry write or da_test command.
Registry wire definition: Qualcomm sns_registry.proto (read=512, event=514).
"""
import argparse
import json
import re
import socket
import struct
import subprocess
import time
from pathlib import Path


DISCOVERY = (0xABABABABABABABAB, 0xABABABABABABABAB)


def varint(n):
    if n < 0 or n >= 1 << 64:
        raise ValueError("varint out of range")
    out = bytearray()
    while n >= 128:
        out.append((n & 127) | 128)
        n >>= 7
    return bytes(out + bytes([n]))


def read_varint(data, pos):
    result = 0
    for shift in range(0, 70, 7):
        if pos >= len(data):
            raise ValueError("truncated varint")
        b = data[pos]
        pos += 1
        if shift == 63 and b > 1:
            raise ValueError("varint overflow")
        result |= (b & 127) << shift
        if not b & 128:
            return result, pos
    raise ValueError("varint overflow")


def fields(data):
    result, pos = [], 0
    while pos < len(data):
        tag, pos = read_varint(data, pos)
        num, wire = tag >> 3, tag & 7
        if num == 0:
            raise ValueError("invalid protobuf field zero")
        if wire == 0:
            value, pos = read_varint(data, pos)
        elif wire in (1, 2, 5):
            if wire == 2:
                size, pos = read_varint(data, pos)
            else:
                size = 8 if wire == 1 else 4
            if pos + size > len(data):
                raise ValueError("truncated protobuf field")
            value = data[pos:pos + size]
            pos += size
        else:
            raise ValueError("unsupported protobuf wire type")
        result.append((num, wire, value))
    return result


def one(data, num, wire, default=None):
    matches = [v for n, w, v in fields(data) if (n, w) == (num, wire)]
    if len(matches) > 1:
        raise ValueError("duplicate singular field")
    return matches[0] if matches else default


def blob(num, value):
    return varint((num << 3) | 2) + varint(len(value)) + value


def uid_bytes(uid):
    return b'\x09' + struct.pack('<Q', uid[0]) + b'\x11' + struct.pack('<Q', uid[1])


def parse_uid(data):
    lo, hi = one(data, 1, 1), one(data, 2, 1)
    if lo is None or hi is None:
        raise ValueError("missing UID")
    return struct.unpack('<Q', lo)[0], struct.unpack('<Q', hi)[0]


def request_frame(txn, uid, msgid, payload):
    # ClientRequest.request is a wrapper whose field 2 holds the actual message.
    pb = (blob(1, uid_bytes(uid)) + b'\x15' + struct.pack('<I', msgid)
          + blob(3, b'\x08\x01\x10\x00') + blob(4, blob(2, payload)))
    data = struct.pack('<H', len(pb)) + pb
    tlvs = b'\x10\x01\x00\x01' + b'\x01' + struct.pack('<H', len(data)) + data
    return struct.pack('<BHHH', 0, txn, 0x20, len(tlvs)) + tlvs


def qmi_decode(data):
    if len(data) < 7:
        raise ValueError("truncated QMI header")
    flags, txn, msgid, size = struct.unpack_from('<BHHH', data)
    if len(data) != size + 7:
        raise ValueError("QMI length mismatch")
    tlvs, pos = {}, 7
    while pos < len(data):
        if pos + 3 > len(data):
            raise ValueError("truncated TLV header")
        tag, size = struct.unpack_from('<BH', data, pos)
        pos += 3
        if pos + size > len(data) or tag in tlvs:
            raise ValueError("invalid TLV")
        tlvs[tag] = data[pos:pos + size]
        pos += size
    return flags, txn, msgid, tlvs


def events_from_frame(data):
    flags, txn, msgid, tlvs = qmi_decode(data)
    if flags == 2:  # QMI response, never interpret this as a sensor event.
        result = tlvs.get(2)
        if result is None or len(result) != 4:
            raise ValueError("missing QMI result")
        status, error = struct.unpack('<HH', result)
        if status:
            raise RuntimeError(f"QMI operation failed: {error}")
        response = tlvs.get(0x11)
        if response is not None and (len(response) != 4 or struct.unpack('<I', response)[0]):
            raise RuntimeError(f"SSC request rejected: {response.hex()}")
        return []
    if flags != 4 or msgid not in (0x21, 0x22):
        return []
    value = tlvs.get(2)
    if value is None or len(value) < 2:
        raise ValueError("missing event payload")
    size, = struct.unpack_from('<H', value)
    if size != len(value) - 2:
        raise ValueError("event protobuf length mismatch")
    pb = value[2:]
    uid = parse_uid(one(pb, 1, 2, b''))
    result = []
    for n, w, body in fields(pb):
        if (n, w) != (2, 2):
            continue
        mid, timestamp = one(body, 1, 5), one(body, 2, 1)
        if mid is None or timestamp is None:
            raise ValueError("missing event metadata")
        result.append((uid, struct.unpack('<I', mid)[0],
                       struct.unpack('<Q', timestamp)[0], one(body, 3, 2, b'')))
    return result


def attribute_values(data, depth=0):
    """Decode sns_std_attr_value oneof, preserving scalar types and subtypes."""
    if depth > 8:
        raise ValueError('attribute nesting limit')
    result = []
    for num, wire, entry in fields(data):
        if (num, wire) != (1, 2):
            continue
        for n, w, value in fields(entry):
            if (n, w) == (1, 2):
                result.append(attribute_values(value, depth + 1))
            elif (n, w) == (2, 2):
                result.append(value.rstrip(b'\0').decode('utf-8'))
            elif (n, w) == (3, 5):
                result.append(struct.unpack('<f', value)[0])
            elif (n, w) == (4, 1):
                result.append(struct.unpack('<q', value)[0])
            elif (n, w) == (5, 0):
                result.append(bool(value))
    return result


def registry_data(data, depth=0):
    if depth > 12:
        raise ValueError("registry nesting limit")
    out = []
    for n, w, item in fields(data):
        if (n, w) != (3, 2):
            continue
        row = {'name': one(item, 1, 2, b'').rstrip(b'\0').decode('utf-8')}
        version = one(item, 2, 5)
        if version is not None:
            row['version'] = struct.unpack('<I', version)[0]
        for key, wire, value in fields(item):
            if (key, wire) == (10, 2):
                row['items'] = registry_data(value, depth + 1)
            elif (key, wire) == (11, 2):
                row['str'] = value.rstrip(b'\0').decode('utf-8')
            elif (key, wire) == (12, 5):
                row['float'] = struct.unpack('<f', value)[0]
            elif (key, wire) == (13, 1):
                row['int'] = struct.unpack('<q', value)[0]
        out.append(row)
    return out


class Client:
    def __init__(self, trace=None):
        lookup = subprocess.run(['qrtr-lookup'], check=True, capture_output=True,
                                text=True, timeout=5).stdout
        endpoints = []
        for line in lookup.splitlines():
            cols = line.split()
            if len(cols) >= 5 and cols[0] == '400':
                endpoints.append((int(cols[3]), int(cols[4])))
        if len(endpoints) != 1:
            raise RuntimeError(f"expected one SSC service, found {endpoints}")
        self.endpoint = endpoints[0]
        self.sock = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
        self.sock.connect(self.endpoint)
        self.txn = 0
        self.trace = open(trace, 'x') if trace else None

    def close(self):
        self.sock.close()
        if self.trace:
            self.trace.close()

    def log(self, direction, frame):
        if self.trace:
            self.trace.write(json.dumps({'monotonic': time.monotonic(),
                                        'direction': direction, 'hex': frame.hex()}) + '\n')
            self.trace.flush()

    def query(self, uid, mid, payload, expected_mid, timeout=3):
        # The only transmitted operations are SUID discovery, attributes, registry READ.
        if mid not in (1, 512):
            raise ValueError("read-only operation allowlist")
        self.txn += 1
        frame = request_frame(self.txn, uid, mid, payload)
        self.log('send', frame)
        self.sock.send(frame)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.sock.settimeout(max(0.001, deadline - time.monotonic()))
            try:
                frame = self.sock.recv(65536)
            except socket.timeout:
                break
            self.log('recv', frame)
            flags, txn, qmi_msgid, _ = qmi_decode(frame)
            if flags == 2 and (txn != self.txn or qmi_msgid != 0x20):
                continue
            for event_uid, event_mid, timestamp, body in events_from_frame(frame):
                if event_uid == uid and event_mid == expected_mid:
                    return body
                if event_uid == uid and event_mid == 128 and expected_mid != 128:
                    raise RuntimeError(f"SSC error event: {body.hex()}")
        raise TimeoutError(f"no event {expected_mid} from {uid}")

    def discover(self, datatype=''):
        body = self.query(DISCOVERY, 512, blob(1, datatype.encode()) + b'\x10\x00\x18\x00', 768)
        return list(dict.fromkeys(parse_uid(v) for n, w, v in fields(body)
                                  if (n, w) == (2, 2) and parse_uid(v) != DISCOVERY))


def inventory(client):
    result = []
    for uid in client.discover():
        payload = client.query(uid, 1, b'\x10\x00', 128)
        attrs = {}
        for n, w, value in fields(payload):
            if (n, w) == (1, 2):
                attr_id = one(value, 1, 0)
                attrs[str(attr_id)] = {'values': attribute_values(one(value, 2, 2, b'')),
                                       'hex': value.hex()}
        def first(key):
            values = attrs.get(str(key), {}).get('values', [])
            return values[0] if values else None
        result.append({'uid': [f'{v:016x}' for v in uid], 'name': first(0),
                       'datatype': first(2), 'available': first(3),
                       'stream_type': first(16), 'attributes': attrs})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', help='create private JSONL wire trace (must not exist)')
    parser.add_argument('--registry', action='append', default=[], metavar='GROUP',
                        help='read effective registry group; no write operation exists')
    args = parser.parse_args()
    if any(not re.fullmatch(r'[A-Za-z0-9_.]{1,120}', g) for g in args.registry):
        parser.error('invalid registry group name')
    client = Client(args.trace)
    try:
        result = {'endpoint': client.endpoint,
                  'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
        if args.registry:
            uids = client.discover('registry')
            if len(uids) != 1:
                raise RuntimeError(f'expected one registry UID, got {uids}')
            result['registry'] = []
            for group in args.registry:
                body = client.query(uids[0], 512, blob(1, group.encode() + b'\0'), 514)
                name = one(body, 1, 2, b'').rstrip(b'\0').decode()
                if name != group:
                    raise ValueError(f'registry group mismatch: {name!r} != {group!r}')
                result['registry'].append({'requested': group, 'name': name,
                                          'items': registry_data(one(body, 2, 2, b''))})
        else:
            result['sensors'] = inventory(client)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    finally:
        client.close()


if __name__ == '__main__':
    main()
