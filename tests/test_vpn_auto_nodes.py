"""Hysteria 2 and TUIC subscription links and the automatic server choice."""
import unittest
from unittest.mock import patch
from alisa_cli import vless,vpn

VLESS_LINK='vless://00000000-0000-4000-8000-000000000001@node1.example.com:443?type=tcp&security=tls&sni=node1.example.com&flow=xtls-rprx-vision#First'
HY2_LINK='hysteria2://secret-value@node2.example.com:8443?sni=node2.example.com#Second'
TUIC_LINK='tuic://00000000-0000-4000-8000-000000000002:secret-value@node3.example.com:443?sni=node3.example.com&congestion_control=bbr#Third'
XHTTP_LINK='vless://00000000-0000-4000-8000-000000000003@node4.example.com:443?type=xhttp&security=reality&pbk=public-key-fixture&sid=abcd#Fourth'

class SubscriptionSchemes(unittest.TestCase):
 def test_hysteria2_link_becomes_a_sing_box_outbound(self):
  item=vless.parse_hy2(HY2_LINK)
  self.assertEqual(item['type'],'hysteria2');self.assertEqual(item['server'],'node2.example.com')
  self.assertEqual(item['server_port'],8443);self.assertEqual(item['password'],'secret-value')
  self.assertEqual(item['tls'],{'enabled':True,'server_name':'node2.example.com'})
  self.assertEqual(item['_name'],'Second')

 def test_hysteria2_rejects_disabled_certificate_checks(self):
  with self.assertRaisesRegex(ValueError,'отключить проверку TLS'):vless.parse_hy2(HY2_LINK.split('#')[0]+'&insecure=1')

 def test_hysteria2_salamander_obfuscation_is_optional(self):
  item=vless.parse_hy2(HY2_LINK.split('#')[0]+'&obfs=salamander&obfs-password=mask#Second')
  self.assertEqual(item['obfs'],{'type':'salamander','password':'mask'})
  with self.assertRaisesRegex(ValueError,'маскирование'):vless.parse_hy2(HY2_LINK.split('#')[0]+'&obfs=unknown')

 def test_tuic_link_becomes_a_sing_box_outbound(self):
  item=vless.parse_tuic(TUIC_LINK)
  self.assertEqual(item['type'],'tuic');self.assertEqual(item['server'],'node3.example.com')
  self.assertEqual(item['uuid'],'00000000-0000-4000-8000-000000000002');self.assertEqual(item['password'],'secret-value')
  self.assertEqual(item['congestion_control'],'bbr');self.assertEqual(item['tls']['server_name'],'node3.example.com')
  self.assertEqual(item['_name'],'Third')

 def test_tuic_rejects_disabled_certificate_checks_and_unknown_modes(self):
  base=TUIC_LINK.split('#')[0]
  with self.assertRaisesRegex(ValueError,'отключить проверку TLS'):vless.parse_tuic(base+'&allow_insecure=1')
  with self.assertRaisesRegex(ValueError,'управления перегрузкой'):vless.parse_tuic(base+'&congestion_control=unknown')
  with self.assertRaisesRegex(ValueError,'режим UDP'):vless.parse_tuic(base+'&udp_relay_mode=unknown')

 def test_raw_transport_is_plain_tcp(self):
  item=vless.parse_link(VLESS_LINK.replace('type=tcp','type=raw'))
  self.assertNotIn('transport',item);self.assertEqual(item['server'],'node1.example.com')

 def test_subscription_mixes_supported_schemes_and_counts_rejections(self):
  broken=VLESS_LINK.replace('type=tcp','type=raw').replace('flow=xtls-rprx-vision','flow=unsupported-flow')
  items,rejected=vless.subscription('\n'.join([VLESS_LINK,HY2_LINK,TUIC_LINK,broken,'vmess://ignored@example.com:443#Nope']))
  self.assertEqual([i['type'] for i in items],['vless','hysteria2','tuic'])
  self.assertEqual(rejected,1)

 def test_provider_notice_inside_names_is_still_rejected(self):
  with self.assertRaisesRegex(vless.SubscriptionNotice,'App not supported'):
   vless.subscription(HY2_LINK.replace('Second','app not supported'))

class AutomaticChoice(unittest.TestCase):
 def test_auto_config_groups_servers_and_keeps_the_legacy_tag(self):
  nodes=[vless.parse_link(VLESS_LINK),vless.parse_hy2(HY2_LINK),vless.parse_tuic(TUIC_LINK)]
  config=vless.auto_config(nodes,url='https://probe.example.com/generate_204')
  tags=[item['tag'] for item in config['outbounds']]
  self.assertEqual(tags,['gpt-node-0','gpt-node-1','gpt-node-2','gpt-vless'])
  group=config['outbounds'][-1]
  self.assertEqual(group['type'],'urltest');self.assertEqual(group['outbounds'],tags[:3])
  self.assertEqual(group['url'],'https://probe.example.com/generate_204')
  self.assertEqual(config['route'],{'final':'gpt-vless','auto_detect_interface':True})
  self.assertEqual([i['tag'] for i in config['inbounds']],['gpt-tun','gpt-socks'])
  self.assertEqual(config['outbounds'][0]['type'],'vless');self.assertNotIn('_name',config['outbounds'][0])

 def test_auto_choice_skips_xray_only_servers(self):
  servers,rejected=vless.subscription('\n'.join([VLESS_LINK,HY2_LINK,TUIC_LINK,XHTTP_LINK]))
  self.assertEqual(servers[3].get('_engine'),'xray')
  with patch.object(vpn,'fetch_subscription',return_value=(servers,rejected)):
   selection=vpn.select_server('https://subscription.example.com/list',index='auto')
  self.assertEqual([s['type'] for s in selection['auto']],['vless','hysteria2','tuic'])
  self.assertEqual(selection['selected']['server'],'node1.example.com')

 def test_auto_choice_requires_two_servers(self):
  servers,rejected=vless.subscription(VLESS_LINK)
  with patch.object(vpn,'fetch_subscription',return_value=(servers,rejected)):
   with self.assertRaisesRegex(ValueError,'минимум два'):vpn.select_server('https://subscription.example.com/list',index='auto')

 def test_single_server_still_returns_one_outbound(self):
  config=vless.core_config(vless.parse_link(VLESS_LINK))
  self.assertEqual(len(config['outbounds']),1);self.assertEqual(config['outbounds'][0]['tag'],'gpt-vless')

 def test_auto_config_without_servers_is_refused(self):
  with self.assertRaisesRegex(ValueError,'Нет серверов'):vless.auto_config([])

if __name__=='__main__':unittest.main()
