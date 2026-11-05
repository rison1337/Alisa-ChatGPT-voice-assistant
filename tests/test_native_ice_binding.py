"""Run the actual launcher in a private fixture, with no station or audio."""
import os,subprocess,tempfile,unittest
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
        self.assertEqual(bound,'192.0.2.44');self.assertTrue(queried)

    def test_address_is_reselected_when_the_route_changes(self):
        bound,_=self.launch('1.1.1.1 via 198.51.100.1 dev eth0 src 198.51.100.28')
        self.assertEqual(bound,'198.51.100.28')

    def test_explicit_vpn_binding_is_preserved_without_route_lookup(self):
        bound,queried=self.launch('1.1.1.1 dev wlan0 src 192.0.2.44',explicit='172.31.254.1')
        self.assertEqual(bound,'172.31.254.1');self.assertFalse(queried)

    def test_no_ipv4_route_retains_unbound_fallback(self):
        bound,_=self.launch('',route_exit=2)
        self.assertEqual(bound,'unset')

    def test_unexpected_route_output_is_not_used_as_an_address(self):
        bound,_=self.launch('1.1.1.1 dev wlan0 src invalid-address')
        self.assertEqual(bound,'unset')
