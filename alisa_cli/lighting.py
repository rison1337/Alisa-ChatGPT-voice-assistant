"""Temporary LED demo over one SSH stream, with the firmware mute guard."""
import colorsys,contextlib,math,shlex,subprocess,threading,time
from .state import ROOT,STYLES,STYLE_NAMES
from .ssh import Station
from . import ui,runtime

def label(style):
    return 'Пульсация: '+STYLE_NAMES[style[6:]].lower() if style.startswith('pulse-') else STYLE_NAMES[style]

def frame(style,seconds):
    if style=='rainbow':
        peak=136+24*math.sin(math.pi*seconds);phase=(seconds%6)/6
        values=[int(component*peak+.5) for zone in range(3) for component in colorsys.hsv_to_rgb((phase+zone/3)%1,1,1)]
    else:
        name=style.removeprefix('pulse-')
        rgb={'red':(160,0,0),'green':(0,160,0),'blue':(0,0,160),'purple':(120,0,120),'white':(100,100,100),'off':(0,0,0)}[name]
        scale=.75+.25*math.sin(math.pi*seconds) if style.startswith('pulse-') else 1
        values=[int(value*scale+.5) for value in rgb*3]
    return ('F '+' '.join(map(str,values))+'\n').encode()

class Preview:
    def __init__(self,config):
        self.config=config;self.process=None;self.thread=None;self.style=config['led']
        self.lock=threading.Lock();self.stop=threading.Event();self.error='';self.checked=False
    def select(self,style):
        if style not in STYLES:raise ValueError('Неизвестная подсветка')
        with self.lock:
            if self.stop.is_set():return ''
            self.style=style
            if not self.checked:
                self.checked=True
                try:
                    if self.config.get('installed'):
                        status=runtime.status(self.config)
                        if status.get('phase') in ('active','opening') or status.get('voice_active'):
                            self.error='Сейчас идёт разговор GPT: демо не меняет его индикацию. Цвет можно выбрать и сохранить.'
                    if not self.error:
                        station=Station(self.config)
                        station.run('test -d /sys/class/leds/plum:red:ring1 || test -d /sys/class/leds/micro:red:ring1',timeout=12)
                        script=(ROOT/'assets/native/indicator.sh').read_text(encoding='utf-8')
                        # The demo restores the actual pre-demo frame, while the
                        # runtime normally restores idle darkness after a call.
                        script=script.replace("saved=' 0 0 0 0 0 0 0 0 0'",'saved="$task_led_current"')
                        self.process=station.open_input('ALISA_LISTEN_LED=0 ALISA_LISTEN_LED_FILE= sh -c '+shlex.quote(script))
                        self.thread=threading.Thread(target=self.animate,daemon=True);self.thread.start()
                except (RuntimeError,OSError,subprocess.SubprocessError):self.error='Демо недоступно: проверьте SSH. Выбор цвета доступен.'
            if self.process is not None and self.process.poll() is not None:self.error='Демо завершилось: проверьте SSH. Выбор цвета доступен.'
            return label(style)+'\n\n'+(self.error or 'Демо на колонке. Физическое отключение микрофона сохраняет приоритет индикации.')+'\n\nEnter — сохранить. Esc — вернуть прежнюю подсветку.'
    def animate(self):
        try:
            while not self.stop.is_set():
                self.process.stdin.write(frame(self.style,time.monotonic()));self.process.stdin.flush()
                self.stop.wait(.1)
        except (OSError,ValueError):pass
    def close(self):
        self.stop.set()
        with self.lock:
            if self.thread is not None:self.thread.join(timeout=1)
            if self.process is not None:
                with contextlib.suppress(OSError,ValueError):
                    self.process.stdin.write(b'0\n');self.process.stdin.flush();self.process.stdin.close()
                try:self.process.wait(timeout=4)
                except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=4)

def choose(config):
    from .firmware import supports_lighting,LIGHTING_UNAVAILABLE
    if not supports_lighting(config.get('model')):
        ui.note(LIGHTING_UNAVAILABLE)
        return 'off'
    if not ui.interactive():return ui.select('Подсветка',[(label(style),style) for style in STYLES],default=config['led'])
    demo=Preview(config);chosen=[]
    def accept(value):
        if value!='back':chosen.append(value)
        return 0
    try:
        ui.dashboard('Подсветка GPT',[(label(style),style) for style in STYLES]+[('Назад без изменений','back')],default=config['led'],
            help_text='Выделите цвет — он появится на колонке. Enter сохраняет, Esc отменяет.',on_select=accept,
            on_focus=lambda context,value:demo.select(value) if value!='back' else 'Esc или Enter — вернуться без сохранения.',focus_delay=.15)
    finally:demo.close()
    if not chosen:raise ui.Back
    return chosen[0]
