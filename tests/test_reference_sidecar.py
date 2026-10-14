import tempfile,unittest
from unittest.mock import Mock
from app.reference_sidecar import DurableReferenceTunnel
class SidecarTests(unittest.TestCase):
 def test_client_close_does_not_revoke_lease(self):
  with tempfile.TemporaryDirectory() as root:
   client=DurableReferenceTunnel(root);client._rpc=Mock(return_value={'active':True})
   client.close();self.assertTrue(client.active_reference('https://example.test/reference.png'))
   self.assertEqual(client._rpc.call_args.args[0],'active')
 def test_disable_explicitly_revokes(self):
  with tempfile.TemporaryDirectory() as root:
   client=DurableReferenceTunnel(root);client._rpc=Mock(return_value={});client.disable();client._rpc.assert_called_once_with('disable')
 def test_disabled_never_starts_publication(self):
  from app.core import UserError
  with tempfile.TemporaryDirectory() as root:
   client=DurableReferenceTunnel(root);client._start=Mock()
   with self.assertRaises(UserError):client.publish(b'test','0'*64)
   client._start.assert_not_called()
