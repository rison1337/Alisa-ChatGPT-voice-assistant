"""Display preference selection, cancellation and next-conversation updates."""
import copy,io,os,subprocess,tempfile,time,unittest
from pathlib import Path
import test_mini3_display as clock_fixtures
from unittest.mock import Mock,patch
from alisa_cli import cli,display,menus,runtime,state,ui


class DisplaySettings(unittest.TestCase):
    def config(self):
        c=state.defaults('native');c.update(host='192.0.2.44',model='bergamot',installed=True)
        return c

    def test_default_and_old_configs_use_eyes_and_clock_is_valid(self):
        c=self.config();self.assertEqual(c['display'],'eyes')
        c.pop('display');state.validate(c)
        self.assertIn(b'ALISA_MINI3_CLOCK_EYES=1',runtime.native_environment(c))
        c['display']='clock';state.validate(c)
        for value in ('off',None,True):
            c['display']=value
            with self.assertRaises(ValueError):state.validate(c)

    def test_wizard_offers_display_after_lighting_only_for_mini3(self):
        for model,expected in (('bergamot',[('led',),('display',)]),('mini2',[('led',)])):
            c=self.config();c.update(model=model,display='clock')
            with self.subTest(model=model),patch.object(cli,'settings') as settings,patch.object(ui,'note'),patch.object(ui,'select',side_effect=lambda title,items,default=None:default),patch.object(cli,'yes',return_value=False):
                cli.installation_options(c,{'country':'DE'})
            self.assertEqual([call.kwargs['fields'] for call in settings.call_args_list],expected)
            self.assertEqual(c['display'],'clock')

    def test_menu_is_scoped_to_mini3_native(self):
        c=self.config()
        self.assertIn(('settings','display'),[v for _,v in menus.entries('native','settings',c)])
        for mode,model in (('bridge','bergamot'),('native','mini2'),('native','yandexmidi')):
            c.update(mode=mode,model=model)
            self.assertNotIn(('settings','display'),[v for _,v in menus.entries(mode,'settings',c)])
            with self.assertRaises(ValueError):display.choose(c)

    def test_cancel_closes_preview_without_mutating_config(self):
        c=self.config();before=copy.deepcopy(c)
        with patch.object(ui,'interactive',return_value=True),patch.object(display,'Preview') as demo,patch.object(ui,'dashboard'):
            with self.assertRaises(ui.Back):display.choose(c)
        demo.return_value.close.assert_called_once();self.assertEqual(c,before)

    def test_selection_is_returned_only_after_preview_closes(self):
        c=self.config();events=[]
        def dashboard(*args,**kwargs):
            self.assertEqual([v for _,v in args[1]],['eyes','clock','back'])
            kwargs['on_focus']('', 'eyes');kwargs['on_focus']('', 'clock')
            kwargs['on_select']('clock')
        with patch.object(ui,'interactive',return_value=True),patch.object(display,'Preview') as demo,patch.object(ui,'dashboard',side_effect=dashboard):
            demo.return_value.close.side_effect=lambda:events.append('closed')
            self.assertEqual(display.choose(c),'clock')
        self.assertEqual(events,['closed']);self.assertEqual(c['display'],'eyes')

    def test_settings_update_only_preference_file_and_do_not_interrupt_audio(self):
        c=self.config();before=copy.deepcopy(c);c['display']='clock'
        with patch.object(runtime,'Station') as station,patch.object(runtime,'status') as status,patch.object(runtime,'start') as start,patch.object(runtime,'stop') as stop,patch.object(runtime,'native_action') as action:
            runtime.update_preferences(c,before)
        station.return_value.put.assert_called_once_with(runtime.NATIVE+'/display-mode',b'clock\n')
        for mock in (status,start,stop,action):mock.assert_not_called()

    def test_failed_remote_write_restores_previous_display_preference(self):
        c=self.config();before=copy.deepcopy(c);c['display']='clock'
        with patch.object(runtime,'Station') as station:
            station.return_value.put.side_effect=[OSError('write failed'),None]
            with self.assertRaises(OSError):runtime.update_preferences(c,before)
        self.assertEqual(station.return_value.put.call_args.args,(runtime.NATIVE+'/display-mode',b'eyes\n'))

    def test_display_settings_are_saved_and_wizard_selection_stays_unsaved(self):
        for apply in (True,False):
            c=self.config()
            with self.subTest(apply=apply),patch.object(display,'choose',return_value='clock'),patch.object(cli,'save') as save,patch.object(runtime,'update_preferences') as update,patch.object(ui,'note'):
                cli.settings(c,apply=apply,fields=('display',))
            self.assertEqual(c['display'],'clock');self.assertEqual(save.called,apply);self.assertEqual(update.called,apply)

    def test_active_conversation_blocks_preview_even_with_stale_local_state(self):
        c=self.config();c['installed']=False
        for phase in ('active','opening','connecting'):
            with self.subTest(phase=phase),patch.object(runtime,'status',return_value={'phase':phase}),patch.object(display,'Station') as station:
                demo=display.Preview(c)
                try:self.assertIn('разговор GPT',demo.select('eyes'))
                finally:demo.close()
                station.assert_not_called()

    def test_streams_scripts_over_stdin_and_clock_selection_releases_preview(self):
        c=self.config();process=Mock();process.poll.return_value=None;process.wait.return_value=0;process.stdin=io.BytesIO()
        with patch.object(runtime,'status',return_value={'phase':'idle'}),patch.object(display,'Station') as station,patch.object(display.threading,'Thread'):
            station.return_value.open_input.return_value=process
            demo=display.Preview(c);demo.select('eyes')
            data=process.stdin.getvalue().splitlines();self.assertEqual(len(data),3)
            import base64
            for line,name in zip(data,('clock-eyes.sh','clock-display.sh','clock-isolation.sh')):
                self.assertEqual(base64.b64decode(line),(state.ROOT/'assets/native'/name).read_bytes())
            command=station.return_value.open_input.call_args.args[0]
            self.assertLess(len(command),8192);self.assertIn('ALISA_CLOCK_PREVIEW=1',command)
            demo.select('clock');demo.close();process.wait.assert_called_once()

    @unittest.skipUnless(clock_fixtures.SHELL,'Bash required for streamed preview fixture')
    def test_real_streamed_preview_switches_to_clock_and_restores_on_close(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);led=root/'led'
            for part,value in zip(clock_fixtures.PARTS,clock_fixtures.INITIAL):
                target=root/('led'+part);target.mkdir();(target/'brightness').write_bytes((str(value)+'\n').encode())
            (root/'compatible').write_bytes(b'yandex,bergamot\0')
            (root/'uptime').write_bytes(b'0.00 0.00\n');(root/'volume').write_bytes(b'1\n')
            (root/'bin').mkdir();binary=root/'bin/alisa-standalone'
            binary.write_bytes(b'#!/bin/sh\necho \'{"phase": "idle"}\'\n');binary.chmod(0o700)
            env={**os.environ,'ALISA_CLOCK_LED_ROOT':led.as_posix(),
                 'ALISA_CLOCK_DEVICE_TREE':(root/'compatible').as_posix(),
                 'ALISA_CLOCK_UPTIME_FILE':(root/'uptime').as_posix(),
                 'ALISA_CLOCK_VOLUME_FILE':(root/'volume').as_posix()}
            class Station:
                def __init__(self,config):pass
                def open_input(self,command):
                    command=command.replace('ALISA_CLOCK_STATUS_BASE=/data/opt/alisa-standalone','ALISA_CLOCK_STATUS_BASE="'+root.as_posix()+'"')
                    command=command.replace(' sh "$demo/clock-eyes.sh"',' bash "$demo/clock-eyes.sh"')
                    return subprocess.Popen([clock_fixtures.SHELL,'-c',command],env=env,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            def frame():return [int((root/('led'+part)/'brightness').read_bytes()) for part in clock_fixtures.PARTS]
            with patch.object(display,'Station',Station),patch.object(runtime,'status',return_value={'phase':'idle'}):
                demo=display.Preview(self.config())
                try:
                    for _ in range(2):
                        demo.select('eyes');deadline=time.monotonic()+3
                        while time.monotonic()<deadline and sum(v>0 for v in frame())!=8:time.sleep(.02)
                        self.assertEqual(sum(v>0 for v in frame()),8)
                        demo.select('clock');self.assertEqual(frame(),clock_fixtures.INITIAL)
                    demo.select('eyes');deadline=time.monotonic()+3
                    while time.monotonic()<deadline and sum(v>0 for v in frame())!=8:time.sleep(.02)
                    self.assertEqual(sum(v>0 for v in frame()),8)
                finally:demo.close()
                self.assertEqual(frame(),clock_fixtures.INITIAL)


if __name__=='__main__':unittest.main()
