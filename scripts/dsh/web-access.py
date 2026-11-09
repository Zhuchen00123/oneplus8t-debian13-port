#!/usr/bin/env python3
"""Obtain the current launch URL without copying journal credentials to public files."""
import http.cookiejar
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request


def launch_url():
    pid = subprocess.check_output(
        ['systemctl', 'show', 'kebab-dsh.service', '-p', 'MainPID', '--value'], text=True
    ).strip()
    if not pid.isdigit() or pid == '0':
        raise SystemExit('DSH service is not running')
    logs = subprocess.check_output(
        ['journalctl', '-u', 'kebab-dsh.service', f'_PID={pid}', '-n', '200', '-o', 'cat', '--no-pager'],
        text=True,
    )
    matches = re.findall(r'dsh web: (http://127\.0\.0\.1:3080/\?token=[A-Za-z0-9_-]+)', logs)
    if not matches:
        raise SystemExit('Current launch URL was not found')
    return matches[-1]


def verify():
    url = launch_url()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    try:
        urllib.request.urlopen('http://127.0.0.1:3080/', timeout=10)
    except urllib.error.HTTPError as error:
        unauthenticated = error.code
    else:
        unauthenticated = 200
    with opener.open(url, timeout=15) as response:
        body = response.read()
        result = {'unauthenticated_status': unauthenticated, 'authenticated_status': response.status,
                  'html_bytes': len(body), 'content_type': response.headers.get('Content-Type')}
    payload = json.dumps({'type': 'client-request', 'rpcId': 'phase1-check',
                          'method': 'settings/describe', 'payload': {'args': {}}}).encode()
    request = urllib.request.Request('http://127.0.0.1:3080/api/settings/describe', data=payload,
                                    headers={'Content-Type': 'application/json'})
    with opener.open(request, timeout=15) as response:
        data = json.load(response)
        result['settings_status'] = response.status
        result['settings_ok'] = data.get('result', {}).get('ok') is True
    if unauthenticated != 401 or result['authenticated_status'] != 200 or not result['settings_ok']:
        raise SystemExit('Web authentication verification failed')
    print(json.dumps(result))


if __name__ == '__main__':
    if sys.argv[1:] == ['url']:
        print(launch_url())  # Explicit local user action; treat the output as a credential.
    elif sys.argv[1:] == ['verify']:
        verify()
    else:
        raise SystemExit('Usage: web-access.py url|verify')
