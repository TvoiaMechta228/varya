# Сторонние компоненты

- .NET, Windows App SDK / WinUI — Microsoft. https://github.com/microsoft/WindowsAppSDK
- Python и зависимости исполнителей — версии закреплены в requirements*.txt.
- faster-whisper — MIT. https://github.com/SYSTRAN/faster-whisper
- Whisper Small / конверсия Systran. https://huggingface.co/Systran/faster-whisper-small
- Piper — GPL-3.0. https://github.com/OHF-Voice/piper1-gpl
- Голос irina-medium — карточка включена в models/VOICE_MODEL_CARD. В карточке исходного датасета указана лицензия Unknown. Бесплатность инструментов не определяет условия распространения конкретного голоса.
- Qwen2.5-1.5B-Instruct-GGUF — Apache-2.0. https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF
- llama.cpp b12651 — MIT. https://github.com/ggml-org/llama.cpp
- Chromium / Playwright — браузер и драйвер со своими лицензиями в каталоге tools/browser и пакете playwright. https://playwright.dev/python/
- Vosk и малая русская модель — распознавание фразы остановки. https://alphacephei.com/vosk/models
- python-docx, openpyxl, python-pptx и pypdf — обработка документов. Лицензии пакетов включаются PyInstaller.
- WiX 3.14.1 — открытые инструменты сборки MSI. https://github.com/wixtoolset/wix3/releases/tag/wix3141rtm
- WebRTC VAD, CTranslate2, ONNX Runtime, PyAudio, PyInstaller и остальные зависимости сохраняют лицензии своих авторов.

model-manifest.json закрепляет файлы моделей и SHA-256. Локальная сборка предназначена для работы без платных API. При распространении учитывайте лицензии компонентов и голосов.
