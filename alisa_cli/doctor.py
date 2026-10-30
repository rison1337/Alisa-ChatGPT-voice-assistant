import json,shutil,subprocess,sys
from .ssh import Station
from .firmware import MAIND,VQE,BUILDS,ARCHITECTURES,WARNING,classify

def windows_chromium():
    if sys.platform!='win32':return None
    from .windows import browser
    return browser()

def inspect(config):
    s=Station(config)
    script='id -u; uname -m; cat /proc/device-tree/compatible; printf "\\n"; sha256sum /system/vendor/quasar/maind /system/vendor/quasar/libs/libyandex_evil.so; df -Pk /data; for x in aplay socat base64 sha256sum ip; do command -v "$x" || true; done; printf "ALISA_FIRMWARE_VERSION="; head -c 256 /system/vendor/quasar/version_full 2>/dev/null || true'
    lines=s.text(script).replace('\x00',' ').splitlines()
    if len(lines)<3:raise RuntimeError('Колонка вернула неполный ответ SSH. Повторите проверку подключения.')
    root=lines[0].strip()=='0'
    device_tree=lines[2]
    hashes={line.split()[-1]:line.split()[0] for line in lines if len(line.split())==2 and len(line.split()[0])==64}
    pair=(hashes.get('/system/vendor/quasar/maind'),hashes.get('/system/vendor/quasar/libs/libyandex_evil.so'))
    firmware=classify(device_tree,pair)
    model=firmware['model'];compatible=model is not None
    mode_supported=compatible and not (model=='lite1' and config['mode']=='bridge')
    missing=[tool for tool in ['ssh','chromium'] if not shutil.which(tool)]
    if sys.platform=='win32':
        from .windows import missing as windows_missing
        missing=windows_missing()
    if config['mode']=='bridge':
        for tool in ['pactl','pacat','paplay','Xvfb','xauth']:
            if not shutil.which(tool) and not (tool=='Xvfb' and (__import__('pathlib').Path(__file__).resolve().parents[1]/'engines/bridge/display-tools/usr/bin/Xvfb').exists()):missing.append(tool)
        if sys.platform!='linux':missing.append('Windows audio backend (следующий этап)')
        if not missing:
            if subprocess.run(['pactl','info'],capture_output=True).returncode:missing.append('работающий PipeWire/PulseAudio')
    available=None
    for line in lines:
        fields=line.split()
        if len(fields)>=6 and fields[1].isdigit() and fields[3].isdigit():
            try:available=int(fields[3])
            except ValueError:pass
    needed=['aplay','base64','sha256sum']+(['socat'] if config['mode']=='bridge' else [])
    missing_remote=[name for name in needed if not any(x.endswith('/'+name) for x in lines)]
    version=next((line.partition('=')[2].strip() for line in lines if line.startswith('ALISA_FIRMWARE_VERSION=')),None)
    return {'ssh':True,'root':root,'plum':compatible,'mode_supported':mode_supported,**firmware,'architecture':lines[1].strip(),
        'firmware_version':version or None,'firmware_sha256':{'maind':pair[0],'vqe':pair[1]},
        'free_data_kib':available,'host_missing':missing,'station_missing':missing_remote,
        'ok':root and compatible and mode_supported and firmware['firmware_files_present'] and lines[1].strip() in ARCHITECTURES and available is not None and available>12000 and not missing and not missing_remote}

def require(config,report=None,warn=True):
    report=report if report is not None else inspect(config)
    reasons=[]
    if not report['root']:reasons.append('нужны права root по SSH')
    if not report['plum']:reasons.append('устройство не распознано как Станция Лайт 1 или Лайт 2')
    if report.get('mode_supported') is False:reasons.append('Лайт 1 пока поддерживается только в нативном режиме' if report.get('model')=='lite1' else 'режим не поддерживается этой моделью')
    if not report.get('firmware_files_present',report['firmware_abi_supported']):reasons.append('не удалось прочитать maind и библиотеку VQE')
    if report.get('architecture', 'aarch64') not in ARCHITECTURES:reasons.append('архитектура колонки не поддерживается')
    if report['free_data_kib'] is None or report['free_data_kib']<=12000:reasons.append('на колонке нужно больше 12 МиБ свободного места')
    if report['host_missing']:reasons.append('на компьютере не хватает: '+', '.join(report['host_missing']))
    if report['station_missing']:reasons.append('на колонке не хватает: '+', '.join(report['station_missing']))
    if not report['ok'] and not reasons:reasons.append('не пройдена обязательная проверка компонентов')
    if reasons:raise RuntimeError('Установка остановлена: '+('; '.join(reasons))+'.')
    if warn and not report['firmware_abi_supported']:
        from . import ui
        ui.note(WARNING,'yellow')
    return report
