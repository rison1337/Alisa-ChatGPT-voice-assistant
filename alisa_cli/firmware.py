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
}
ARCHITECTURES=('aarch64','armv7l','armv8l','arm')
WARNING=('Эта сборка прошивки ещё не проверена. Установка разрешена, '
         'но работа микрофона, звука и голосовых команд не гарантируется.')


def classify(device_tree,pair):
    tokens=set(re.split(r'[\x00\s]+',device_tree.lower()))
    model=next((name for name,(markers,_) in BUILDS.items() if tokens.intersection(markers)),None)
    present=len(pair)==2 and all(isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) for value in pair)
    known=bool(model and present and tuple(pair) in BUILDS[model][1])
    status='unsupported-model' if not model else 'verified' if known else 'unverified' if present else 'unavailable'
    return {'model':model,'firmware_abi_supported':known,'firmware_files_present':present,
            'firmware_status':status,'warnings':[WARNING] if status=='unverified' else []}
