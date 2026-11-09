#!/usr/bin/python3
"""Git reads the existing CNB credential without placing it in URLs or argv."""
import json, pathlib, sys
if 'username' in sys.argv[1].lower():
    print('cnb')
else:
    print(json.loads(pathlib.Path('/srv/kebab-strelay/private/private.json').read_text())['cnb_token'])
