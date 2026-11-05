"""Explicit STUN recovery, persistence and rollback without a live station."""
import contextlib,copy,unittest
from unittest.mock import patch
from alisa_cli import cli,menus,state


class StunRetry(unittest.TestCase):
    def config(self):
        c=state.defaults('native');c.update(installed=True,host='192.0.2.44')
        return c

    @contextlib.contextmanager
    def runtime(self,replies):
        with contextlib.ExitStack() as stack:
            mocks={}
            for name in ('verify','yes','save','start_mode'):
                mocks[name]=stack.enter_context(patch.object(cli,name))
            mocks['verify'].side_effect=replies;mocks['yes'].return_value=True
            mocks['stop']=stack.enter_context(patch.object(cli.runtime,'stop'))
            stack.enter_context(patch.object(cli.ui,'interactive',return_value=True))
            stack.enter_context(patch.object(cli.ui,'note'))
            stack.enter_context(patch.object(cli.ui,'activity',side_effect=lambda *a,**k:contextlib.nullcontext()))
            yield mocks

    def test_menu_suspends_dashboard_for_voice_check_confirmation(self):
        c=self.config()
        with patch.object(menus,'load',return_value=c),patch.object(cli.ui,'interactive',return_value=True),patch.object(cli.ui,'dashboard',return_value=0) as dashboard:
            menus.run('native',lambda *args:0)
        inline=dashboard.call_args.kwargs['inline']
        self.assertFalse(inline(('verify',None)))
        self.assertTrue(inline(('status',None)))
        self.assertTrue(inline(('stop',None)))

    def test_success_without_timeout_never_prompts_or_restarts(self):
        c=self.config();reply={'real_reply_verified':True}
        with self.runtime([reply]) as m:
            self.assertEqual(cli.verify_with_stun_retry(c),reply)
        for name in ('yes','save','start_mode','stop'):m[name].assert_not_called()

    def test_retry_saves_only_after_confirmed_reply_for_any_native_model(self):
        for model in ('bergamot','lite1','lite2','mini2','yandexmidi'):
            c=self.config();c['model']=model;events=[]
            with self.subTest(model=model),self.runtime([RuntimeError('ice_gather_timeout'),{'real_reply_verified':True}]) as m:
                m['save'].side_effect=lambda value:events.append(('save',value['disable_stun']))
                cli.verify_with_stun_retry(c)
            self.assertTrue(c['disable_stun']);self.assertEqual(events,[('save',True)])
            self.assertEqual(m['verify'].call_count,2);m['stop'].assert_called_once()
            self.assertTrue(m['start_mode'].call_args.args[0]['disable_stun'])

    def test_declining_keeps_original_state_and_error(self):
        c=self.config();before=copy.deepcopy(c)
        with self.runtime([RuntimeError('ice_gather_timeout')]) as m:
            m['yes'].return_value=False
            with self.assertRaisesRegex(RuntimeError,'^ice_gather_timeout$'):cli.verify_with_stun_retry(c)
        self.assertEqual(c,before)
        for name in ('save','start_mode','stop'):m[name].assert_not_called()

    def test_other_errors_bridge_existing_bypass_and_noninteractive_do_not_retry(self):
        for error,mode,disabled,interactive in (
                ('login_required','native',False,True),('ice_gather_timeout','bridge',False,True),
                ('ice_gather_timeout','native',True,True),('ice_gather_timeout','native',False,False)):
            c=self.config();c.update(mode=mode,disable_stun=disabled)
            with self.subTest(error=error,mode=mode,disabled=disabled,interactive=interactive),self.runtime([RuntimeError(error)]) as m:
                with patch.object(cli.ui,'interactive',return_value=interactive),self.assertRaisesRegex(RuntimeError,error):
                    cli.verify_with_stun_retry(c)
            for name in ('yes','save','start_mode','stop'):m[name].assert_not_called()

    def test_failed_retry_restores_missing_field_and_original_runtime(self):
        c=self.config();c.pop('disable_stun');before=copy.deepcopy(c)
        with self.runtime([RuntimeError('ice_gather_timeout'),RuntimeError('retry failed')]) as m:
            with self.assertRaisesRegex(RuntimeError,'retry failed'):cli.verify_with_stun_retry(c)
        self.assertEqual(c,before);self.assertEqual(m['stop'].call_count,2)
        self.assertEqual(m['start_mode'].call_count,2)
        self.assertEqual(m['start_mode'].call_args.args[0],before)
        m['save'].assert_called_once_with(before)

    def test_start_failure_and_interrupt_restore_previous_setting(self):
        for failure in (RuntimeError('old client'),KeyboardInterrupt()):
            c=self.config();before=copy.deepcopy(c)
            with self.subTest(failure=type(failure).__name__),self.runtime([RuntimeError('ice_gather_timeout')]) as m:
                m['start_mode'].side_effect=[failure,None]
                with self.assertRaises(type(failure)):cli.verify_with_stun_retry(c)
            self.assertEqual(c,before);self.assertEqual(m['start_mode'].call_count,2)

    def test_save_failure_rolls_back_after_successful_probe(self):
        c=self.config();before=copy.deepcopy(c)
        with self.runtime([RuntimeError('ice_gather_timeout'),{'real_reply_verified':True}]) as m:
            m['save'].side_effect=[OSError('disk full'),None]
            with self.assertRaisesRegex(OSError,'disk full'):cli.verify_with_stun_retry(c)
        self.assertEqual(c,before);self.assertEqual(m['start_mode'].call_count,2)

    def test_unconfirmed_reply_is_not_saved_as_success(self):
        c=self.config()
        with self.runtime([RuntimeError('ice_gather_timeout'),{}]) as m:
            with self.assertRaisesRegex(RuntimeError,'не подтвердила'):cli.verify_with_stun_retry(c)
        self.assertFalse(c['disable_stun']);self.assertEqual(m['start_mode'].call_count,2)

    def test_rollback_failure_is_reported(self):
        c=self.config()
        with self.runtime([RuntimeError('ice_gather_timeout'),RuntimeError('retry failed')]) as m:
            m['start_mode'].side_effect=[None,RuntimeError('SSH unavailable')]
            with self.assertRaisesRegex(RuntimeError,'Не удалось восстановить'):cli.verify_with_stun_retry(c)
        self.assertFalse(c['disable_stun'])

    def test_stun_settings_save_without_touching_running_audio(self):
        c=self.config()
        with patch.object(cli,'save') as save,patch.object(cli.runtime,'stop') as stop,patch.object(cli,'start_mode') as start,patch.object(cli.ui,'note'):
            cli.stun_settings(c,'stun-off')
            self.assertTrue(c['disable_stun']);save.assert_called_once()
            cli.stun_settings(c,'stun-off');self.assertEqual(save.call_count,1)
            cli.stun_settings(c,'stun-on');self.assertFalse(c['disable_stun'])
        stop.assert_not_called();start.assert_not_called()

    def test_stun_settings_failure_keeps_config_and_bridge_is_rejected(self):
        c=self.config();before=copy.deepcopy(c)
        with patch.object(cli,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):cli.stun_settings(c,'stun-off')
        self.assertEqual(c,before)
        c['mode']='bridge'
        with self.assertRaises(ValueError):cli.stun_settings(c,'stun-off')

    def test_native_menu_exposes_setting_for_all_models_and_bridge_does_not(self):
        for model in ('bergamot','lite2','mini2','yandexmidi'):
            c=self.config();c['model']=model
            self.assertIn(('settings','stun'),[item for _,item in menus.entries('native','settings',c)])
        self.assertNotIn(('settings','stun'),[item for _,item in menus.entries('bridge','settings',c)])


if __name__=='__main__':unittest.main()
