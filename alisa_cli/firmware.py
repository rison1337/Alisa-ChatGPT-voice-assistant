"""Known builds are evidence, not an installation allowlist.

Unknown builds on a supported model are installable with a warning. Missing
firmware files and unsupported models remain errors in every entry point.
"""
import re

MAIND='7d431541e2737a94f8499953204dbb1306877e0cf7d36170c4b1a9e19974ba14'
VQE='fa7ad1544fe9ddfccedcf15acc031768345b0b8c7333bc1c0b47bc476dd4152f'
BUILDS={
    'lite2':(('station-lite2','yandex,station-lite2','plum','yandex,prune'),[(MAIND,VQE)]),
    'lite1':(('yandex,station-micro',),[('2dad0ffc61ba73d15ea813b754d7a56866d0477c321dcad9138b8d06e14ddbf1',
                                       'df67c7ad9fb9f2f9c473ba2aaed7663a0e898a2c6622993e65d3b8834a0b3646')]),
    # Yandex Station 2 (yandexmidi): the pinned pair is the firmware the model
    # was validated on (live voice dialog, one station). Any other build stays
    # 'unverified' and installs with the usual warning.
    'yandexmidi':(('yandex,station-midi','yandexmidi','station-midi'),
                  [('9ad1476ee37df3e401a8426df7710f8f3d2d0695e01d0b949d99a5a8b562a4d2',
                    'e3f80b79cd5b7f9422b790b3fe81cc41883e1c07914e6b474f4fd9313e3b84af')]),
}
ARCHITECTURES=('aarch64','armv7l','armv8l','arm')
MODEL_NAMES={'lite1':'Станция Лайт 1','lite2':'Станция Лайт 2','yandexmidi':'Станция 2'}
LIGHTING_UNAVAILABLE='Подсветка GPT недоступна для этой модели.'
WARNING=('Эта сборка прошивки ещё не проверена. Установка разрешена, '
         'но работа микрофона, звука и голосовых команд не гарантируется.')


def supports_lighting(model):
    return model is None or model in BUILDS


def apply_model(config,model):
    if model not in BUILDS:return
    config['model']=model


def classify(device_tree,pair):
    tokens=set(re.split(r'[\x00\s]+',device_tree.lower()))
    model=next((name for name,(markers,_) in BUILDS.items() if tokens.intersection(markers)),None)
    present=len(pair)==2 and all(isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) for value in pair)
    known=bool(model and present and tuple(pair) in BUILDS[model][1])
    status='unsupported-model' if not model else 'verified' if known else 'unverified' if present else 'unavailable'
    return {'model':model,'firmware_abi_supported':known,'firmware_files_present':present,
            'firmware_status':status,'warnings':[WARNING] if status=='unverified' else []}
