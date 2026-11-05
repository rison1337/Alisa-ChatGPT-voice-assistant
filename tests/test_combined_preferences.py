"""Mini 3 display and optional listening lighting must survive each other."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from alisa_cli import cli,menus,runtime,state
import test_mini3_ring as ring_fixture


class CombinedPreferences(unittest.TestCase):
    def config(self):
        value=state.defaults('native')
        value.update(host='192.0.2.44',installed=True,model='bergamot',display='eyes',led_scope='active',disable_stun=True)
        return value

    def test_menu_exposes_display_and_scope_without_duplicate_listening_toggle(self):
        value=self.config()
        entries=menus.entries('native','settings',value)
        self.assertIn(('Подсветка','@lighting'),entries)
        self.assertIn(('Дисплей GPT',('settings','display')),entries)
        self.assertFalse(any(target in (('settings','listen-on'),('settings','listen-off')) for _,target in entries))
        lighting=menus.entries('native','lighting',value)
        self.assertTrue(any(target==('settings','led-scope') for _,target in lighting))
        self.assertFalse(any(target in (('settings','listen-on'),('settings','listen-off')) for _,target in lighting))

    def test_display_changes_keep_scope_and_stun_without_audio_restart(self):
        value=self.config();before=copy.deepcopy(value);value['display']='clock'
        with patch.object(runtime,'Station') as station,patch.object(runtime,'start') as start,patch.object(runtime,'stop') as stop,patch.object(runtime,'native_action') as action,patch.object(cli,'save') as save:
            cli.save_preferences(value,before)
        station.return_value.put.assert_called_once_with(runtime.NATIVE+'/display-mode',b'clock\n')
        save.assert_called_once_with(value)
        self.assertEqual(value['led_scope'],'active');self.assertTrue(value['disable_stun'])
        for mock in (start,stop,action):mock.assert_not_called()

    def test_failure_after_display_write_restores_both_preferences(self):
        value=self.config();before=copy.deepcopy(value)
        value.update(display='clock',led_scope='speaking')
        writes={runtime.NATIVE+'/display-mode':b'eyes\n',runtime.NATIVE+'/led-scope':b'active rainbow\n'}
        attempts=[]
        def put(path,data):
            attempts.append((path,data))
            if len(attempts)==2:raise OSError('simulated second-write failure')
            writes[path]=data
        with patch.object(runtime,'Station') as station,patch.object(cli,'save') as save:
            station.return_value.put.side_effect=put
            with self.assertRaisesRegex(OSError,'second-write'):cli.save_preferences(value,before)
        self.assertEqual(writes,{runtime.NATIVE+'/display-mode':b'eyes\n',runtime.NATIVE+'/led-scope':b'active rainbow\n'})
        save.assert_not_called()

    def test_full_apply_keeps_display_scope_and_vpn_guard_together(self):
        value=self.config();value['vpn'].update(enabled=True)
        with patch.object(runtime,'Station') as station:runtime.apply_settings(value)
        writes=dict(call.args for call in station.return_value.put.call_args_list)
        self.assertEqual(writes[runtime.NATIVE+'/display-mode'],b'eyes\n')
        self.assertEqual(writes[runtime.NATIVE+'/led-scope'],b'active rainbow\n')
        self.assertIn(b'ALISA_DISABLE_STUN=1',writes[runtime.NATIVE+'/runtime.env'])
        self.assertIn(b'ALISA_ICE_BIND_ADDRESS=172.31.254.1',writes[runtime.NATIVE+'/runtime.env'])
        self.assertEqual(writes[runtime.TOOLS+'/vpn-required'],b'required\n')


@unittest.skipUnless(sys.platform=='linux','file-backed shell fixture')
class CombinedMini3Ring(unittest.TestCase):
    def test_listening_scope_changes_live_and_mute_keeps_its_warning(self):
        fixture=ring_fixture.Mini3Ring()
        # The production script reads an atomically replaced file for every
        # silent frame; no daemon or helper restart should be necessary.
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'ALISA_LED_SCOPE_FILE':str(Path(folder)/'scope')}):
            with fixture.ring() as (process,fb,gpio,flag,env):
                scope=Path(folder)/'scope'
                fixture.send(process,'F 0 0 0 0 0 0 0 0 0')
                self.assertEqual(fb.read_bytes(),bytes(18))
                for preference,color in (('active green',bytes((0,160,0))*6),
                                         ('active blue',bytes((160,0,0))*6),
                                         ('active off',bytes(18)),('speaking rainbow',bytes(18))):
                    staged=scope.with_suffix('.new');staged.write_text(preference+'\n');staged.replace(scope)
                    fixture.send(process,'F 0 0 0 0 0 0 0 0 0')
                    self.assertEqual(fb.read_bytes(),color)
                warning=bytes((0,0,80))*6;fb.write_bytes(warning)
                gpio.write_text(' gpio-505 ( |mute-switch ) in lo\n')
                scope.write_text('active rainbow\n')
                fixture.send(process,'F 0 0 0 0 0 0 0 0 0',expected='STOCK')
                self.assertEqual(fb.read_bytes(),warning)
                self.assertIsNone(process.poll())


if __name__=='__main__':unittest.main()
