"""Real terminal input: arrows, checkbox toggles, masking and cancellation."""
import importlib.util,io,os,sys,time,unittest,warnings
from pathlib import Path

@unittest.skipUnless(importlib.util.find_spec('pexpect'),'Development PTY tests use pexpect')
class TerminalUI(unittest.TestCase):
 def run_terminal(self,code,drive):
  import pexpect
  child=pexpect.spawn(sys.executable,['-c',code],cwd=Path(__file__).resolve().parents[1],encoding='utf-8',
         env={**os.environ,'TERM':'xterm-256color','PROMPT_TOOLKIT_NO_CPR':'1'},timeout=12,dimensions=(30,100))
  # Full-screen renderers reuse unchanged characters with cursor movement;
  # inspect the rendered screen rather than requiring contiguous raw bytes.
  with warnings.catch_warnings():
   warnings.simplefilter('ignore',UserWarning)
   from pexpect.ANSI import ANSI
  terminal=ANSI(30,100)
  class Output(io.StringIO):
   def write(self,chunk):terminal.write(chunk);return super().write(chunk)
  output=Output();child.logfile_read=output
  def expect_screen(text):
   deadline=time.monotonic()+child.timeout
   while text not in str(terminal):
    if time.monotonic()>deadline:raise AssertionError('Missing rendered text: '+text+'\n'+str(terminal))
    try:child.read_nonblocking(8192,timeout=.1)
    except pexpect.TIMEOUT:pass
  child.expect_screen=expect_screen
  child.transcript=output
  try:drive(child);child.expect(pexpect.EOF);child.close();return output.getvalue(),child.exitstatus
  finally:
   if child.isalive():child.terminate(force=True)
 def menu_item(self,child,page,value,enter=True):
  from alisa_cli import menus,state
  config=state.defaults('native');config['vpn']['enabled']=True
  index=next(i for i,(_,target) in enumerate(menus.entries('native',page,config)) if target==value)
  child.send('\x1b[H'+'\x1b[B'*index+('\r' if enter else ''))
 def test_arrows_select_and_space_checks(self):
  code="from alisa_cli import ui; ui.banner(); print('MODE',ui.select('Режим',[('Нативный','native'),('Мост','bridge')])); print('OPTIONS',ui.checklist('Настройки',[('Автозапуск','auto'),('VLESS','vpn')]))"
  def drive(c):
   c.expect('Режим');c.send('\x1b[B\r');c.expect('MODE bridge');c.expect('Настройки');c.send(' \x1b[B \r');c.expect("OPTIONS \\['auto', 'vpn'\\]")
  output,code=self.run_terminal(code,drive)
  self.assertEqual(code,0);self.assertIn('\x1b[',output)
 def test_password_is_masked_and_never_echoed(self):
  code="from alisa_cli import ui; value=ui.secret('Пароль колонки'); print('LENGTH',len(value))"
  secret='never-print-test-password'
  def drive(c):c.expect('Пароль колонки');c.sendline(secret);c.expect('LENGTH '+str(len(secret)))
  output,code=self.run_terminal(code,drive);self.assertEqual(code,0);self.assertNotIn(secret,output)
 def test_ctrl_c_exits_without_accepting_a_choice(self):
  code="from alisa_cli import ui\ntry: ui.select('Выбор',[('Запуск','start'),('Выход','exit')])\nexcept KeyboardInterrupt: print('CANCELLED')"
  def drive(c):c.expect('Выбор');c.sendcontrol('c');c.expect('CANCELLED')
  _,code=self.run_terminal(code,drive);self.assertEqual(code,0)
 def test_fresh_cli_requests_ip_and_port_without_searching(self):
  import tempfile
  with tempfile.TemporaryDirectory(prefix='alisa-empty-config-') as folder:
   code='import os; os.environ["ALISA_HOME"]='+repr(folder)+'; from alisa_cli.cli import main; raise SystemExit(main([]))'
   def drive(c):
    c.expect('выберите режим');c.send('\r');c.expect('IP-адрес колонки');c.sendline('192.0.2.44');c.expect('Порт SSH');c.sendcontrol('c')
   output,result=self.run_terminal(code,drive)
   self.assertEqual(result,130);self.assertIn('2222',output);self.assertNotIn('SSH-адрес или alias',output)
 def test_dashboard_keeps_vpn_page_and_selected_action_after_execution(self):
  code="""from alisa_cli import ui,menus,state
c=state.defaults('native');c.update(host='192.0.2.44',installed=True)
menus.load=lambda mode:c
calls=[]
def execute(action,operation=None):
 calls.append((action,operation));ui.note('DONE '+action+'/'+str(operation));return 0
try: menus.run('native',execute)
except KeyboardInterrupt: print('CALLS',calls)
"""
  def drive(c):
   c.expect('Нативный режим');self.menu_item(c,'main','@vpn');c.expect_screen('Запустить VPN')
   self.menu_item(c,'vpn',('vpn','stop'));time.sleep(.2)
   c.send('\r');time.sleep(.3)
   c.sendcontrol('c');c.expect('CALLS')
  output,result=self.run_terminal(code,drive)
  self.assertEqual(result,0);self.assertIn("[('vpn', 'stop'), ('vpn', 'stop')]",output)
  self.assertEqual(output.count('\x1b[?1049h'),1)
  self.assertEqual(output.count('\x1b[?1049l'),1)

 def test_help_and_checks_update_without_leaving_the_screen(self):
  code="""from alisa_cli import ui,menus,state
c=state.defaults('native');c.update(host='192.0.2.44',installed=True)
menus.load=lambda mode:c
calls=[]
def execute(action,operation=None):
 calls.append(action);ui.note('RESULT '+action);return 0
try: menus.run('native',execute)
except KeyboardInterrupt: print('CALLS',calls)
"""
  def drive(c):
   c.expect('Нативный режим');self.menu_item(c,'main','@service');c.expect_screen('Настроить / переустановить')
   self.menu_item(c,'service',('help',None),enter=False);c.expect_screen('RESULT help')
   c.send('\r');time.sleep(.3)
   self.menu_item(c,'service','@checks');c.expect_screen('Колонка и компоненты')
   c.send('\r');c.expect_screen('RESULT doctor');c.sendcontrol('c');c.expect('CALLS')
  output,result=self.run_terminal(code,drive)
  self.assertEqual(result,0);self.assertIn("['help', 'help', 'doctor']",output)
  self.assertEqual(output.count('\x1b[?1049h'),1);self.assertEqual(output.count('\x1b[?1049l'),1)

 def test_update_offer_unwinds_the_dashboard_before_process_replacement(self):
  code="""from alisa_cli import ui,menus,state,updates
c=state.defaults('native');c.update(host='192.0.2.44',installed=True)
menus.load=lambda mode:c
updates.check=lambda **kwargs:dict(available=True,latest_version='99.0.0',current_version='0.3.0')
updates.prepare=lambda *args,**kwargs:updates.UpdateScheduled('synthetic',[],'.')
def execute(action,operation=None):
 return updates.command([operation] if operation else [])
try: menus.run('native',execute,start_page='updates')
except updates.UpdateScheduled: print('UPDATE_EXITED_CLEANLY')
"""
  def drive(c):
   c.expect_screen('Проверить и обновить');c.send('\r')
   c.expect('Доступна Alisa Voice');c.send('\x1b[A\r');c.expect('UPDATE_EXITED_CLEANLY')
  output,result=self.run_terminal(code,drive)
  self.assertEqual(result,0);self.assertNotIn('Traceback',output)
  # A nested confirmation suspends and restores the dashboard. Every entry
  # must be balanced before the updater takes over the terminal.
  self.assertGreaterEqual(output.count('\x1b[?1049h'),1)
  self.assertEqual(output.count('\x1b[?1049h'),output.count('\x1b[?1049l'))
  self.assertLess(output.rfind('\x1b[?1049l'),output.rfind('UPDATE_EXITED_CLEANLY'))

 def test_dashboard_dialog_returns_to_the_same_page(self):
  code="""from alisa_cli import ui,menus,state
c=state.defaults('native');c.update(host='192.0.2.44',installed=True)
menus.load=lambda mode:c
def execute(action,operation=None):
 ui.note('SAVED '+ui.text('Введите фразу'));return 0
try: menus.run('native',execute)
except KeyboardInterrupt: print('FINISHED')
"""
  def drive(c):
   c.expect('Нативный режим');self.menu_item(c,'main','@settings');c.expect_screen('Голосовые фразы');c.send('\r')
   c.expect('Введите фразу');c.sendline('тестовая фраза');c.expect('SAVED тестовая фраза');time.sleep(.3)
   c.sendcontrol('c');c.expect('FINISHED')
  output,result=self.run_terminal(code,drive)
  self.assertEqual(result,0);self.assertNotIn('asyncio.run() cannot',output)

 def test_highlighting_status_loads_it_without_enter(self):
  code="""from alisa_cli import ui,menus,state
c=state.defaults('native');c.update(host='192.0.2.44',installed=True)
menus.load=lambda mode:c
calls=[]
def execute(action,operation=None):
 calls.append(action);ui.note('AUTORESULT '+action);return 0
try: menus.run('native',execute)
except KeyboardInterrupt: print('CALLS',calls)
"""
  def drive(c):
   c.expect('Нативный режим');c.expect('AUTORESULT status');c.sendcontrol('c');c.expect('CALLS')
  output,result=self.run_terminal(code,drive)
  self.assertEqual(result,0);self.assertIn("['status']",output)
  self.assertEqual(output.count('\x1b[?1049h'),1)

 def test_autostart_toggles_stay_on_their_row_and_escape_returns(self):
  code="""from alisa_cli import ui,menus,state
c=state.defaults('native');c.update(host='192.0.2.44',installed=True);c['vpn']['enabled']=True
menus.load=lambda mode:c
calls=[]
def execute(action,operation=None):
 calls.append((action,operation))
 if action=='autostart':c['autostart']=operation=='on'
 else:c['vpn']['autostart']=operation=='autostart-on'
 ui.note('TOGGLED '+operation);return 0
try: menus.run('native',execute)
except KeyboardInterrupt: print('CALLS',calls)
"""
  def drive(c):
   c.expect('Нативный режим');self.menu_item(c,'main','@settings');c.expect_screen('Голосовые фразы')
   self.menu_item(c,'settings',('autostart','on'),enter=False)
   c.send(' ');c.expect_screen('Автозапуск GPT: включён');c.send('\x1b[B ');c.expect_screen('Автозапуск VLESS: включён')
   c.send(' ');c.expect_screen('Автозапуск VLESS: выключен');c.send('\x1b');time.sleep(.6)
   c.sendcontrol('c');c.expect('CALLS')
  output,result=self.run_terminal(code,drive)
  self.assertEqual(result,0)
  self.assertIn("[('autostart', 'on'), ('vpn', 'autostart-on'), ('vpn', 'autostart-off')]",output)
  self.assertNotIn('Настроить галочками',output)

 def test_listening_wave_checkbox_switches_with_space_and_stays_selected(self):
  code="""from alisa_cli import ui,menus,state
c=state.defaults('native');c.update(host='192.0.2.44',installed=True)
menus.load=lambda mode:c
calls=[]
def execute(action,operation=None):
 calls.append((action,operation));c['listen_led']=operation=='listen-on'
 ui.note('LISTEN '+str(c['listen_led']));return 0
try: menus.run('native',execute,start_page='settings')
except KeyboardInterrupt: print('CALLS',calls)
"""
  def drive(c):
   c.expect('Настройки');self.menu_item(c,'settings',('settings','listen-on'),enter=False)
   c.expect_screen('В паузах GPT: выключена')
   c.send(' ');time.sleep(.4);c.send(' ');time.sleep(.4)
   c.sendcontrol('c');c.expect('CALLS')
  output,result=self.run_terminal(code,drive)
  self.assertEqual(result,0);self.assertIn("[('settings', 'listen-on'), ('settings', 'listen-off')]",output)
  self.assertEqual(output.count('\x1b[?1049h'),1)

 def test_vpn_page_toggles_autostart_and_offers_file_removal(self):
  code="""from alisa_cli import ui,menus,state
c=state.defaults('native');c.update(host='192.0.2.44',installed=True);c['vpn']['enabled']=True
menus.load=lambda mode:c
calls=[]
def execute(action,operation=None):
 calls.append((action,operation))
 if operation in ('autostart-on','autostart-off'):c['vpn']['autostart']=operation=='autostart-on'
 ui.note('Автозапуск VLESS: '+('включён' if c['vpn']['autostart'] else 'выключен'));return 0
try: menus.run('native',execute,start_page='vpn')
except KeyboardInterrupt: print('CALLS',calls)
"""
  def drive(c):
   c.expect_screen('Удалить VLESS и его файлы')
   self.menu_item(c,'vpn',('vpn','autostart-on'),enter=False);c.send(' ')
   # pexpect.ANSI miscounts the checkmark when the renderer updates a row;
   # verify the actual keyboard actions through the controller's call log.
   time.sleep(.7);c.send(' ');time.sleep(.7)
   self.menu_item(c,'vpn',('vpn','remove'),enter=False);time.sleep(.5)
   c.sendcontrol('c');c.expect('CALLS')
  output,result=self.run_terminal(code,drive)
  self.assertEqual(result,0);self.assertIn("('vpn', 'autostart-on')",output);self.assertIn("('vpn', 'autostart-off')",output)
  self.assertNotIn("('vpn', 'remove')",output)

 def test_account_actions_explain_existing_profile_change_and_logout_on_focus(self):
  code="""from alisa_cli import ui,menus,state
c=state.defaults('native');c.update(host='192.0.2.44',installed=True)
menus.load=lambda mode:c
try: menus.run('native',lambda *args:0,start_page='account')
except KeyboardInterrupt: print('FINISHED')
"""
  def drive(c):
   c.expect_screen('Вход в текущем профиле браузера')
   c.send('\x1b[B');c.expect_screen('Откроется чистый профиль')
   c.send('\x1b[B');c.expect_screen('Удалить текущий вход ChatGPT')
   c.sendcontrol('c');c.expect('FINISHED')
  _,result=self.run_terminal(code,drive);self.assertEqual(result,0)

 def test_escape_cancels_a_dialog_without_leaving_management(self):
  code="""from alisa_cli import ui,menus,state
c=state.defaults('native');c.update(host='192.0.2.44',installed=True)
menus.load=lambda mode:c
def execute(action,operation=None):
 try: ui.text('Введите фразу')
 except ui.Back: ui.note('CANCELLED_DIALOG')
 return 0
try: menus.run('native',execute)
except KeyboardInterrupt: print('FINISHED')
"""
  def drive(c):
   c.expect('Нативный режим');self.menu_item(c,'main','@settings');c.expect_screen('Голосовые фразы');c.send('\r');c.expect('Введите фразу')
   c.send('\x1b');c.expect('CANCELLED_DIALOG');time.sleep(.2);c.sendcontrol('c');c.expect('FINISHED')
  output,result=self.run_terminal(code,drive)
  self.assertEqual(result,0)

 def test_led_picker_previews_on_arrows_and_restores_on_escape(self):
  code="""from alisa_cli import ui,state,lighting
c=state.defaults('native');c['host']='192.0.2.44'
events=[]
class Demo:
 def __init__(self,config):pass
 def select(self,style):events.append(style);return 'DEMO '+style
 def close(self):events.append('restored')
lighting.Preview=Demo
try: lighting.choose(c)
except ui.Back: print('EVENTS',events)
"""
  def drive(c):
   c.expect('DEMO rainbow');c.send('\x1b[B');c.expect_screen('DEMO red');c.send('\x1b');c.expect('EVENTS')
  output,result=self.run_terminal(code,drive)
  self.assertEqual(result,0);self.assertIn("['rainbow', 'red', 'restored']",output)

 def test_fast_navigation_has_no_generic_fallback_or_late_preview_overwrite(self):
  code="""import time
from alisa_cli import ui,menus,state
c=state.defaults('native');c.update(host='192.0.2.44',installed=True)
menus.load=lambda mode:c
calls=[]
def execute(action,operation=None):
 calls.append(action);time.sleep(.8);ui.note('CACHED_STATE');return 0
try: menus.run('native',execute)
except KeyboardInterrupt: print('CALLS',calls)
"""
  def drive(c):
   c.expect('Получаю состояние GPT');time.sleep(.4);c.send('\x1b[B')
   c.expect_screen('Алиса, открой чат');time.sleep(.9)
   while True:
    try:c.read_nonblocking(8192,timeout=.05)
    except Exception:break
   self.assertNotIn('CACHED_STATE',c.transcript.getvalue())
   c.send('\x1b[H');c.expect('CACHED_STATE');c.sendcontrol('c');c.expect('CALLS')
  output,result=self.run_terminal(code,drive)
  self.assertEqual(result,0);self.assertIn("['status']",output)
  self.assertNotIn('Результат',output);self.assertNotIn('Esc или Enter — назад',output)
  self.assertEqual(output.count('\x1b[?1049h'),1);self.assertEqual(output.count('\x1b[?1049l'),1)

 def test_login_wait_finishes_without_pressing_enter(self):
  code="""from alisa_cli import ui
checks=[]
def ready():
 checks.append(True);return len(checks)>=5
ui.wait_until('LOGIN_WAITING',ready,interval=.05)
print('AUTO_SAVED')
"""
  def drive(c):c.expect('LOGIN_WAITING');c.expect('AUTO_SAVED')
  output,result=self.run_terminal(code,drive)
  self.assertEqual(result,0);self.assertNotIn('Enter',output)
