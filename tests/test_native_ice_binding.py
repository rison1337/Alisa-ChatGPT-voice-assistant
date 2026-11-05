"""Run the actual launcher in a private fixture, with no station or audio."""
import os,socket,subprocess,tempfile,time,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class NativeIceBinding(unittest.TestCase):
    def launch(self,route,explicit=None,route_exit=0):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);base=root/'native';runtime=root/'runtime';tools=root/'tools'
            (base/'bin').mkdir(parents=True);tools.mkdir()
            script=(ROOT/'engines/native/station/run.sh').read_text()
            script=script.replace('BASE=/data/opt/alisa-standalone','BASE='+str(base)).replace('RUNTIME=/tmp/alisa-standalone','RUNTIME='+str(runtime))
            launcher=root/'run.sh';launcher.write_text(script)
            profile=base/'profile.sh';profile.write_text('#!/bin/sh\nexit 0\n');profile.chmod(0o700)
            binary=base/'bin/alisa-standalone'
            binary.write_text('''#!/bin/sh
case "$1" in
serve) printf '%s' "${ALISA_ICE_BIND_ADDRESS:-unset}" > "$FIXTURE/bound"; sleep .5 ;;
status) echo '{"agent_connected": true}' ;;
esac
''');binary.chmod(0o700)
            ip=tools/'ip'
            ip.write_text('''#!/bin/sh
echo queried > "$FIXTURE/queried"
printf '%s\\n' "$FIXTURE_ROUTE"
exit "$FIXTURE_ROUTE_EXIT"
''');ip.chmod(0o700)
            if explicit is not None:(base/'runtime.env').write_text('export ALISA_ICE_BIND_ADDRESS='+explicit+'\n')
            env={**os.environ,'PATH':str(tools)+os.pathsep+os.environ['PATH'],'FIXTURE':str(root),'FIXTURE_ROUTE':route,'FIXTURE_ROUTE_EXIT':str(route_exit)}
            env.pop('ALISA_ICE_BIND_ADDRESS',None)
            result=subprocess.run(['sh',str(launcher),'monitor'],env=env,capture_output=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            return (root/'bound').read_text(),(root/'queried').exists()

    def test_default_ipv4_route_binds_the_actual_client(self):
        bound,queried=self.launch('1.1.1.1 via 192.0.2.1 dev wlan0 src 192.0.2.44 uid 0\n    cache')
        self.assertEqual(bound,'0.0.0.0');self.assertTrue(queried)

    def test_default_binding_does_not_pin_an_interface_address(self):
        bound,_=self.launch('1.1.1.1 via 198.51.100.1 dev eth0 src 198.51.100.28')
        self.assertEqual(bound,'0.0.0.0')

    def test_explicit_vpn_binding_is_preserved_without_route_lookup(self):
        bound,queried=self.launch('1.1.1.1 dev wlan0 src 192.0.2.44',explicit='172.31.254.1')
        self.assertEqual(bound,'172.31.254.1');self.assertFalse(queried)

    def test_no_ipv4_route_retains_unbound_fallback(self):
        bound,_=self.launch('',route_exit=2)
        self.assertEqual(bound,'unset')

    def test_ipv4_wildcard_is_a_valid_socket_binding(self):
        bound,_=self.launch('1.1.1.1 dev wlan0 src 192.0.2.44')
        # 192.0.2.44 is a fixture route, not an address configured on this host.
        # Binding the discovered interface address here would fail.
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
            udp.bind((bound,0))
            self.assertEqual(udp.getsockname()[0],'0.0.0.0')

    def test_unexpected_route_output_is_not_used_as_an_address(self):
        bound,_=self.launch('1.1.1.1 dev wlan0 src invalid-address')
        self.assertEqual(bound,'unset')

    def test_route_change_between_calls_of_one_live_daemon_does_not_keep_the_old_ip(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);base=root/'native';runtime=root/'runtime';tools=root/'tools'
            (base/'bin').mkdir(parents=True);tools.mkdir()
            script=(ROOT/'engines/native/station/run.sh').read_text()
            script=script.replace('BASE=/data/opt/alisa-standalone','BASE='+str(base)).replace('RUNTIME=/tmp/alisa-standalone','RUNTIME='+str(runtime))
            launcher=root/'run.sh';launcher.write_text(script)
            profile=base/'profile.sh';profile.write_text('#!/bin/sh\nexit 0\n');profile.chmod(0o700)
            binary=base/'bin/alisa-standalone'
            binary.write_text('''#!/bin/sh
case "$1" in
serve)
    printf '%s' "${ALISA_ICE_BIND_ADDRESS:-unset}" > "$FIXTURE/first-call"
    while [ ! -f "$FIXTURE/second-call-ready" ]; do sleep .02; done
    printf '%s' "${ALISA_ICE_BIND_ADDRESS:-unset}" > "$FIXTURE/second-call"
    ;;
status) echo '{"agent_connected": true}' ;;
esac
''');binary.chmod(0o700)
            ip=tools/'ip';ip.write_text('#!/bin/sh\ncat "$FIXTURE/route"\n');ip.chmod(0o700)
            (root/'route').write_text('1.1.1.1 dev wlan0 src 192.0.2.44\n')
            env={**os.environ,'PATH':str(tools)+os.pathsep+os.environ['PATH'],'FIXTURE':str(root)}
            env.pop('ALISA_ICE_BIND_ADDRESS',None)
            process=subprocess.Popen(['sh',str(launcher),'monitor'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+3
                while not (root/'first-call').exists():
                    if process.poll() is not None or time.monotonic()>deadline:self.fail('fixture daemon did not start')
                    time.sleep(.02)
                (root/'route').write_text('1.1.1.1 dev wlan0 src 198.51.100.28\n')
                (root/'second-call-ready').touch()
                _,error=process.communicate(timeout=5)
                self.assertEqual(process.returncode,0,error.decode())
                for name in ('first-call','second-call'):
                    bound=(root/name).read_text()
                    self.assertEqual(bound,'0.0.0.0')
                    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:udp.bind((bound,0))
            finally:
                if process.poll() is None:process.terminate();process.communicate(timeout=5)
