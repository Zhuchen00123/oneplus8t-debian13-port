#!/usr/bin/env python3
"""Publish only addresses and local service state for the unprivileged panel.

No network probes, credentials in output, or changes to existing services.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import time


UNITS = ('kebab-strelay-controller.service', 'kebab-strelay-ssh.service',
         'kebab-strelay-http.service', 'kebab-mcbridge-client.service',
         'kebab-cnb-keepalive.timer', 'kebab-portal-keeper.timer')


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=3, check=True)
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def addresses(raw):
    result = {'ipv4': [], 'ipv6': []}
    try:
        for interface in json.loads(raw):
            if interface.get('ifname') != 'wlan0':
                continue
            for entry in interface.get('addr_info', []):
                if entry.get('scope') != 'global' or entry.get('preferred_life_time') == 0:
                    continue
                if any(flag in entry.get('flags', []) for flag in ('tentative', 'dadfailed', 'deprecated')):
                    continue
                address = ipaddress.ip_address(entry['local'])
                if not (address.is_link_local or address.is_loopback or address.is_unspecified):
                    result['ipv' + str(address.version)].append(str(address))
    except (ValueError, TypeError, KeyError, AttributeError):
        return {'ipv4': [], 'ipv6': []}
    return {key: sorted(set(values)) for key, values in result.items()}


def relay_status(path=Path('/srv/kebab-strelay/private/status.json'), now=None):
    now = time.time() if now is None else now
    try:
        value = json.loads(path.read_text())
        checked = value.get('checked_at')
        url = value.get('https_url')
        if (value.get('ready') is True and type(checked) in (int, float)
                and 0 <= now - checked <= 180 and isinstance(url, str)
                and re.fullmatch(r'https://[A-Za-z0-9-]+-9003\.cnb\.run/', url)):
            return {'https_url': url, 'checked_at': checked}
    except (OSError, ValueError, AttributeError):
        pass
    return None


def mc_address(path=Path('/etc/kebab-mcbridge-client.env')):
    # Parse only two exact keys. Never source this file or publish the shared secret.
    try:
        fields = {}
        for line in path.read_text().splitlines():
            key, separator, value = line.partition('=')
            if separator and key in ('MCB_HOST', 'MCB_PORT'):
                fields[key] = value.strip().strip('\"\'')
        host, port = fields.get('MCB_HOST', ''), fields.get('MCB_PORT', '443')
        if re.fullmatch(r'[A-Za-z0-9-]+\.cnb\.run', host) and port.isdecimal() and 1 <= int(port) <= 65535:
            return f'{host}:{int(port)}'
    except OSError:
        pass
    return None


def service_states(raw):
    result = {}
    for block in raw.strip().split('\n\n'):
        values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        unit, state = values.get('Id'), values.get('ActiveState')
        if unit in UNITS and state in ('active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading'):
            result[unit] = state
    return result


def collect():
    return {'checked_at': time.time(),
            'campus': addresses(command(['ip', '-j', 'address', 'show', 'dev', 'wlan0'])),
            'services': service_states(command(['systemctl', 'show', *UNITS, '-p', 'Id', '-p', 'ActiveState'])),
            'sillytavern': relay_status(), 'mc_address': mc_address()}


def main():
    path = Path('/run/kebab-panel-network/status.json')
    path.parent.mkdir(mode=0o755, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(collect(), ensure_ascii=False) + '\n')
    os.chmod(temporary, 0o644)
    temporary.replace(path)


if __name__ == '__main__':
    main()
