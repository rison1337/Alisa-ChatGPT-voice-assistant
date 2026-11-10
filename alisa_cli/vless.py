import base64,json,re,urllib.request,uuid
from urllib.parse import urlsplit,parse_qs,unquote,urlencode,quote

LINK_SCHEMES=('vless','hy2','hysteria2','tuic')

def is_proxy_link(source):
    return isinstance(source,str) and source.strip().split(':',1)[0].lower() in LINK_SCHEMES

def parse_proxy_link(source):
    scheme=urlsplit(source.strip()).scheme
    if scheme=='vless':return parse_link(source)
    if scheme in ('hy2','hysteria2'):return parse_hy2(source)
    if scheme=='tuic':return parse_tuic(source)
    raise ValueError('Поддерживаются ссылки VLESS, Hysteria 2 и TUIC')

class SubscriptionNotice(ValueError):pass

def label(value):
    # Names are displayed on the terminal, never interpreted as control sequences.
    if not isinstance(value,str):return ''
    return ''.join(c for c in value if c.isprintable())[:120]

def check_notice(value):
    text=label(value).lower()
    if any(x in text for x in ('app not supported','client not supported','unsupported app','unsupported client','приложение не поддерживается')):
        raise SubscriptionNotice('Провайдер вернул «App not supported» вместо серверов. Выберите формат Incy/Happ или экспортируйте из приложения отдельную ссылку VLESS. Доступные серверы не получены.')

def parse_link(link):
    try:
        u=urlsplit(link.strip());q={k:v[-1] for k,v in parse_qs(u.query,keep_blank_values=True).items()}
        if u.scheme!='vless' or not u.hostname or not u.username:raise ValueError()
        identity=str(uuid.UUID(unquote(u.username)));port=u.port or 443;server=u.hostname
        if not 1<=port<=65535 or re.search(r'[\s\x00-\x1f]',server):raise ValueError()
    except (ValueError,TypeError):raise ValueError('Некорректная ссылка VLESS') from None
    if (q.get('encryption') or 'none')!='none':raise ValueError('Неизвестное шифрование VLESS')
    transport=q.get('type') or 'tcp'
    if transport=='raw':transport='tcp'  # some panels spell plain TCP as "raw"
    security=q.get('security') or 'none'
    if transport not in ['tcp','ws','grpc','httpupgrade','xhttp']:raise ValueError('Транспорт VLESS пока не поддерживается: '+re.sub('[^a-z0-9]','',transport))
    if security not in ['none','tls','reality']:raise ValueError('Неизвестный режим TLS VLESS')
    result={'type':'vless','tag':'gpt-vless','server':server,'server_port':port,'uuid':identity,'packet_encoding':'xudp'}
    flow=q.get('flow','')
    if flow:
        if flow!='xtls-rprx-vision':raise ValueError('Поддерживается только flow xtls-rprx-vision')
        result['flow']=flow
    if security!='none':
        result['tls']={'enabled':True,'server_name':q.get('sni') or q.get('host') or server,
                       'utls':{'enabled':True,'fingerprint':q.get('fp') or 'chrome'}}
        if q.get('allowInsecure','0').lower() in ['1','true']:raise ValueError('Ссылка просит отключить проверку TLS; исправьте настройки сервера')
        if security=='reality':
            if not q.get('pbk'):raise ValueError('В ссылке Reality отсутствует публичный ключ')
            sid=q.get('sid','')
            if len(sid)>16 or len(sid)%2 or re.search('[^a-fA-F0-9]',sid):raise ValueError('Некорректный Reality short ID')
            result['tls']['reality']={'enabled':True,'public_key':q['pbk'],'short_id':sid}
    if transport=='ws':result['transport']={'type':'ws','path':q.get('path') or '/','headers':{'Host':q.get('host') or server}}
    elif transport=='grpc':result['transport']={'type':'grpc','service_name':q.get('serviceName','')}
    elif transport=='httpupgrade':result['transport']={'type':'httpupgrade','path':q.get('path') or '/','host':q.get('host') or server}
    elif transport=='xhttp':
        mode=q.get('mode') or 'auto'
        if mode not in ('auto','packet-up','stream-up','stream-one'):raise ValueError('Неизвестный режим XHTTP')
        extra={}
        if q.get('extra'):
            if len(q['extra'])>4096:raise ValueError('XHTTP extra слишком большой')
            try:extra=json.loads(q['extra'])
            except (ValueError,RecursionError):raise ValueError('Некорректный JSON XHTTP extra') from None
            # Do not allow another endpoint/security chain hidden in extra.
            if not isinstance(extra,dict) or set(extra)-{'xmux','scMaxEachPostBytes','scMinPostsIntervalMs','scMaxBufferedPosts','scStreamUpServerSecs','xPaddingBytes','noGRPCHeader','noSSEHeader'}:
                raise ValueError('XHTTP extra содержит неподдерживаемые параметры')
        result['_engine']='xray'
        result['transport']={'type':'xhttp','path':q.get('path') or '/','host':q.get('host',''),'mode':mode,'extra':extra}
    name=label(unquote(u.fragment))
    check_notice(name)
    if name:result['_name']=name
    return result

def parse_hy2(link):
    # Hysteria 2: hysteria2://password@host:port?sni=...&obfs=salamander&obfs-password=...
    try:
        u=urlsplit(link.strip());q={k:v[-1] for k,v in parse_qs(u.query,keep_blank_values=True).items()}
        if u.scheme not in ('hy2','hysteria2') or not u.hostname or not u.username:raise ValueError()
        # Hysteria userpass is one credential, including the literal colon.
        password=unquote(u.netloc.rsplit('@',1)[0]);port=u.port or 443;server=u.hostname
        if not 1<=port<=65535 or re.search(r'[\s\x00-\x1f]',server):raise ValueError()
    except (ValueError,TypeError):raise ValueError('Некорректная ссылка Hysteria 2') from None
    if q.get('insecure','0').lower() in ['1','true']:raise ValueError('Ссылка просит отключить проверку TLS; исправьте настройки сервера')
    result={'type':'hysteria2','tag':'gpt-vless','server':server,'server_port':port,'password':password,
            'tls':{'enabled':True,'server_name':q.get('sni') or q.get('peer') or server}}
    if q.get('alpn'):result['tls']['alpn']=[x for x in q['alpn'].split(',') if x]
    obfs=q.get('obfs')
    if obfs:
        if obfs!='salamander':raise ValueError('Неизвестное маскирование Hysteria 2: '+re.sub('[^a-z0-9]','',obfs))
        result['obfs']={'type':'salamander','password':q.get('obfs-password','')}
    name=label(unquote(u.fragment))
    check_notice(name)
    if name:result['_name']=name
    return result

def parse_tuic(link):
    # TUIC: tuic://uuid:password@host:port?sni=...&congestion_control=bbr&alpn=h3
    try:
        u=urlsplit(link.strip());q={k:v[-1] for k,v in parse_qs(u.query,keep_blank_values=True).items()}
        if u.scheme!='tuic' or not u.hostname or not u.username:raise ValueError()
        identity=str(uuid.UUID(unquote(u.username)));password=unquote(u.password or '')
        port=u.port or 443;server=u.hostname
        if not 1<=port<=65535 or re.search(r'[\s\x00-\x1f]',server):raise ValueError()
    except (ValueError,TypeError):raise ValueError('Некорректная ссылка TUIC') from None
    if not password:raise ValueError('В ссылке TUIC нет пароля')
    if q.get('allow_insecure','0').lower() in ['1','true']:raise ValueError('Ссылка просит отключить проверку TLS; исправьте настройки сервера')
    congestion=q.get('congestion_control') or 'bbr'
    if congestion not in ['bbr','cubic','new_reno']:raise ValueError('Неизвестный алгоритм управления перегрузкой TUIC')
    relay=q.get('udp_relay_mode','')
    if relay and relay not in ['native','quic']:raise ValueError('Неизвестный режим UDP TUIC')
    result={'type':'tuic','tag':'gpt-vless','server':server,'server_port':port,'uuid':identity,'password':password,
            'congestion_control':congestion,
            'tls':{'enabled':True,'server_name':q.get('sni') or server}}
    if relay:result['udp_relay_mode']=relay
    if q.get('alpn'):result['tls']['alpn']=[x for x in q['alpn'].split(',') if x]
    name=label(unquote(u.fragment))
    check_notice(name)
    if name:result['_name']=name
    return result

def from_fields(server,port,identity,query,name=''):
    if not isinstance(server,str) or re.search(r'[^a-zA-Z0-9.:%_-]',server):raise ValueError('Некорректный адрес сервера')
    if not isinstance(port,int) or isinstance(port,bool) or not 1<=port<=65535:raise ValueError('Некорректный порт сервера')
    if not isinstance(identity,str):raise ValueError('Некорректный UUID')
    host='['+server+']' if ':' in server else server
    result=parse_link('vless://'+identity+'@'+host+':'+str(port)+'?'+urlencode({k:v for k,v in query.items() if v is not None}))
    check_notice(name)
    if name:result['_name']=label(name)
    return result

def xray_outbound(item,name):
    settings=item.get('settings',{});stream=item.get('streamSettings',{})
    peers=settings.get('vnext') or [settings]
    for peer in peers:
        for user in peer.get('users') or [peer]:
            net=stream.get('network') or 'tcp'
            if net=='raw':net='tcp'
            sec=stream.get('security') or 'none'
            tls=stream.get('realitySettings' if sec=='reality' else 'tlsSettings',{})
            q={'type':net,'security':sec,'flow':user.get('flow'),'encryption':user.get('encryption') or 'none',
               'sni':tls.get('serverName'),'fp':tls.get('fingerprint'),'pbk':tls.get('publicKey'),
               'sid':tls.get('shortId'),'allowInsecure':str(tls.get('allowInsecure',False)).lower()}
            if net=='ws':
                ws=stream.get('wsSettings',{});q.update(path=ws.get('path'),host=ws.get('headers',{}).get('Host'))
            elif net=='grpc':q['serviceName']=stream.get('grpcSettings',{}).get('serviceName')
            elif net=='httpupgrade':
                tr=stream.get('httpupgradeSettings',{});q.update(path=tr.get('path'),host=tr.get('host'))
            elif net=='xhttp':
                tr=stream.get('xhttpSettings',{});q.update(path=tr.get('path'),host=tr.get('host'),mode=tr.get('mode'),extra=json.dumps(tr.get('extra',{})))
            # Server-side fragmentation, proxy chains and mux cannot be flattened
            # into an independent outbound without changing their semantics.
            if item.get('proxySettings') or stream.get('sockopt',{}).get('dialerProxy') or item.get('mux',{}).get('enabled'):
                raise ValueError('Составная конфигурация требует экспорт отдельного сервера')
            yield from_fields(peer['address'],peer['port'],user['id'],q,name)

def clash_outbound(item):
    tr=item.get('network') or 'tcp';reality=item.get('reality-opts') or {}
    q={'type':tr,'security':'reality' if reality else ('tls' if item.get('tls') else 'none'),
       'flow':item.get('flow'),'sni':item.get('servername') or item.get('sni'),
       'fp':item.get('client-fingerprint'),'pbk':reality.get('public-key'),'sid':reality.get('short-id'),
       'allowInsecure':str(item.get('skip-cert-verify',False)).lower()}
    if tr=='ws':
        ws=item.get('ws-opts',{});q.update(path=ws.get('path'),host=ws.get('headers',{}).get('Host'))
    elif tr=='grpc':q['serviceName']=item.get('grpc-opts',{}).get('grpc-service-name')
    if item.get('dialer-proxy'):raise ValueError('Цепочка прокси не поддерживается')
    return from_fields(item['server'],item['port'],item['uuid'],q,item.get('name',''))

def quic_outbound(item,kind):
    """Rebuild independent QUIC servers; never import routing or proxy chains."""
    if item.get('detour') or item.get('dialer-proxy') or item.get('proxySettings'):
        raise ValueError('Цепочка прокси не поддерживается')
    protocol=item['type']
    server=item['server'];port=item['port'] if kind=='clash' else item['server_port']
    if not isinstance(server,str) or not server or re.search(r'[^a-zA-Z0-9.:%_-]',server):
        raise ValueError('Некорректный адрес сервера')
    if not isinstance(port,int) or isinstance(port,bool) or not 1<=port<=65535:
        raise ValueError('Некорректный порт сервера')
    password=item.get('password')
    if not isinstance(password,str) or not password:raise ValueError('В сервере нет пароля')
    tls={} if kind=='clash' else item.get('tls') or {}
    if kind!='clash' and tls.get('enabled') is not True:raise ValueError('Для QUIC нужен TLS')
    insecure=item.get('skip-cert-verify',False) if kind=='clash' else tls.get('insecure',False)
    if insecure not in (False,None):raise ValueError('Отключение проверки TLS не поддерживается')
    alpn=item.get('alpn') if kind=='clash' else tls.get('alpn')
    if alpn is not None and (not isinstance(alpn,list) or not all(isinstance(x,str) and x and ',' not in x for x in alpn)):
        raise ValueError('Некорректный ALPN')
    query={'sni':(item.get('sni') or item.get('servername')) if kind=='clash' else tls.get('server_name')}
    if alpn:query['alpn']=','.join(alpn)
    if protocol in ('hy2','hysteria2'):
        if item.get('ports') or item.get('server_ports'):raise ValueError('Диапазон портов пока не поддерживается')
        obfs=item.get('obfs')
        if obfs:
            if kind=='clash':query.update(obfs=obfs,**{'obfs-password':item.get('obfs-password','')})
            else:query.update(obfs=obfs['type'],**{'obfs-password':obfs.get('password','')})
        user=quote(password,safe='');scheme='hysteria2'
    else:
        user=quote(str(uuid.UUID(item['uuid'])),safe='')+':'+quote(password,safe='');scheme='tuic'
        query.update(congestion_control=item.get('congestion-controller' if kind=='clash' else 'congestion_control'),
                     udp_relay_mode=item.get('udp-relay-mode' if kind=='clash' else 'udp_relay_mode'))
    host='['+server+']' if ':' in server else server
    result=parse_proxy_link(scheme+'://'+user+'@'+host+':'+str(port)+'?'+urlencode({k:v for k,v in query.items() if v is not None}))
    name=item.get('name' if kind=='clash' else 'tag','');check_notice(name)
    if name:result['_name']=label(name)
    return result

def structured(value):
    configs=value if isinstance(value,list) else [value];result=[];bad=0
    if len(configs)>256:raise ValueError('В подписке больше 256 конфигураций')
    for config in configs:
        if not isinstance(config,dict):bad+=1;continue
        check_notice(config.get('remarks',''))
        if 'proxies' in config:
            entries=config['proxies'];kind='clash'
        elif 'outbounds' in config:
            entries=config['outbounds'];kind='xray'
        else:bad+=1;continue
        if not isinstance(entries,list) or len(entries)>256:raise ValueError('Некорректный список серверов')
        for item in entries:
            if not isinstance(item,dict):bad+=1;continue
            protocol=item.get('protocol') or item.get('type')
            if protocol in ('freedom','blackhole','direct','block','dns'):continue
            if protocol not in ('vless','hy2','hysteria2','tuic'):bad+=1;continue
            try:
                if protocol!='vless':result.append(quic_outbound(item,kind))
                elif kind=='clash':result.append(clash_outbound(item))
                elif 'protocol' in item:result.extend(xray_outbound(item,config.get('remarks') or item.get('tag','')))
                else:
                    # sing-box JSON: rebuild fields rather than importing routing,
                    # arbitrary listeners, detours or a direct outbound.
                    tls=item.get('tls') or {};reality=tls.get('reality') or {};tr=item.get('transport') or {}
                    if item.get('detour'):raise ValueError('Цепочка прокси не поддерживается')
                    result.append(from_fields(item['server'],item['server_port'],item['uuid'],{
                      'type':tr.get('type','tcp'),'security':'reality' if reality.get('enabled') else ('tls' if tls.get('enabled') else 'none'),
                      'flow':item.get('flow'),'sni':tls.get('server_name'),'fp':tls.get('utls',{}).get('fingerprint'),
                      'pbk':reality.get('public_key'),'sid':reality.get('short_id'),'allowInsecure':str(tls.get('insecure',False)).lower(),
                      'path':tr.get('path'),'host':tr.get('host') or tr.get('headers',{}).get('Host'),'serviceName':tr.get('service_name')},item.get('tag','')))
            except SubscriptionNotice:raise
            except (ValueError,KeyError,TypeError,AttributeError):bad+=1
    return result,bad

def subscription(text):
    if len(text)>2*1024*1024:raise ValueError('Подписка слишком большая')
    text=text.strip()
    if not is_proxy_link(text):
        try:text=base64.b64decode(re.sub(r'\s','',text)+'='*(-len(re.sub(r'\s','',text))%4),altchars=b'-_',validate=True).decode()
        except (ValueError,UnicodeError):pass
    if text.startswith(('[','{')):
        try:result,rejected=structured(json.loads(text))
        except (json.JSONDecodeError,RecursionError):raise ValueError('Некорректный JSON подписки') from None
        if not result:raise ValueError('В JSON нет поддерживаемых независимых серверов VLESS/Hysteria 2/TUIC')
        return result,rejected
    if re.search(r'^proxies\s*:',text,re.M):
        from ._vendor import yaml
        try:result,rejected=structured(yaml.safe_load(text))
        except (yaml.YAMLError,RecursionError):raise ValueError('Некорректный YAML подписки') from None
        if not result:raise ValueError('В YAML нет поддерживаемых серверов VLESS/Hysteria 2/TUIC')
        return result,rejected
    links=[x.strip() for x in text.splitlines() if is_proxy_link(x)]
    if not links:raise ValueError('Нет ссылок VLESS/Hysteria 2/TUIC: поддерживаются текстовые и Base64-подписки')
    result=[];rejected=0
    for link in links[:256]:
        try:
            result.append(parse_proxy_link(link))
        except SubscriptionNotice:raise
        except ValueError:rejected+=1
    if not result:raise ValueError('Все серверы подписки используют неподдерживаемые параметры')
    return result,rejected

def fetch_subscription(url,client='standard'):
    if urlsplit(url).scheme!='https':raise ValueError('Для подписки нужна ссылка HTTPS')
    class HTTPSOnly(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            if urlsplit(newurl).scheme!='https':raise ValueError('Подписка перенаправляет на незащищённый адрес')
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    profiles={'standard':{'User-Agent':'AlisaVoice/0.1','Accept':'text/plain, application/json'},
              'incy':{'User-Agent':'INCY/1.0.0/Linux','Accept':'*/*','x-client':'INCY','x-app-version':'1.0.0','x-device-locale':'ru-RU'},
              'happ':{'User-Agent':'Happ/3.12.0/Linux','Accept':'*/*'}}
    if client not in profiles:raise ValueError('Неизвестный формат подписки')
    request=urllib.request.Request(url,headers=profiles[client])
    with urllib.request.build_opener(HTTPSOnly).open(request,timeout=15) as response:
        raw=response.read(2*1024*1024+1)
    if len(raw)>2*1024*1024:raise ValueError('Подписка слишком большая')
    return subscription(raw.decode('utf-8-sig'))

def core_config(outbound):
    if outbound.get('_engine')=='xray':return xray_config(outbound)
    return {'log':{'level':'warn','timestamp':True},
      'inbounds':[{'type':'tun','tag':'gpt-tun','interface_name':'alisa-gpt','address':['172.31.254.1/30','fdfe:4150::1/126'],
                   'auto_route':False,'mtu':1400,'stack':'gvisor'},
                  {'type':'socks','tag':'gpt-socks','listen':'127.0.0.1','listen_port':18081}],
      'outbounds':[{k:v for k,v in outbound.items() if not k.startswith('_')}],'route':{'final':'gpt-vless','auto_detect_interface':True}}

# Public "no content" endpoint: request latency through a server is the honest
# measure of its speed; ping (ICMP) says nothing about the proxied path.
PROBE_URL='https://www.gstatic.com/generate_204'

def auto_config(nodes,url=None):
    # One outbound per server plus a urltest group tagged like the single
    # outbound used to be, so routes and health checks stay unchanged.
    items=[];tags=[]
    for index,item in enumerate(nodes):
        node={k:v for k,v in item.items() if not k.startswith('_')}
        node['tag']='gpt-node-'+str(index)
        tags.append(node['tag']);items.append(node)
    if not tags:raise ValueError('Нет серверов для автоматического выбора')
    items.append({'type':'urltest','tag':'gpt-vless','outbounds':tags,'url':url or PROBE_URL,
                  'interval':'3m','tolerance':50})
    return {'log':{'level':'warn','timestamp':True},
      'inbounds':[{'type':'tun','tag':'gpt-tun','interface_name':'alisa-gpt','address':['172.31.254.1/30','fdfe:4150::1/126'],
                   'auto_route':False,'mtu':1400,'stack':'gvisor'},
                  {'type':'socks','tag':'gpt-socks','listen':'127.0.0.1','listen_port':18081}],
      'outbounds':items,'route':{'final':'gpt-vless','auto_detect_interface':True}}

def xray_config(outbound):
    transport=outbound.get('transport',{});tls=outbound.get('tls',{})
    stream={'network':'xhttp','security':'reality' if tls.get('reality',{}).get('enabled') else ('tls' if tls.get('enabled') else 'none'),
            'xhttpSettings':{k:v for k,v in transport.items() if k!='type'}}
    if tls.get('enabled'):
        settings={'serverName':tls.get('server_name'),'fingerprint':tls.get('utls',{}).get('fingerprint','chrome')}
        if stream['security']=='reality':
            settings.update(publicKey=tls['reality']['public_key'],shortId=tls['reality']['short_id'])
        stream['realitySettings' if stream['security']=='reality' else 'tlsSettings']=settings
    user={'id':outbound['uuid'],'encryption':'none'}
    if outbound.get('flow'):user['flow']=outbound['flow']
    return {'log':{'loglevel':'warning'},
            'inbounds':[{'tag':'gpt-tun','protocol':'tun','settings':{'name':'alisa-gpt','mtu':1400,'gateway':['172.31.254.1/30','fdfe:4150::1/126']}},
                        {'tag':'gpt-socks','protocol':'socks','listen':'127.0.0.1','port':18081,'settings':{'auth':'noauth','udp':True}}],
            'outbounds':[{'tag':'gpt-vless','protocol':'vless','settings':{'vnext':[{'address':outbound['server'],'port':outbound['server_port'],'users':[user]}]},'streamSettings':stream}]}
