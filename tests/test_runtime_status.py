import json,shutil,subprocess,tempfile,unittest
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

 def test_missing_client_on_a_fresh_station_is_stopped_not_an_error(self):
  c=state.defaults('native');c['host']='192.0.2.44'
  response=subprocess.CompletedProcess([],0,stdout=b'{"phase":"stopped","agent_connected":false,"error":""}',stderr=b'')
  with patch.object(runtime.Station,'run',return_value=response):result=runtime.status(c)
  self.assertEqual(result['phase'],'stopped');self.assertFalse(result['agent_connected'])

 @unittest.skipUnless(shutil.which('sh'), 'POSIX shell probe')
 def test_remote_probe_distinguishes_absence_from_a_broken_installed_client(self):
  c=state.defaults('native');c['host']='192.0.2.44'
  with tempfile.TemporaryDirectory(prefix='native status ') as folder:
   base=Path(folder);binary=base/'bin/alisa-standalone';binary.parent.mkdir()
   def run(command,**kwargs):
    return subprocess.run(['sh','-c',command],capture_output=True)
   with patch.object(runtime,'NATIVE',str(base)),patch.object(runtime.Station,'run',side_effect=run):
    self.assertEqual(runtime.status(c)['phase'],'stopped')
    binary.write_text('#!/bin/sh\necho "error while loading shared libraries" >&2\nexit 127\n');binary.chmod(0o700)
    with self.assertRaisesRegex(RuntimeError,'127'):runtime.status(c)
    binary.unlink();binary.symlink_to(base/'missing-loader')
    with self.assertRaisesRegex(RuntimeError,'127'):runtime.status(c)

 def test_state_unavailable_during_first_start_is_stopped(self):
  c=state.defaults('native');c['host']='192.0.2.44'
  response=subprocess.CompletedProcess([],2,stdout=b'{"error":"state_unavailable","ok":false}',stderr=b'')
  with patch.object(runtime.Station,'run',return_value=response):result=runtime.status(c)
  self.assertEqual(result['phase'],'stopped');self.assertFalse(result['agent_connected'])

 def test_sticky_state_unavailable_keeps_the_live_daemon_reply(self):
  c=state.defaults('native');c['host']='192.0.2.44'
  response=subprocess.CompletedProcess([],0,stdout=b'{"phase":"idle","agent_connected":true,"error":"state_unavailable"}',stderr=b'')
  with patch.object(runtime.Station,'run',return_value=response):result=runtime.status(c)
  self.assertEqual(result['phase'],'idle');self.assertTrue(result['agent_connected'])

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
