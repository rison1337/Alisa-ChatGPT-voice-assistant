"""Guided setup branches and diagnostics shown to the installer user."""
import contextlib,io,json,unittest
from unittest.mock import patch
from alisa_cli import cli,devices,menus,state,storage,ui

def config(mode='native'):
    value=state.defaults(mode);value['host']='192.0.2.44';return value

class InstallationFlow(unittest.TestCase):
    def test_saved_vpn_is_reused_without_requesting_credentials(self):
        value=config();value['vpn'].update(enabled=True,engine='xray')
        prompts=[]
        def select(title,items,default=None):prompts.append(title);return default
        with patch.object(ui,'select',side_effect=select),patch.object(ui,'confirm',side_effect=lambda title,default=False:default),patch.object(ui,'note'),patch.object(ui,'checklist') as checklist,patch.object(ui,'secret') as secret,patch.object(cli.vpn,'select_server') as servers,patch.object(cli,'save') as save:
            options=cli.installation_options(value,{'country':'DE'})
        self.assertTrue(options['use_vpn']);self.assertIsNone(options['source'])
        secret.assert_not_called();servers.assert_not_called();checklist.assert_not_called();save.assert_not_called()
        self.assertTrue(any('фразы' in prompt for prompt in prompts));self.assertIn('Подсветка',prompts)

    def test_russian_ip_recommends_setup_and_vpn_autostart_follows_gpt(self):
        value=config();value['autostart']=True
        selection={'selected':{'_engine':'xray'}}
        with patch.object(ui,'select',side_effect=lambda title,items,default=None:default),patch.object(ui,'confirm',side_effect=lambda title,default=False:default),patch.object(ui,'secret',return_value='vless://test') as secret,patch.object(ui,'note'),patch.object(cli.vpn,'select_server',return_value=selection):
            options=cli.installation_options(value,{'country':'RU'})
        self.assertTrue(options['use_vpn']);self.assertTrue(options['vpn_autostart'])
        self.assertIs(options['selection'],selection);secret.assert_called_once()

    def test_bridge_always_offers_phrases_and_led_without_vpn(self):
        value=config('bridge');answers=iter(['открой разговор','закрой разговор','новый разговор','предыдущий разговор'])
        def select(title,items,default=None):return 'change' if 'фразы' in title else 'red' if title=='Подсветка' else default
        with patch.object(ui,'select',side_effect=select),patch.object(ui,'text',side_effect=lambda *a:next(answers)),patch.object(ui,'confirm',return_value=False),patch.object(ui,'note'),patch.object(ui,'secret') as secret,patch.object(cli.vpn,'select_server') as servers:
            options=cli.installation_options(value,{'country':'RU'})
        self.assertFalse(options['use_vpn']);self.assertFalse(options['vpn_autostart'])
        self.assertEqual(value['led'],'red');self.assertEqual(value['phrases']['open'],'открой разговор')
        secret.assert_not_called();servers.assert_not_called()

    def test_wizard_preserves_saved_vpn_and_counts_its_engine(self):
        value=config();value['vpn'].update(enabled=True,engine='xray')
        report={'ok':True,'host_missing':[]}
        options={'use_vpn':True,'source':None,'selection':None,'vpn_autostart':False}
        with patch.object(cli,'setup_key'),patch.object(cli.doctor,'inspect',return_value=report),patch.object(cli.doctor,'require'),patch.object(cli.network,'check',return_value={'country':'DE'}),patch.object(cli,'installation_options',return_value=options),patch.object(cli,'yes',side_effect=lambda title,default=False:title.startswith('Применить')),patch.object(cli,'show'),patch.object(cli,'show_storage'),patch.object(cli,'save'),patch.object(cli,'start_mode'),patch.object(cli.runtime,'status',return_value={'login_saved':True}),patch.object(cli.autostart,'set_enabled'),patch.object(cli.autostart,'station_flag'),patch('alisa_cli.install.install',return_value={}),patch.object(storage,'report',return_value={'enough':True}) as space,patch.object(cli.vpn,'operate') as operate,patch.object(cli.vpn,'configure') as configure,patch.object(ui,'note'),patch.object(ui,'step'):
            cli.wizard(value)
        space.assert_called_once_with(value,vpn_engine='xray')
        operate.assert_not_called();configure.assert_not_called();self.assertTrue(value['vpn']['enabled'])

    def test_repeat_wizard_explains_update_and_does_not_request_login_by_default(self):
        value=config();value['installed']=True;prompts=[];messages=[]
        def yes(title,default=False):prompts.append((title,default));return title.startswith('Применить')
        with patch.object(cli,'setup_key'),patch.object(cli.doctor,'inspect',return_value={'ok':True,'host_missing':[]}),patch.object(cli.doctor,'require'),patch.object(cli.network,'check',return_value={'country':'DE'}),patch.object(cli,'installation_options',return_value={'use_vpn':False,'source':None,'selection':None,'vpn_autostart':False}),patch.object(cli,'yes',side_effect=yes),patch.object(cli,'show'),patch.object(cli,'show_storage'),patch.object(cli,'save'),patch.object(cli,'start_mode',return_value={'login_saved':True}),patch.object(cli.autostart,'set_enabled'),patch.object(cli.autostart,'station_flag'),patch('alisa_cli.install.install',return_value={'installed':True}),patch.object(storage,'report',return_value={'enough':True}),patch.object(ui,'note',side_effect=lambda text,*args:messages.append(text)),patch.object(ui,'step'):
            cli.wizard(value)
        self.assertTrue(any('Уже установлено' in text for text in messages))
        self.assertIn(('Применить настройки и обновить установленную программу?',True),prompts)
        self.assertIn(('Обновить вход ChatGPT?',False),prompts)
        self.assertTrue(any('Обновление завершено' in text for text in messages))

    def test_wizard_returns_to_menu_when_optional_voice_test_fails(self):
        value=config();messages=[]
        report={'ok':True,'host_missing':[],'login_saved':True}
        options={'use_vpn':False,'source':None,'selection':None,'vpn_autostart':False}
        failure='session_refresh_html_403'
        def install(config,activate=True):
            config['installed']=True
            return {'installed':True}
        with contextlib.ExitStack() as stack:
            for module,name,result in [
                (devices,'enter',None),(cli,'load',value),(ui,'banner',None),
                (cli.updates,'clean_downloads',None),(cli.updates,'show_result',None),(cli.updates,'startup_offer',None),
                (cli,'setup_key',None),(cli.doctor,'inspect',report),(cli.doctor,'require',None),
                (cli.network,'check',{'country':'RU'}),(cli,'installation_options',options),
                (cli,'start_mode',{'login_saved':True}),(storage,'report',{'enough':True}),
                (cli,'save',None),(cli,'show',None),(cli,'show_storage',None),
                (cli.autostart,'station_flag',None),(cli.autostart,'set_enabled',None)]:
                stack.enter_context(patch.object(module,name,return_value=result))
            stack.enter_context(patch('alisa_cli.winssh.prepare'))
            stack.enter_context(patch('alisa_cli.install.install',side_effect=install))
            stack.enter_context(patch.object(cli,'yes',side_effect=lambda title,default=False:default))
            stack.enter_context(patch.object(cli,'verify_with_stun_retry',side_effect=RuntimeError(failure)))
            stack.enter_context(patch.object(ui,'note',side_effect=lambda text,*args:messages.append(text)))
            menu=stack.enter_context(patch.object(menus,'run',return_value=0))
            self.assertEqual(cli.main(['native']),0)
        self.assertTrue(value['installed'])
        menu.assert_called_once()
        self.assertEqual(menu.call_args.args[0],'native')
        self.assertIn('Проверка голоса не пройдена. '+failure,messages)
        self.assertTrue(any('Установка завершена' in text for text in messages))

class Diagnostics(unittest.TestCase):
    def test_empty_missing_components_are_hidden_in_terminal_but_preserved_in_json(self):
        value={'free_data_kib':50980,'host_missing':[],'station_missing':[],'ok':True}
        output=io.StringIO()
        with patch.object(ui,'interactive',return_value=True),contextlib.redirect_stdout(output),ui.feedback(quiet=True) as messages:ui.show(value)
        self.assertEqual(output.getvalue(),'');self.assertTrue(any('Свободно' in message for message in messages))
        self.assertFalse(any('компонент' in message for message in messages))
        with patch.object(ui,'interactive',return_value=False),contextlib.redirect_stdout(output):ui.show(value)
        self.assertEqual(json.loads(output.getvalue()),value)

    def test_actual_missing_components_are_reported(self):
        with patch.object(ui,'interactive',return_value=True),ui.feedback(quiet=True) as messages:ui.show({'host_missing':['ssh'],'station_missing':['tar']})
        self.assertIn('Отсутствуют компоненты ПК: ssh',messages)
        self.assertIn('Отсутствуют компоненты колонки: tar',messages)

    def test_status_is_translated_and_empty_error_is_hidden(self):
        with patch.object(ui,'interactive',return_value=True),ui.feedback(quiet=True) as messages:ui.show({'mode':'native','phase':'stopped','error':''})
        self.assertEqual(messages,['Режим: Нативный','Состояние: Остановлен'])

    def test_space_warning_only_names_the_device_with_a_real_deficit(self):
        value={'space':{'free_bytes':50*1048576,'package_bytes':39*1048576,'required_free_bytes':14*1048576,'reserve_bytes':8*1048576,'free_after_bytes':50*1048576},'components':[],
               'computer_free_bytes':200*1048576,'computer_required_bytes':40*1048576,'memory_available_bytes':100*1048576,'enough':True}
        self.assertFalse(any('Не хватает' in line for line in storage.lines(value)))
        value['space']['free_bytes']=10*1048576;value['enough']=False
        lines=storage.lines(value)
        self.assertIn('Не хватает места на колонке: 4.0 МиБ.',lines)
        self.assertFalse(any('Не хватает места на компьютере' in line for line in lines))
        value['space']['free_bytes']=50*1048576;value['computer_free_bytes']=30*1048576
        lines=storage.lines(value)
        self.assertIn('Не хватает места на компьютере: 10.0 МиБ.',lines)
        self.assertFalse(any('Не хватает места на колонке' in line for line in lines))
