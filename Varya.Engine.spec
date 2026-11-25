from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules
from comtypes.client import GetModule
uia = GetModule("UIAutomationCore.dll")
datas = [("templates/*.html", "templates")]
binaries = []
hidden = collect_submodules("pyttsx3.drivers") + collect_submodules("winrt")
hidden += [uia.__name__, uia.CUIAutomation.__module__, "comtypes.stream", "PIL.ImageGrab"]
for package in ("piper", "faster_whisper", "ctranslate2", "playwright", "vosk"):
    data, binary, imports = collect_all(package)
    datas += data
    binaries += binary
    hidden += imports
a = Analysis(["engine_host.py"], pathex=[], binaries=binaries, datas=datas,
             hiddenimports=hidden, excludes=["pytest", "tkinter", "gui", "cockpit", "legacy", "win32ui", "neural_voice", "edge_tts", "pygame", "piper.train", "ctranslate2.converters"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Varya.Engine",
          console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Varya.Engine", upx=False)
