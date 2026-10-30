import base64,ctypes,json,os,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import state,network,vless,autostart
from alisa_cli.ssh import Station
ROOT=Path(__file__).resolve().parents[1]
LINK='vless://00000000-0000-4000-8000-000000000001@example.com:443?security=reality&sni=example.com&pbk=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&sid=0123456789abcdef&flow=xtls-rprx-vision&type=tcp'
def configured(mode):
 value=state.defaults(mode);value['host']='192.0.2.20';return value

class ConfigTests(unittest.TestCase):
 def test_toggle_checks_current_runtime_before_stopping_or_starting(self):
  from alisa_cli import cli
  for mode,current,running in [('native',{'agent_connected':True,'phase':'idle'},True),('native',{'phase':'stopped'},False),('bridge',{'phase':'running'},True)]:
   with self.subTest(mode=mode,current=current),patch.object(cli.runtime,'status',return_value=current),patch.object(cli.runtime,'stop') as stop,patch.object(cli,'start_mode',return_value={'phase':'idle'}) as start:
    cli.toggle_mode(configured(mode))
    self.assertEqual(stop.call_count,int(running));self.assertEqual(start.call_count,int(not running))
 def test_one_start_action_starts_required_vpn_first(self):
  from alisa_cli import cli
  c=configured('native');c['installed']=True;c['vpn']['enabled']=True;calls=[]
  def vpn_action(config,action):calls.append(action);return {'running':False} if action=='status' else {'running':True}
  with patch.object(cli.vpn,'operate',side_effect=vpn_action),patch.object(cli.runtime,'start',side_effect=lambda *a,**k:calls.append('gpt') or {'phase':'idle'}):cli.start_mode(c)
  self.assertEqual(calls,['status','start','gpt'])
 def test_required_vpn_failure_never_starts_direct_gpt(self):
  from alisa_cli import cli
  c=configured('native');c['installed']=True;c['vpn']['enabled']=True
  with patch.object(cli.vpn,'operate',side_effect=[{'running':False},RuntimeError('VPN unavailable')]),patch.object(cli.runtime,'start') as start:
   with self.assertRaises(RuntimeError):cli.start_mode(c)
   start.assert_not_called()
 def test_stopped_native_is_a_readable_state_not_a_protocol_error(self):
  from alisa_cli import runtime
  value=subprocess.CompletedProcess([],2,stdout=b'{"ok":false,"error":"control_unavailable"}',stderr=b'')
  with patch('alisa_cli.runtime.Station.run',return_value=value):result=runtime.status(configured('native'))
  self.assertEqual(result['phase'],'stopped');self.assertFalse(result['agent_connected']);self.assertFalse(result['error'])
 def test_ssh_alias_overrides_are_resolved_before_renaming(self):
  c=configured('bridge');c['user']='root'
  resolved=b'hostname 192.0.2.20\nuser root\nport 2222\nidentityfile /tmp/alisa_key\n'
  with tempfile.TemporaryDirectory() as folder,patch('alisa_cli.ssh.HOME',Path(folder)),patch('alisa_cli.ssh.subprocess.run',return_value=subprocess.CompletedProcess([],0,stdout=resolved)):
   alias,path=Station(c).configure_bridge();text=Path(path).read_text()
   self.assertEqual(alias,'alisa-cli');self.assertIn('hostname 192.0.2.20',text);self.assertIn('port 2222',text)
   self.assertNotIn('HostName "alice"',text)
 def test_failed_browser_open_cannot_pass_verify(self):
  from alisa_cli import cli
  failed=subprocess.CompletedProcess([],0,stdout=b'{"ok": false, "error": "quota"}',stderr=b'')
  with patch('alisa_cli.runtime.bridge_env',return_value=({},'alice')),patch('alisa_cli.cli.subprocess.run',return_value=failed):
   with self.assertRaises(RuntimeError):cli.verify(state.defaults('bridge'))
 def test_separate_modes_and_secrets_permissions(self):
  with tempfile.TemporaryDirectory() as folder,patch.object(state,'HOME',Path(folder)):
   a=configured('native');b=configured('bridge');a['led']='red';state.save(a);state.save(b)
   self.assertEqual(state.load('native')['led'],'red');self.assertEqual(state.load('bridge')['led'],'rainbow')
   self.assertEqual((Path(folder)/'native/config.json').stat().st_mode&0o777,0o600)
 def test_exact_normalized_unique_phrases(self):
  self.assertEqual(state.normalize_phrase('Алиса, ОТКРОЙ test 123!'),'открой test 123')
  p=dict(state.DEFAULT_PHRASES);p['close']=p['open']
  with self.assertRaises(ValueError):state.command_file(p)
  self.assertNotIn(b'$',state.command_file(state.DEFAULT_PHRASES))
 def test_ssh_host_cannot_be_option(self):
  for value in ['-oProxyCommand=id','alice\nroot','']:
   c=state.defaults('native');c['host']=value
   with self.assertRaises(ValueError):state.validate(c)
 def test_hostkey_checks_remain_enabled(self):
  args=Station(configured('native')).args()
  self.assertIn('StrictHostKeyChecking=yes',args);self.assertIn('BatchMode=yes',args)
 def test_country_not_voice_guarantee(self):
  self.assertEqual(network.classify('RU'),'Требуется VPN')
  self.assertEqual(network.classify('US'),'Пройдена')
  self.assertEqual(network.classify(None),'Не удалось определить страну IP')
 def test_unsupported_firmware_reports_compatibility_in_plain_language(self):
  from alisa_cli import doctor
  report={'ok':True,'root':True,'plum':True,'firmware_abi_supported':False,'firmware_files_present':True,'free_data_kib':50000,'host_missing':[],'station_missing':[]}
  with patch('alisa_cli.ui.note') as note:doctor.require(configured('native'),report=report)
  self.assertIn('Установка разрешена',note.call_args.args[0])
 def test_systemd_escapes_paths(self):
  with patch.object(autostart,'ROOT',Path('/tmp/a project%name')):
   text=autostart.unit_text(state.defaults('bridge'))
  self.assertIn('%%name',text);self.assertIn('bridge run',text);self.assertNotIn('enable --now',text)

class VlessTests(unittest.TestCase):
 def test_xhttp_uses_one_xray_with_manual_tun_routes(self):
  raw=LINK.replace('type=tcp','type=xhttp').replace('&flow=xtls-rprx-vision','')+'&path=%2Fgpt&mode=auto&extra=%7B%22xmux%22%3A%7B%22maxConnections%22%3A2%7D%7D'
  outbound=vless.parse_link(raw);self.assertEqual(outbound['_engine'],'xray')
  config=vless.core_config(outbound);tun=config['inbounds'][0]['settings']
  self.assertNotIn('autoSystemRoutingTable',tun);self.assertEqual(tun['name'],'alisa-gpt')
  self.assertEqual(len(config['outbounds']),1);stream=config['outbounds'][0]['streamSettings']
  self.assertEqual(stream['network'],'xhttp');self.assertEqual(stream['xhttpSettings']['extra']['xmux']['maxConnections'],2)
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'xray.json';path.write_text(json.dumps(config))
   p=subprocess.run([ROOT/'assets/vpn/linux-amd64/xray','run','-test','-c',path],capture_output=True)
   self.assertEqual(p.returncode,0)
 def test_xhttp_extra_cannot_introduce_another_download_endpoint(self):
  raw=LINK.replace('type=tcp','type=xhttp')+'&extra=%7B%22downloadSettings%22%3A%7B%22address%22%3A%22example.com%22%7D%7D'
  with self.assertRaises(ValueError):vless.parse_link(raw)
 def test_structured_subscription_preserves_servers_not_provider_routes(self):
  peer={'address':'example.com','port':443,'users':[{'id':'00000000-0000-4000-8000-000000000001','encryption':'none'}]}
  raw=[{'remarks':'Server A','outbounds':[{'protocol':'vless','settings':{'vnext':[peer]},'streamSettings':{'security':'tls'}},{'protocol':'freedom'}]},
       {'remarks':'Server B','outbounds':[{'protocol':'vless','settings':{'vnext':[peer]},'streamSettings':{'security':'tls','network':'grpc','grpcSettings':{'serviceName':'voice'}}}]}]
  servers,bad=vless.subscription(json.dumps(raw));self.assertEqual(len(servers),2);self.assertEqual(bad,0)
  self.assertEqual(servers[1]['_name'],'Server B');self.assertEqual(servers[1]['transport']['service_name'],'voice')
  self.assertNotIn('_name',vless.core_config(servers[1])['outbounds'][0])
 def test_clash_yaml_and_invalid_entries(self):
  raw='proxies:\n - name: Helsinki\n   type: vless\n   server: example.com\n   port: 443\n   uuid: 00000000-0000-4000-8000-000000000001\n   tls: true\n - name: unsupported\n   type: vmess\n'
  servers,bad=vless.subscription(raw);self.assertEqual(len(servers),1);self.assertEqual(bad,1);self.assertEqual(servers[0]['_name'],'Helsinki')
 def test_notices_are_never_offered_as_servers(self):
  cases=[LINK+'#App%20not%20supported',json.dumps([{'remarks':'App not supported','outbounds':[]}]),'proxies:\n - name: App not supported\n   type: vless\n   server: example.com\n   port: 443\n   uuid: 00000000-0000-4000-8000-000000000001\n']
  for value in cases:
   with self.subTest(value=value),self.assertRaises(vless.SubscriptionNotice):vless.subscription(value)
 def test_subscription_failure_does_not_touch_live_device(self):
  from alisa_cli import vpn
  with patch.object(vpn,'fetch_subscription',side_effect=vless.SubscriptionNotice('notice')),patch.object(vpn,'Station') as station:
   with self.assertRaises(vless.SubscriptionNotice):vpn.configure(state.defaults('native'),'https://example.com/sub')
   station.assert_not_called()
 def test_reality_udp_and_no_global_auto_route(self):
  outbound=vless.parse_link(LINK);config=vless.core_config(outbound)
  self.assertEqual(outbound['packet_encoding'],'xudp');self.assertEqual(outbound['tls']['reality']['short_id'],'0123456789abcdef')
  self.assertFalse(config['inbounds'][0]['auto_route']);self.assertEqual(config['inbounds'][1]['listen'],'127.0.0.1')
  self.assertEqual(config['route']['final'],'gpt-vless');self.assertEqual(len(config['outbounds']),1)
 def test_plain_and_base64_subscription(self):
  for text in [LINK,base64.b64encode((LINK+'\n'+LINK).encode()).decode()]:
   servers,bad=vless.subscription(text);self.assertTrue(servers);self.assertEqual(bad,0)
 def test_reject_unknown_transport_and_insecure_tls(self):
  for text in [LINK.replace('type=tcp','type=kcp'),LINK+'&allowInsecure=1','vless://bad@server:123']:
   with self.assertRaises(ValueError):vless.parse_link(text)
 def test_validate_with_actual_pinned_core(self):
  core=ROOT/'assets/vpn/linux-amd64/sing-box'
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'core.json';p.write_text(json.dumps(vless.core_config(vless.parse_link(LINK))))
   r=subprocess.run([core,'check','-c',p],capture_output=True)
   self.assertEqual(r.returncode,0,r.stderr.decode())

class NativeCommands(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();cls.library=Path(cls.temp.name)/'commands.so'
  subprocess.run(['cc','-shared','-fPIC','-O2',ROOT/'engines/native/station/native_agent.c','-o',cls.library],check=True)
  cls.lib=ctypes.CDLL(str(cls.library));cls.lib.alice_native_command.argtypes=[ctypes.c_char_p,ctypes.c_size_t]
 @classmethod
 def tearDownClass(cls):cls.temp.cleanup()
 def test_custom_exact_commands_and_old_phrase_disabled(self):
  p=Path(self.temp.name)/'commands.txt';phrases=dict(state.DEFAULT_PHRASES);phrases['open']='поговорим с гпт';p.write_bytes(state.command_file(phrases))
  with patch.dict(os.environ,{'ALISA_COMMANDS_FILE':str(p)}):
   for text,expected in [('Алиса, поговорим с гпт',1),('открой чат',0),('закрой чат',3),('открой предыдущий чат',4),('поговорим с гпт и включи музыку',0)]:
    raw=text.encode();self.assertEqual(self.lib.alice_native_command(raw,len(raw)),expected,text)
   # Change the file while this same agent library remains loaded.
   phrases['open']='открой разговор';p.write_bytes(state.command_file(phrases))
   for text,expected in [('поговорим с гпт',0),('открой разговор',1)]:
    raw=text.encode();self.assertEqual(self.lib.alice_native_command(raw,len(raw)),expected)

class ShellTests(unittest.TestCase):
 def test_shell_syntax_and_persistent_blackhole(self):
  for script in (ROOT/'station').glob('*.sh'):subprocess.run(['sh','-n',script],check=True)
  text=(ROOT/'station/vpn.sh').read_text()
  self.assertIn('unreachable default',text);self.assertIn('fwmark "$MARK"',text)
  self.assertNotIn('iptables -F',text);self.assertNotIn('ip route flush',text)

if __name__=='__main__':unittest.main()
