import json,shutil,subprocess,sys
from .ssh import Station
MAIND='7d431541e2737a94f8499953204dbb1306877e0cf7d36170c4b1a9e19974ba14'
VQE='fa7ad1544fe9ddfccedcf15acc031768345b0b8c7333bc1c0b47bc476dd4152f'

def inspect(config):
    s=Station(config)
    script='id -u; uname -m; cat /proc/device-tree/compatible; printf "\\n"; sha256sum /system/vendor/quasar/maind /system/vendor/quasar/libs/libyandex_evil.so; df -Pk /data; for x in aplay socat base64 sha256sum ip; do command -v "$x" || true; done'
    lines=s.text(script).replace('\x00',' ').splitlines()
    if len(lines)<3:raise RuntimeError('Колонка вернула неполный ответ SSH. Повторите проверку подключения.')
    root=lines[0].strip()=='0';compatible=any('station-lite2' in line or 'plum' in line or 'yandex,prune' in line for line in lines)
    abi=any(MAIND in line for line in lines) and any(VQE in line for line in lines)
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
    needed=['aplay','socat','base64','sha256sum']
    missing_remote=[name for name in needed if not any(x.endswith('/'+name) for x in lines)]
    return {'ssh':True,'root':root,'plum':compatible,'firmware_abi_supported':abi,'architecture':lines[1],
        'free_data_kib':available,'host_missing':missing,'station_missing':missing_remote,
        'ok':root and compatible and abi and available is not None and available>12000 and not missing and not missing_remote}

def require(config,report=None):
    report=report if report is not None else inspect(config)
    if not report['ok']:
        reasons=[]
        if not report['root']:reasons.append('нужны права root по SSH')
        if not report['plum']:reasons.append('устройство не распознано как Станция Лайт 2')
        if not report['firmware_abi_supported']:reasons.append('эта сборка прошивки ещё не поддерживается аудиомодулями; файлы прошивки не изменены')
        if report['free_data_kib'] is None or report['free_data_kib']<=12000:reasons.append('на колонке нужно больше 12 МиБ свободного места')
        if report['host_missing']:reasons.append('на компьютере не хватает: '+', '.join(report['host_missing']))
        if report['station_missing']:reasons.append('на колонке не хватает: '+', '.join(report['station_missing']))
        raise RuntimeError('Установка остановлена: '+('; '.join(reasons) or 'не пройдена проверка компонентов')+'.')
    return report
