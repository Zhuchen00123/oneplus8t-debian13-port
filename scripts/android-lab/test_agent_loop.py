#!/usr/bin/env python3
import unittest
from agent_loop import find_control, PACKAGE, resolve_serial
from pathlib import Path
import tempfile
import json

def xml(**changes):
    attrs = {'resource-id':PACKAGE+':id/input_text','package':PACKAGE,'enabled':'true','bounds':'[10,20][110,80]','text':'fixture'}
    attrs.update(changes)
    return '<hierarchy><node '+ ' '.join(k+'="'+v+'"' for k,v in attrs.items())+'/></hierarchy>'

class LocatorTests(unittest.TestCase):
    def test_exact_id_center(self):
        n, xy=find_control(xml(),'input_text'); self.assertEqual(xy,(60,50));self.assertEqual(n.get('text'),'fixture')
    def test_wrong_package(self):
        with self.assertRaises(ValueError):find_control(xml(package='third.party'),'input_text')
    def test_disabled(self):
        with self.assertRaises(ValueError):find_control(xml(enabled='false'),'input_text')
    def test_missing(self):
        with self.assertRaises(ValueError):find_control(xml(),'other')
    def test_empty_bounds(self):
        with self.assertRaises(ValueError):find_control(xml(bounds='[10,20][10,80]'),'input_text')
    def test_bad_bounds(self):
        with self.assertRaises(ValueError):find_control(xml(bounds='garbage'),'input_text')
    def test_unreasonable_bounds(self):
        with self.assertRaises(ValueError):find_control(xml(bounds='[10,20][9000,80]'),'input_text')
    def test_duplicate(self):
        x=xml();x=x.replace('</hierarchy>',x.removeprefix('<hierarchy>'))
        with self.assertRaises(ValueError):find_control(x,'input_text')
    def test_no_doctype(self):
        with self.assertRaises(ValueError):find_control('<!DOCTYPE x>'+xml(),'input_text')
    def test_size_limit(self):
        with self.assertRaises(ValueError):find_control('x'*2000001,'input_text')

class StateTests(unittest.TestCase):
    def test_same_boot_verified_auto(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'boot').write_text('fixture');(p/'state').write_text(json.dumps({'boot_id':'fixture','adb_authenticated':True,'serial':'192.168.240.2:5555'}))
            self.assertEqual(resolve_serial('auto',p/'state',p/'boot'),'192.168.240.2:5555')
    def test_stopped_same_boot_state_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'boot').write_text('fixture');(p/'state').write_text(json.dumps({'boot_id':'fixture','adb_authenticated':False,'serial':'192.168.240.2:5555'}))
            with self.assertRaises(RuntimeError):resolve_serial('auto',p/'state',p/'boot')
    def test_stale_boot_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'boot').write_text('new');(p/'state').write_text(json.dumps({'boot_id':'old','adb_authenticated':True,'serial':'192.168.240.2:5555'}))
            with self.assertRaises(RuntimeError):resolve_serial('auto',p/'state',p/'boot')
    def test_missing_state_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            with self.assertRaises(RuntimeError):resolve_serial('auto',p/'state',p/'boot')

if __name__=='__main__':unittest.main()
