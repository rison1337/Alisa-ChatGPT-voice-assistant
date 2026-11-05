"""Bergamot display selection and temporary preview with clock recovery."""
import base64,contextlib,subprocess,threading
from . import runtime,ui
from .state import ROOT
from .ssh import Station

MODES=(('Анимированные глаза','eyes'),('Штатные часы','clock'))


class Preview:
    def __init__(self,config):
        self.config=config;self.process=None;self.thread=None
        self.stop=threading.Event();self.lock=threading.Lock();self.error=''

    def _release(self):
        self.stop.set()
        if self.thread is not None:self.thread.join(timeout=1)
        if self.process is not None:
            with contextlib.suppress(OSError,ValueError):
                self.process.stdin.write(b'0\n');self.process.stdin.flush()
            with contextlib.suppress(OSError,ValueError):self.process.stdin.close()
            try:code=self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process=None;self.thread=None
                raise RuntimeError('Демо дисплея не завершило восстановление часов. Проверьте колонку.')
            self.process=None;self.thread=None
            if code:raise RuntimeError('Демо дисплея завершилось с ошибкой. Проверьте возврат часов.')

    def select(self,mode):
        if mode not in ('eyes','clock'):raise ValueError('Неизвестный режим дисплея')
        with self.lock:
            if mode=='clock':
                self._release()
                return 'Штатные часы. Дисплеем управляет прошивка.'
            if self.process is None and not self.error:
                try:
                    # Read actual runtime even if local installed state is stale.
                    status=runtime.status(self.config)
                    if status.get('phase') in ('active','opening','connecting') or status.get('voice_active'):
                        self.error='Сейчас идёт разговор GPT: предпросмотр недоступен. Выбор можно сохранить.'
                    else:
                        bootstrap='''set -eu
umask 077
demo=$(mktemp -d /tmp/alisa-display-demo.XXXXXX)
cleanup() {
    rm -f "$demo/clock-eyes.sh" "$demo/clock-display.sh" "$demo/clock-isolation.sh"
    rmdir "$demo"
}
trap cleanup EXIT
trap 'exit 0' TERM INT HUP
for name in clock-eyes.sh clock-display.sh clock-isolation.sh; do
    IFS= read -r source
    printf '%s' "$source" | base64 -d > "$demo/$name"
done
ALISA_CLOCK_PREVIEW=1 ALISA_CLOCK_STATUS_BASE=/data/opt/alisa-standalone sh "$demo/clock-eyes.sh"
'''
                        self.process=Station(self.config).open_input(bootstrap)
                        for name in ('clock-eyes.sh','clock-display.sh','clock-isolation.sh'):
                            self.process.stdin.write(base64.b64encode((ROOT/'assets/native'/name).read_bytes())+b'\n')
                        self.process.stdin.flush();self.stop.clear()
                        self.thread=threading.Thread(target=self.animate,daemon=True);self.thread.start()
                except (RuntimeError,OSError,subprocess.SubprocessError):
                    self._release()
                    self.error='Предпросмотр недоступен: проверьте SSH и дисплей. Выбор можно сохранить.'
            if self.process is not None and self.process.poll() is not None:
                self._release()
                self.error='Предпросмотр завершился. Выбор можно сохранить.'
            return 'Анимированные глаза.\n\n'+(self.error or 'Предпросмотр на колонке. После выхода вернутся часы.')

    def animate(self):
        try:
            while not self.stop.is_set():
                self.process.stdin.write(b'F 0 0 0 0 0 0 0 0 0\n');self.process.stdin.flush()
                self.stop.wait(.125)
        except (OSError,ValueError):pass

    def close(self):
        with self.lock:self._release()


def choose(config):
    if config['mode']!='native' or config.get('model')!='bergamot':
        raise ValueError('Выбор дисплея доступен только для Мини 3 в нативном режиме')
    current=config.get('display','eyes')
    if not ui.interactive():return ui.select('Дисплей во время GPT',MODES,default=current)
    demo=Preview(config);chosen=[]
    def accept(value):
        if value!='back':chosen.append(value)
        return 0
    try:
        ui.dashboard('Дисплей во время GPT',list(MODES)+[('Назад без изменений','back')],default=current,
            help_text='Стрелки — предпросмотр. Enter — сохранить. Esc — отменить.',on_select=accept,
            on_focus=lambda context,value:demo.select(value) if value!='back' else 'Вернуться без сохранения.',focus_delay=.15)
    finally:demo.close()
    if not chosen:raise ui.Back
    return chosen[0]
