from __future__ import annotations
import json,os,re,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
HOME=Path(os.environ.get('ALISA_HOME',Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))/'AlisaVoice/state' if os.name=='nt' else Path.home()/'.local/state/alisa-voice'))
MODES=('native','bridge')
STYLES=('rainbow','red','blue','green','purple','white','pulse-red','pulse-blue','pulse-green','pulse-purple','pulse-white','off')
STYLE_NAMES={'rainbow':'Радуга','red':'Красный','blue':'Синий','green':'Зелёный','purple':'Фиолетовый','white':'Белый','off':'Выключена'}
VOICES=('breeze','cove','ember','fathom','glimmer','juniper','maple','orbit','vale')
DEFAULT_PHRASES={'open':'открой чат','close':'закрой чат','new':'создай новый чат','previous':'открой предыдущий чат'}

def write_private(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if os.name=='nt':
        from .windows import private_directory
        private_directory(path.parent)
    data=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode() if not isinstance(value,bytes) else value
    fd,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        if hasattr(os,'fchmod'):os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def defaults(mode):
    if mode not in MODES:raise ValueError('Выберите native или bridge')
    return {'version':1,'mode':mode,'host':'','user':'root','port':2222,'identity':None,
        'autostart':False,'led':'rainbow','phrases':dict(DEFAULT_PHRASES),'installed':False,
        'vpn':{'enabled':False,'autostart':False,'required':False}}

def load(mode):
    p=HOME/mode/'config.json'
    if not p.exists():return defaults(mode)
    value=json.loads(p.read_text(encoding='utf-8'));validate(value);return value

def save(value):
    validate(value);write_private(HOME/value['mode']/'config.json',value)

def normalize_phrase(value):
    value=value.lower().replace('ё','е')
    value=re.sub(r'[^a-zа-я0-9]+',' ',value).strip()
    if value.startswith('алиса '):value=value[6:]
    value=value.removesuffix(' пожалуйста').strip()
    if value=='алиса':raise ValueError('Укажите слова после обращения Алиса')
    if not value or len(value.encode())>200:raise ValueError('Фраза должна содержать от 1 до 200 байт текста')
    return value

def command_file(phrases):
    values={k:normalize_phrase(v) for k,v in phrases.items()}
    if len(set(values.values()))!=len(values):raise ValueError('Фразы действий должны отличаться')
    return ''.join(f'{number}\t{values[key]}\n' for key,number in [('open',1),('new',2),('close',3),('previous',4)]).encode()

def validate(value):
    if 'password' in value:raise ValueError('Пароль SSH не сохраняется в настройках')
    if value.get('mode') not in MODES:raise ValueError('Неизвестный режим')
    host=value.get('host','')
    if not isinstance(host,str) or not host or host.startswith('-') or re.search(r'\s|[\x00-\x1f]',host):raise ValueError('Некорректный SSH-адрес')
    if value.get('port') is not None and (not isinstance(value['port'],int) or not 1<=value['port']<=65535):raise ValueError('Некорректный SSH-порт')
    if value.get('user') and not re.fullmatch('[a-zA-Z0-9_-]+',value['user']):raise ValueError('Некорректный SSH-пользователь')
    if value.get('led') not in STYLES:raise ValueError('Неизвестный цвет/анимация')
    command_file(value['phrases'])
