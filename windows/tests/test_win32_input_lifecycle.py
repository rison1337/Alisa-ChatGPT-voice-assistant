"""Queued Win32 input notifications must not rearm detached registrations."""
import contextlib,sys,unittest
from unittest.mock import Mock,patch


@unittest.skipUnless(sys.platform=='win32','Win32 handle registration')
class Win32InputLifecycle(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self):
        from alisa_cli import ui
        ui.library()
        from prompt_toolkit.input import win32
        from ctypes.wintypes import HANDLE
        registry=win32._Win32Handles();loop=Mock();waits=[];queued=[]
        loop.call_soon_threadsafe.side_effect=lambda callback:queued.append(callback)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(win32,'get_running_loop',return_value=loop))
            stack.enter_context(patch.object(win32,'create_win32_event',side_effect=[HANDLE(101),HANDLE(102)]))
            executor=stack.enter_context(patch.object(win32,'run_in_executor_with_context',side_effect=lambda wait,**kwargs:waits.append(wait)))
            wait=stack.enter_context(patch.object(win32,'wait_for_handles',side_effect=lambda handles:handles[1]))
            kernel=stack.enter_context(patch.object(win32,'windll'))
            yield registry,HANDLE(7),waits,queued,executor,wait,kernel

    def test_queued_ready_after_detach_does_not_submit_to_shutdown_executor(self):
        with self.fixture() as (registry,handle,waits,queued,executor,wait,kernel):
            callback=Mock();registry.add_win32_handle(handle,callback)
            event=registry._remove_events[handle.value];waits[0]()
            registry.remove_win32_handle(handle)
            executor.side_effect=RuntimeError('Executor shutdown has been called')
            queued.pop()()
            callback.assert_not_called();self.assertEqual(executor.call_count,1)
            kernel.kernel32.CloseHandle.assert_called_once_with(event)

    def test_callback_detaching_itself_is_not_rearmed(self):
        with self.fixture() as (registry,handle,waits,queued,executor,wait,kernel):
            registry.add_win32_handle(handle,lambda:registry.remove_win32_handle(handle))
            event=registry._remove_events[handle.value];waits[0]();queued.pop()()
            self.assertEqual(executor.call_count,1)
            kernel.kernel32.CloseHandle.assert_called_once_with(event)

    def test_replacement_keeps_new_registration_and_skips_old_callback(self):
        with self.fixture() as (registry,handle,waits,queued,executor,wait,kernel):
            old=Mock();new=Mock();registry.add_win32_handle(handle,old)
            old_event=registry._remove_events[handle.value];waits[0]()
            registry.add_win32_handle(handle,new)
            new_event=registry._remove_events[handle.value];queued.pop()()
            old.assert_not_called();new.assert_not_called()
            self.assertIs(registry._remove_events[handle.value],new_event)
            self.assertEqual(executor.call_count,2)
            kernel.kernel32.CloseHandle.assert_called_once_with(old_event)

    def test_live_callback_rearms_and_remove_waiter_closes_event_once(self):
        with self.fixture() as (registry,handle,waits,queued,executor,wait,kernel):
            callback=Mock();registry.add_win32_handle(handle,callback)
            event=registry._remove_events[handle.value];waits[0]();queued.pop()()
            callback.assert_called_once();self.assertEqual(executor.call_count,2)
            kernel.kernel32.CloseHandle.assert_not_called()
            registry.remove_win32_handle(handle);wait.return_value=event;wait.side_effect=None
            waits[1]();kernel.kernel32.CloseHandle.assert_called_once_with(event)

    def test_detach_then_callback_exception_does_not_rearm(self):
        with self.fixture() as (registry,handle,waits,queued,executor,wait,kernel):
            def callback():
                registry.remove_win32_handle(handle)
                raise ValueError('callback error')
            registry.add_win32_handle(handle,callback);waits[0]()
            with self.assertRaisesRegex(ValueError,'callback error'):queued.pop()()
            self.assertEqual(executor.call_count,1);kernel.kernel32.CloseHandle.assert_called_once()


if __name__=='__main__':unittest.main()
