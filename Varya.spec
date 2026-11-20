# Сборка одного EXE без консоли; динамический драйвер SAPI5 включён явно.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=[],
    datas=collect_data_files('speech_recognition'),
    hiddenimports=(
        collect_submodules('pyttsx3.drivers')
        + ['comtypes.client', 'comtypes.stream', 'PIL.ImageGrab']
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='Varya',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    uac_admin=False,
)
