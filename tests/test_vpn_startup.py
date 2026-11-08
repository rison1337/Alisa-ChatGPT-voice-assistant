"""Run station scripts with fake ip/core tools; never change host networking."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform=='linux' and shutil.which('sh'),'Linux station shell')
class VpnStartup(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='alisa-vpn-test-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.base=self.root/'base';self.base.mkdir()
        self.tools=self.root/'tools';self.tools.mkdir();self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.state=self.root/'network.json'
        self.state.write_text(json.dumps({'reads':0,'up_after':3,'routes':{},'route_failures':0,'commands':[]}))
        self.env={**os.environ,'PATH':str(self.tools)+os.pathsep+os.environ['PATH'],'TEST_NETWORK':str(self.state),
                  'TEST_CORE_STARTS':str(self.root/'core-starts'),'TEST_WATCH':str(self.root/'watch-starts')}
        for name in ('vpn.sh','boot.sh'):
            source=(ROOT/'station'/name).read_text().replace('/tmp/alisa-',str(self.runtime/'alisa-'))
            source=source.replace('/data/opt/alisa-cli',str(self.base))
            source=source.replace('/data/opt/alisa-standalone',str(self.root/'native')).replace('/dev/net/tun','/dev/null')
            path=self.base/name;path.write_text(source);path.chmod(0o700)
        (self.base/'vpn-engine').write_text('sing-box\n');(self.base/'vless.json').write_text('{}')
        self.tool('sing-box','''import time
if sys.argv[1]=='check':raise SystemExit(0)
with open(os.environ['TEST_CORE_STARTS'],'a') as f:f.write(str(os.getpid())+'\\n')
while True:time.sleep(1)
''',self.base)
        self.tool('sleep','import time;time.sleep(.002)\n')
        self.tool('pidof','print(123)\n')
        self.tool('nohup','''if len(sys.argv)>2 and sys.argv[2]=='watch':
 with open(os.environ['TEST_WATCH'],'a') as f:f.write('watch\\n')
 raise SystemExit(0)
os.execv(sys.argv[1],sys.argv[1:])
''')
        self.tool('ip','''from pathlib import Path
p=Path(os.environ['TEST_NETWORK']);s=json.loads(p.read_text());a=sys.argv[1:];s['commands'].append(a)
family='6' if a and a[0]=='-6' else '4'
if a and a[0] in ('-4','-6'):a=a[1:]
code=0;out=''
if a[:2]==['link','show']:
 s['reads']+=1;s['up']=s['reads']>=s['up_after']
 out='9: alisa-gpt: <POINTOPOINT,NOARP'+(',UP' if s['up'] else '')+'> mtu 1500 state UNKNOWN'
elif a[:2]==['route','show']:
 rows=s['routes'].get(family,[])
 if family=='6' and a[-1]=='all':
  out='\\n'.join(row.replace(' metric',' table 150 metric',1) for row in rows)
  out+='\\ndefault dev alisa-gpt table 151 metric 10'
 elif family=='6' and s.get('ipv6_filter_bug'):out=''
 else:out='\\n'.join(rows)
elif a[:3]==['route','replace','unreachable']:
 rows=s['routes'].setdefault(family,[])
 if 'unreachable default metric 42760' not in rows:rows.append('unreachable default metric 42760')
elif a[:3]==['route','replace','default']:
 failing=s['route_failures'] and s.get('fail_family',family)==family
 if not s.get('up') or failing:
  if failing:s['route_failures']-=1
  code=2;out='RTNETLINK answers: Network is down'
 else:
  rows=s['routes'].setdefault(family,[])
  if 'default dev alisa-gpt metric 10' not in rows:rows.append('default dev alisa-gpt metric 10')
p.write_text(json.dumps(s));print(out);raise SystemExit(code)
''')
        self.addCleanup(self.stop_cores)

    def tool(self,name,source,directory=None):
        path=(directory or self.tools)/name
        path.write_text('#!'+sys.executable+'\nimport os,sys,json\n'+source);path.chmod(0o700)
        return path

    def stop_cores(self):
        paths=[self.root/'core-starts',self.runtime/'alisa-vpn.pid']
        for path in paths:
            if not path.exists():continue
            for value in path.read_text().splitlines():
                try:
                    pid=int(value)
                    command=Path(f'/proc/{pid}/cmdline').read_bytes()
                    if str(self.base/'sing-box').encode() in command:os.kill(pid,signal.SIGTERM)
                except (ValueError,OSError):pass

    def run_script(self,action='start',name='vpn.sh'):
        return subprocess.run(['sh',str(self.base/name),action],env=self.env,capture_output=True,timeout=12)

    def network(self,**changes):
        value=json.loads(self.state.read_text())
        if changes:value.update(changes);self.state.write_text(json.dumps(value))
        return value

    def existing_core(self):
        process=subprocess.Popen([str(self.base/'sing-box'),'run','-c',str(self.base/'vless.json')],env=self.env)
        self.addCleanup(process.wait,3);self.addCleanup(process.terminate)
        (self.runtime/'alisa-vpn.pid').write_text(str(process.pid)+'\n')
        return process

    def assert_ready(self):
        network=self.network();self.assertTrue(network['up'])
        for family in ('4','6'):
            self.assertIn('default dev alisa-gpt metric 10',network['routes'][family])
            self.assertIn('unreachable default metric 42760',network['routes'][family])
        self.assertEqual(self.run_script('ready').returncode,0)
        self.assertTrue((self.root/'watch-starts').exists())
        return network

    def test_tunnel_must_be_up_before_routes_are_added(self):
        result=self.run_script();self.assertEqual(result.returncode,0,result.stderr.decode())
        network=self.assert_ready();self.assertGreaterEqual(network['reads'],3)

    def test_existing_process_with_missing_routes_is_repaired(self):
        core=self.existing_core();self.network(up_after=1)
        result=self.run_script();self.assertEqual(result.returncode,0,result.stderr.decode())
        self.assert_ready();self.assertIsNone(core.poll())
        self.assertEqual((self.runtime/'alisa-vpn.pid').read_text().strip(),str(core.pid))

    def test_existing_process_still_waits_for_tunnel_up(self):
        self.existing_core();self.network(up_after=4)
        self.assertEqual(self.run_script().returncode,0);self.assert_ready()

    def test_link_drop_during_route_install_is_retried(self):
        self.network(up_after=1,route_failures=1)
        self.assertEqual(self.run_script().returncode,0);self.assert_ready()

    def test_timeout_keeps_unreachable_routes_and_reports_not_ready(self):
        self.network(up_after=1000)
        self.assertNotEqual(self.run_script().returncode,0)
        self.assertNotEqual(self.run_script('ready').returncode,0)
        for family in ('4','6'):
            self.assertEqual(self.network()['routes'][family],['unreachable default metric 42760'])
        self.assertFalse((self.root/'watch-starts').exists())

    def test_unready_process_remains_visible_to_stop_and_removal_callers(self):
        self.existing_core();self.network(up_after=1)
        self.assertEqual(self.run_script('status').returncode,0)
        self.assertNotEqual(self.run_script('ready').returncode,0)

    def test_busybox_ipv6_table_filter_uses_the_full_dump(self):
        self.network(up_after=1,ipv6_filter_bug=True)
        self.assertEqual(self.run_script().returncode,0)
        self.assert_ready()

    def test_full_ipv6_dump_does_not_accept_a_route_from_another_table(self):
        self.existing_core()
        self.network(up_after=1,ipv6_filter_bug=True,routes={
            '4':['default dev alisa-gpt metric 10'],
            '6':['unreachable default metric 42760']})
        self.assertNotEqual(self.run_script('ready').returncode,0)

    def test_busybox_full_dump_rejects_another_tools_ipv6_route_in_our_table(self):
        self.network(ipv6_filter_bug=True,routes={'6':['2001:db8::/64 dev other0 metric 5']})
        self.assertNotEqual(self.run_script().returncode,0)
        self.assertEqual(self.network()['routes'],{'6':['2001:db8::/64 dev other0 metric 5']})
        self.assertFalse((self.root/'core-starts').exists())

    def test_ipv6_route_failure_is_not_reported_as_ready_with_only_ipv4(self):
        self.network(up_after=1,route_failures=100,fail_family='6')
        self.assertNotEqual(self.run_script().returncode,0)
        self.assertNotEqual(self.run_script('ready').returncode,0)
        routes=self.network()['routes']
        self.assertIn('default dev alisa-gpt metric 10',routes['4'])
        self.assertEqual(routes['6'],['unreachable default metric 42760'])
        self.assertFalse((self.root/'watch-starts').exists())

    def boot_fixture(self,failures):
        self.tool('vpn.sh',f'''from pathlib import Path
p=Path({str(self.root/'boot-count')!r});n=int(p.read_text()) if p.exists() else 0
if sys.argv[1]=='start':n+=1;p.write_text(str(n));raise SystemExit(1 if n<={failures} else 0)
raise SystemExit(0 if n>{failures} else 1)
''',self.base)
        native=self.root/'native';native.mkdir()
        self.tool('run.sh',f'from pathlib import Path;Path({str(self.root/"client-started")!r}).touch()\n',native)
        for flag in ('vpn-autostart','vpn-required','native-autostart'):(self.base/flag).touch()

    def test_boot_retries_vpn_before_starting_native(self):
        self.boot_fixture(2)
        result=self.run_script('wait','boot.sh');self.assertEqual(result.returncode,0,result.stderr.decode())
        self.assertEqual((self.root/'boot-count').read_text(),'3')
        self.assertTrue((self.root/'client-started').exists())

    def test_boot_does_not_start_native_when_vpn_remains_unavailable(self):
        self.boot_fixture(10)
        self.assertNotEqual(self.run_script('wait','boot.sh').returncode,0)
        self.assertFalse((self.root/'client-started').exists())
        self.assertEqual((self.root/'boot-count').read_text(),'3')
