#!/usr/bin/env python3
"""Offline node-lease tests; no host devices or services are touched."""
import json
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest import mock
import lab_control as control

class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.node=self.root/'fixture-node';self.node.write_text('fixture')
        (self.root/'host-node-baseline.json').write_text(json.dumps({str(self.node):{'mode':0o600}}))
        self.patch=mock.patch.object(control,'ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)
    def read_wrapper(self, original):
        def read(path,*a,**kw):
            if path.as_posix()=='/proc/sys/kernel/random/boot_id':return 'fixture-current-boot'
            return original(path,*a,**kw)
        return read
    def test_adb_newline_and_comment_are_not_key_drift(self):
        self.assertEqual(control.normalized_adb_keys('own-key original-comment'),control.normalized_adb_keys('own-key android-comment\n'))
    def test_adb_foreign_material_not_accepted(self):
        self.assertNotEqual(control.normalized_adb_keys('own-key comment'),control.normalized_adb_keys('foreign-key comment\n'))
    def test_real_device_required(self):
        with self.assertRaisesRegex(RuntimeError,'not a real character device'):
            control.snapshot_nodes()
        self.assertFalse((self.root/'active-attempt.json').exists())
    def test_new_attempt_and_idempotent_restore(self):
        actual_stat=Path.stat;actual_read=Path.read_text;mode={'value':0o600}
        def node_stat(path,*a,**kw):
            if path==self.node:return types.SimpleNamespace(st_mode=stat.S_IFCHR|mode['value'],st_uid=0,st_gid=0,st_rdev=258)
            return actual_stat(path,*a,**kw)
        def chmod(path,value):
            self.assertEqual(Path(path),self.node);mode['value']=value
        with mock.patch.object(Path,'stat',node_stat),mock.patch.object(Path,'read_text',self.read_wrapper(actual_read)),mock.patch.object(control.os,'listxattr',return_value=[],create=True),mock.patch.object(control.os,'chmod',side_effect=chmod) as change:
            attempt=control.snapshot_nodes();data=json.loads((attempt/'node-baseline.json').read_text())
            self.assertEqual(data['boot_id'],'fixture-current-boot');self.assertEqual(data['nodes'][str(self.node)]['mode'],0o600);change.assert_not_called()
            with self.assertRaisesRegex(RuntimeError,'unfinished previous node lease'):control.snapshot_nodes()
            mode['value']=0o666;control.restore_nodes();self.assertEqual(mode['value'],0o600);self.assertEqual(change.call_count,1)
            self.assertTrue(json.loads((attempt/'nodes-restored.json').read_text())['verified']);self.assertEqual(json.loads((self.root/'active-attempt.json').read_text())['state'],'restored')
            control.restore_nodes();self.assertEqual(change.call_count,1)
            next_attempt=control.snapshot_nodes();self.assertNotEqual(next_attempt,attempt)
    def test_lease_path_escape_refused(self):
        (self.root/'active-attempt.json').write_text(json.dumps({'state':'leased','path':'../outside'}))
        with self.assertRaisesRegex(RuntimeError,'outside experiment run directory'):control.restore_nodes()
    def test_hold_preempts_every_external_operation(self):
        (self.root/'HOLD_UNEXPECTED_RESET').write_text('fixture')
        with mock.patch.object(control,'run') as run,mock.patch.object(control,'resource_guard') as guard:
            with self.assertRaisesRegex(RuntimeError,'unexpected host reset'):control.start()
            run.assert_not_called();guard.assert_not_called()

if __name__=='__main__':unittest.main()
