"""Preference edits must keep the existing call and audio processes alive."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import cli,runtime,state,ui

def config(mode='native'):
 value=state.defaults(mode);value.update(host='192.0.2.44',installed=True);return value

class LivePreferences(unittest.TestCase):
 def test_unchanged_dialog_does_not_save_or_contact_station(self):
  c=config()
  with patch.object(ui,'text',side_effect=lambda title,default:default),patch.object(cli,'save') as save,patch.object(runtime,'update_preferences') as update:
   cli.settings(c,fields=('phrases',))
  save.assert_not_called();update.assert_not_called()

 def test_phrases_apply_without_checking_or_restarting_active_audio(self):
  c=config();old=copy.deepcopy(c);c['phrases']['open']='поговорим с гпт'
  with patch.object(runtime,'Station') as station,patch.object(runtime,'status') as status,patch.object(runtime,'start') as start,patch.object(runtime,'stop') as stop:
   runtime.update_preferences(c,old)
  status.assert_not_called();start.assert_not_called();stop.assert_not_called()
  station.return_value.put.assert_called_once_with(runtime.NATIVE+'/commands.txt',state.command_file(c['phrases']))
  station.return_value.run.assert_not_called()

 def test_native_led_updates_active_call_without_reopening_it(self):
  c=config();old=copy.deepcopy(c);c['led']='red'
  with patch.object(runtime,'Station') as station,patch.object(runtime,'status',return_value={'agent_connected':True,'phase':'active','led_style':'rainbow'}),patch.object(runtime,'native_action') as action,patch.object(runtime,'start') as start,patch.object(runtime,'stop') as stop:
   runtime.update_preferences(c,old)
  action.assert_called_once_with(c,'led','red');start.assert_not_called();stop.assert_not_called()
  self.assertEqual(station.return_value.put.call_count,2)
  self.assertEqual(dict(call.args for call in station.return_value.put.call_args_list),{
   runtime.NATIVE+'/runtime.env':runtime.native_environment(c),runtime.NATIVE+'/listen-led':b'0\n'})
  station.return_value.run.assert_not_called()

 def test_bridge_led_does_not_connect_over_ssh_or_close_browser(self):
  c=config('bridge');old=copy.deepcopy(c);c['led']='pulse-blue'
  with tempfile.TemporaryDirectory() as folder,patch.object(runtime,'HOME',Path(folder)),patch.object(runtime,'Station') as station,patch.object(runtime,'status') as status,patch.object(runtime,'bridge_command') as command:
   runtime.update_preferences(c,old)
   self.assertEqual(json.loads((Path(folder)/'bridge/runtime/preferences.json').read_text()),{'led':'pulse-blue'})
  station.return_value.run.assert_not_called();station.return_value.put.assert_not_called();status.assert_not_called();command.assert_not_called()

 def test_running_daemon_accepts_led_while_agent_is_connecting(self):
  c=config();old=copy.deepcopy(c);c['led']='blue'
  with patch.object(runtime,'Station'),patch.object(runtime,'status',return_value={'pid':1234,'agent_connected':False,'led_style':'rainbow'}),patch.object(runtime,'native_action') as action:
   runtime.update_preferences(c,old)
  action.assert_called_once_with(c,'led','blue')

 def test_failed_live_apply_restores_old_files_and_does_not_save_new_config(self):
  c=config();old=copy.deepcopy(c);c['led']='red';c['phrases']['open']='новая фраза'
  with patch.object(runtime,'Station') as station,patch.object(runtime,'status',return_value={'agent_connected':True,'led_style':'rainbow'}),patch.object(runtime,'native_action',side_effect=[RuntimeError('failed'),{'ok':True}]),patch.object(cli,'save') as save:
   with self.assertRaises(RuntimeError):cli.save_preferences(c,old)
  save.assert_not_called()
  writes={call.args[0]:call.args[1] for call in station.return_value.put.call_args_list}
  self.assertEqual(writes[runtime.NATIVE+'/runtime.env'],runtime.native_environment(old))
  self.assertEqual(writes[runtime.NATIVE+'/commands.txt'],state.command_file(old['phrases']))

 def test_old_daemon_requires_explicit_update_instead_of_hidden_restart(self):
  c=config();old=copy.deepcopy(c);c['led']='red'
  with patch.object(runtime,'Station') as station,patch.object(runtime,'status',return_value={'agent_connected':True}),patch.object(runtime,'stop') as stop:
   with self.assertRaisesRegex(RuntimeError,'обновите нативный клиент'):runtime.update_preferences(c,old)
  station.return_value.put.assert_not_called();stop.assert_not_called()

 def test_listening_toggle_only_writes_its_flag_without_querying_or_restarting_audio(self):
  c=config();old=copy.deepcopy(c);c['listen_led']=True
  with patch.object(runtime,'Station') as station,patch.object(runtime,'status') as status,patch.object(runtime,'start') as start,patch.object(runtime,'stop') as stop,patch.object(runtime,'native_action') as action:
   runtime.update_preferences(c,old)
  station.return_value.put.assert_called_once_with(runtime.NATIVE+'/listen-led',b'1\n')
  status.assert_not_called();start.assert_not_called();stop.assert_not_called();action.assert_not_called()

 def test_complete_led_off_also_disables_listening_wave_without_losing_preference(self):
  c=config();c.update(listen_led=True,led='off')
  self.assertEqual(runtime.listening_preference(c),b'0\n')
  c['led']='rainbow';self.assertEqual(runtime.listening_preference(c),b'1\n')

 def test_old_configuration_defaults_to_dark_pauses_and_menu_toggle_saves(self):
  from alisa_cli import menus
  c=config();c.pop('listen_led',None)
  with tempfile.TemporaryDirectory() as folder,patch.object(state,'HOME',Path(folder)):
   state.save(c);loaded=state.load('native');self.assertFalse(loaded['listen_led'])
   self.assertIn(('[ ] Подсветка в паузах GPT',('settings','listen-on')),menus.entries('native','settings',loaded))
  with patch.object(cli,'load',return_value=c),patch.object(runtime,'update_preferences') as apply,patch.object(cli,'save') as save:
   self.assertEqual(cli.main(['native','settings','listen-on']),0)
  self.assertTrue(c['listen_led']);save.assert_called_once_with(c);apply.assert_called_once()
