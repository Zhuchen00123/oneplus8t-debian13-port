import unittest
from waydroid_lab_network import policy_rule_argv, BRIDGE, SUBNET, POLICY

class PolicyTests(unittest.TestCase):
    def test_only_bridge_ingress_is_routed(self):
        self.assertEqual(policy_rule_argv('add'),['ip','rule','add','pref',POLICY,'iif',BRIDGE,'from',str(SUBNET),'lookup',POLICY])
    def test_owned_delete_matches_creation(self):
        self.assertEqual(policy_rule_argv('add')[3:],policy_rule_argv('del')[3:])
        self.assertNotIn('iif',policy_rule_argv('del',False))
    def test_no_rule_flush(self):
        with self.assertRaises(ValueError):policy_rule_argv('flush')

if __name__=='__main__':unittest.main()
