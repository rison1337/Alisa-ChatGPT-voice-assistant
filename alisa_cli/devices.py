"""Native device selection: separate state roots and a fresh CLI process."""
import contextlib,hashlib,json,os,re,sys,uuid
from . import state,ui
from .firmware import MODEL_NAMES
SWITCHED=76


class Switch(Exception):
    def __init__(self,device,arguments):
        self.device=device;self.arguments=arguments


def home(device):
    if device=='default':return state.BASE_HOME
    if not isinstance(device,str) or not re.fullmatch('[0-9a-f]{32}',device):raise ValueError('Некорректный профиль колонки')
    path=state.BASE_HOME/'devices'/device
    from .app_install import linked
    if linked(path.parent) or linked(path):raise RuntimeError('Каталог профиля колонки не должен быть ссылкой')
    return path


def valid_name(name):
    return isinstance(name,str) and 1<=len(name)<=80 and name==name.strip() and not re.search(r'[\x00-\x1f\x7f]',name)


def legacy_name():
    path=state.BASE_HOME/'native/config.json'
    if not path.exists():return 'Основная колонка'
    config=json.loads(path.read_text(encoding='utf-8'))
    model=MODEL_NAMES.get(config.get('model'),'Колонка')
    name=model+(' · '+config['host'] if config.get('host') else '')
    return name if valid_name(name) else 'Основная колонка'


def catalog():
    path=state.BASE_HOME/'native-devices.json'
    if not path.exists():
        return {'version':1,'selected':'default','devices':[{'id':'default','name':legacy_name()}]}
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or value.get('version')!=1 or not isinstance(value.get('devices'),list):
        raise ValueError('Некорректный список колонок')
    ids=[]
    for item in value['devices']:
        if not isinstance(item,dict) or not valid_name(item.get('name')):raise ValueError('Некорректное имя колонки')
        home(item.get('id'));ids.append(item['id'])
    if ids.count('default')!=1 or len(ids)!=len(set(ids)) or value.get('selected') not in ids:
        raise ValueError('Некорректный список колонок')
    return value


def current_name():
    item=next((item for item in catalog()['devices'] if item['id']==state.DEVICE),None)
    if item is None:raise ValueError('Выбранная колонка отсутствует в списке профилей')
    return item['name']


def enter(mode,arguments):
    # Pin a running process; another CLI window may change the remembered choice.
    device='default' if mode=='bridge' else os.environ.get('ALISA_NATIVE_DEVICE') or catalog()['selected']
    home(device)
    if device!=state.DEVICE:raise Switch(device,arguments)
    if mode=='bridge':os.environ.pop('ALISA_NATIVE_DEVICE',None)
    elif (state.BASE_HOME/'native-devices.json').exists():os.environ.setdefault('ALISA_NATIVE_DEVICE',device)


def restart(request):
    environment=os.environ.copy()
    if request.arguments and request.arguments[0]=='bridge':environment.pop('ALISA_NATIVE_DEVICE',None)
    else:environment['ALISA_NATIVE_DEVICE']=request.device
    # All modules capture HOME at import. Re-enter only after the dashboard exits.
    argv=[sys.executable,'-B','-X','utf8',str(state.ROOT/'alisa.py'),*request.arguments]
    os.execve(sys.executable,argv,environment)


@contextlib.contextmanager
def edit_catalog():
    state.BASE_HOME.mkdir(parents=True,exist_ok=True,mode=0o700)
    from .app_install import exclusive_lock
    with exclusive_lock(state.BASE_HOME/'native-devices.lock','Список колонок меняется в другом окне. Повторите действие.'):
        value=catalog();yield value
        state.write_private(state.BASE_HOME/'native-devices.json',value)


def choose():
    value=catalog()
    items=[(('✓ ' if item['id']==state.DEVICE else '')+item['name'],item['id']) for item in value['devices']]
    choice=ui.select('Колонка для управления',items+[
        ('Добавить колонку…','add'),('Переименовать выбранную…','rename'),('Назад','back')],default=state.DEVICE)
    if choice=='back':return
    if choice in ('add','rename'):
        name=ui.text('Название колонки',current_name() if choice=='rename' else '',
                     validate=lambda text:True if valid_name(text) else 'Введите название от 1 до 80 символов без управляющих знаков')
        if not valid_name(name):raise ValueError('Некорректное имя колонки')
    if choice==state.DEVICE:return
    with edit_catalog() as value:
        if choice in ('add','rename'):
            if any(item['name'].casefold()==name.casefold() and (choice=='add' or item['id']!=state.DEVICE) for item in value['devices']):
                raise ValueError('Название колонки уже используется')
            if choice=='rename':
                next(item for item in value['devices'] if item['id']==state.DEVICE)['name']=name
            else:
                choice=uuid.uuid4().hex;value['devices'].append({'id':choice,'name':name})
        if choice!='rename':
            if choice not in [item['id'] for item in value['devices']]:raise ValueError('Неизвестная колонка')
            value['selected']=choice
    if choice!='rename':raise Switch(choice,['native'])


def bundle():
    paths=['assets/native/manifest.json','engines/native/tools/deploy.py',
           'station/vpn.sh','station/boot.sh','station/native-call.sh']
    digest=hashlib.sha256()
    for name in paths:digest.update((state.ROOT/name).read_bytes())
    return digest.hexdigest()


def needs_update(config):
    # Old single-device installs have no marker and keep their existing behavior.
    return bool(config.get('installed') and config.get('native_bundle') and config['native_bundle']!=bundle())


def component_note(config):
    if not config.get('installed'):return ''
    if not config.get('native_bundle'):return 'Версия компонентов не проверена'
    return 'Нужно обновить компоненты' if needs_update(config) else ''


def require_current(config,action):
    if config['mode']=='native' and needs_update(config) and action not in (
            'install','devices','status','doctor','storage','network','stop','help','uninstall','update'):
        raise RuntimeError('Компоненты выбранной колонки отличаются от программы. Выберите «Установка и обслуживание → Обновить компоненты колонки». Другие колонки не изменятся.')
