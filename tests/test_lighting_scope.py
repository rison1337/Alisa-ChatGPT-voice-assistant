"""Optional listening effects preserve saved lighting and active conversations."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alisa_cli import cli,lighting,menus,runtime,state,ui


def config():
    value=state.defaults('native');value.update(host='192.0.2.44',model='mini2',installed=True)
    return value


class LightingScope(unittest.TestCase):
    def test_old_settings_keep_speaking_default_and_optional_cyan_wave(self):
        for listen in (False,True):
            value=config();value.pop('led_scope');value['listen_led']=listen
            with tempfile.TemporaryDirectory() as folder,patch.object(state,'HOME',Path(folder)):
                state.save(value);loaded=state.load('native')
            self.assertEqual(loaded['led_scope'],'speaking');self.assertEqual(loaded['listen_led'],listen)
            self.assertEqual(loaded['led'],'rainbow')
        self.assertEqual(state.defaults('native')['led_scope'],'speaking')
        value['led_scope']='unknown'
        with self.assertRaises(ValueError):state.validate(value)

    def test_menu_groups_scope_and_effect_and_retains_legacy_toggle(self):
        value=config()
        self.assertIn(('Подсветка','@lighting'),menus.entries('native','settings',value))
        entries=menus.entries('native','lighting',value)
        self.assertIn(('Цвет и анимация',('settings','led')),entries)
        self.assertTrue(any(target==('settings','led-scope') for _,target in entries))
        self.assertTrue(any(target==('settings','listen-on') for _,target in entries))
        value['led_scope']='active'
        self.assertFalse(any(target==('settings','listen-on') for _,target in menus.entries('native','lighting',value)))
        self.assertIn(('Подсветка',('settings','led')),menus.entries('bridge','settings',value))

    def test_scope_picker_marks_legacy_default_and_keeps_current_choice(self):
        value=config();value['led_scope']='active'
        with patch.object(ui,'select',return_value='speaking') as select:
            self.assertEqual(lighting.choose_scope(value),'speaking')
        self.assertEqual(select.call_args.kwargs['default'],'active')
        self.assertIn(('Только во время ответа (по умолчанию)','speaking'),select.call_args.args[1])

    def test_dashboard_returns_to_lighting_after_scope_change(self):
        value=config();views=[]
        def dashboard(**view):
            views.append(view)
            if view['title']=='Настройки':return '@lighting'
            if value['led_scope']=='speaking':return ('settings','led-scope')
            self.assertIn('прослушивания',view['items'][1][0]);return 'back'
        def execute(action,operation):
            self.assertEqual((action,operation),('settings','led-scope'))
            value['led_scope']='active';return 0
        # End once Back returns from lighting to settings.
        def finite_dashboard(**view):
            if len(views)==3:return 'back'
            return dashboard(**view)
        with patch.object(menus,'load',return_value=value),patch.object(ui,'interactive',return_value=False),\
             patch.object(ui,'dashboard',side_effect=finite_dashboard):
            self.assertEqual(menus.run('native',execute,start_page='settings'),0)
        self.assertEqual([view['title'] for view in views],['Настройки','Подсветка','Подсветка'])
        self.assertEqual(views[-1]['default'],('settings','led-scope'))

    def test_cli_scope_toggle_preserves_selected_effect_and_wave(self):
        value=config();value['listen_led']=True
        for scope in ('active','speaking'):
            with patch.object(cli,'load',return_value=value),patch.object(cli,'save') as save,\
                 patch.object(runtime,'update_preferences') as update,patch.object(ui,'note'):
                self.assertEqual(cli.main(['native','settings','--led-scope',scope]),0)
            self.assertEqual(value['led_scope'],scope);self.assertEqual(value['led'],'rainbow')
            self.assertTrue(value['listen_led']);save.assert_called_once();update.assert_called_once()

    def test_scope_only_writes_atomic_preference_without_audio_commands(self):
        value=config();old=copy.deepcopy(value);value['led_scope']='active'
        with patch.object(runtime,'Station') as station,patch.object(runtime,'status') as status,\
             patch.object(runtime,'native_action') as action,patch.object(runtime,'start') as start,patch.object(runtime,'stop') as stop:
            runtime.update_preferences(value,old)
        station.return_value.put.assert_called_once_with(runtime.NATIVE+'/led-scope',b'active rainbow\n')
        station.return_value.run.assert_not_called();status.assert_not_called();action.assert_not_called()
        start.assert_not_called();stop.assert_not_called()

    def test_failed_scope_write_restores_previous_preference_and_does_not_save(self):
        value=config();old=copy.deepcopy(value);value['led_scope']='active'
        with patch.object(runtime,'Station') as station,patch.object(cli,'save') as save:
            station.return_value.put.side_effect=[RuntimeError('failed'),None]
            with self.assertRaises(RuntimeError):cli.save_preferences(value,old)
        self.assertEqual(station.return_value.put.call_args.args,(runtime.NATIVE+'/led-scope',b'speaking rainbow\n'))
        save.assert_not_called()

    def test_full_apply_writes_scope_and_selected_effect_together(self):
        value=config();value.update(led_scope='active',led='pulse-purple')
        with patch.object(runtime,'Station') as station:runtime.apply_settings(value)
        writes=dict(call.args for call in station.return_value.put.call_args_list)
        self.assertEqual(writes[runtime.NATIVE+'/led-scope'],b'active pulse-purple\n')
        value['led']='off';self.assertEqual(runtime.lighting_scope_preference(value),b'active off\n')

    def test_scope_change_does_not_modify_an_uninstalled_device(self):
        value=config();old=copy.deepcopy(value);value.update(installed=False,led_scope='active')
        with patch.object(runtime,'Station') as station:runtime.update_preferences(value,old)
        station.assert_not_called()
