import json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import state,runtime
from alisa_cli.errors import PublicError

class NativeStatus(unittest.TestCase):
 def test_ssh_failure_is_never_reported_as_stopped_or_logged_out(self):
  c=state.defaults('native');c['host']='192.0.2.44'
  for error in (b'No route to host',b'Permission denied',b'Host key verification failed'):
   response=subprocess.CompletedProcess([],255,stdout=b'',stderr=error)
   with self.subTest(error=error),patch.object(runtime.Station,'run',return_value=response):
    with self.assertRaises(PublicError):runtime.status(c)

 def test_controller_unavailable_over_working_ssh_is_stopped(self):
  c=state.defaults('native');c['host']='192.0.2.44'
  response=subprocess.CompletedProcess([],2,stdout=b'{"error":"control_unavailable","ok":false}',stderr=b'')
  with patch.object(runtime.Station,'run',return_value=response):result=runtime.status(c)
  self.assertEqual(result['phase'],'stopped');self.assertFalse(result['agent_connected'])
  self.assertNotIn('login_saved',result)

 def test_invalid_success_reply_does_not_allow_update(self):
  c=state.defaults('native');c['host']='192.0.2.44'
  for output in (b'',b'[]',b'{}',b'not JSON'):
   response=subprocess.CompletedProcess([],0,stdout=output,stderr=b'')
   with self.subTest(output=output),patch.object(runtime.Station,'run',return_value=response):
    with self.assertRaises(PublicError):runtime.status(c)

class BridgeStatus(unittest.TestCase):
 def test_dead_bridge_is_stopped_and_old_voice_error_is_not_current(self):
  c=state.defaults('bridge');c['host']='192.0.2.44'
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'bridge/runtime/voice.json';p.parent.mkdir(parents=True);p.write_text(json.dumps({'pid':0,'error':'browser_verification_required'}))
   response=subprocess.CompletedProcess([],0,stdout=b'{"phase":"running","process_alive":false}',stderr=b'')
   with patch.object(runtime,'HOME',Path(folder)),patch.object(runtime,'bridge_command',return_value=response):result=runtime.status(c)
  self.assertEqual(result['phase'],'stopped');self.assertNotIn('error',result)
