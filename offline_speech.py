"""Потоковая русская речь Vosk и локальный женский голос Piper."""

import json
import os
from pathlib import Path
import time
import threading
import logging

import speech_recognition as sr

import settings
import speech

_model = None
_voice = None
_whisper = None
_interrupt_model = None


def listen_for_interrupt(done: threading.Event) -> None:
    """Во время ответа принимать только обращение по имени и команду остановки."""
    global _interrupt_model
    import pyaudio
    from vosk import Model, KaldiRecognizer, SetLogLevel

    audio = pyaudio.PyAudio()
    stream = None
    try:
        if _interrupt_model is None:
            SetLogLevel(-1)
            _interrupt_model = Model(str(assets() / "vosk-model-small-ru-0.22"))
        grammar = [
            "варя стоп",
            "джарвис стоп",
            "варя остановись",
            "джарвис остановись",
            "[unk]",
        ]
        recognizer = KaldiRecognizer(
            _interrupt_model, 16000, json.dumps(grammar, ensure_ascii=False)
        )
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=1600,
            input_device_index=settings.snapshot().get("microphone"),
        )
        while not done.is_set() and not speech.is_cancelled():
            data = stream.read(1600, exception_on_overflow=False)
            final = recognizer.AcceptWaveform(data)
            result = json.loads(
                recognizer.Result() if final else recognizer.PartialResult()
            )
            text = result.get("text", result.get("partial", ""))
            if (
                any(phrase in text for phrase in grammar[:-1])
                and speech.barge_in_callback
            ):
                speech.barge_in_callback()
                return
    except Exception:
        logging.getLogger(__name__).warning(
            "Голосовое перебивание недоступно на текущем устройстве", exc_info=True
        )
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        audio.terminate()


def assets() -> Path:
    """Найти модели рядом с установленным приложением."""
    return Path(os.environ.get("VARYA_HOME", Path(__file__).parent)) / "models"


def release_microphone() -> None:
    """Освободить модель распознавания после выключения микрофона."""
    global _model, _whisper, _interrupt_model
    _model = None
    _whisper = None
    _interrupt_model = None


def listen_vosk(timeout: float, phrase_time_limit: float) -> str | None:
    """Слушать с локальным определением конца фразы и быстрой отменой."""
    global _model
    from vosk import KaldiRecognizer, Model, SetLogLevel
    import pyaudio

    if speech.is_cancelled():
        return None
    if _model is None:
        speech.publish("state", "Подготавливаю локальное распознавание…")
        SetLogLevel(-1)
        _model = Model(str(assets() / "vosk-model-small-ru-0.22"))
    recognizer = KaldiRecognizer(_model, 16000)
    audio = pyaudio.PyAudio()
    stream = None
    try:
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=1600,
            input_device_index=settings.snapshot().get("microphone"),
        )
        speech.publish("state", "Слушаю…")
        started = time.monotonic()
        speaking_at = None
        partial = ""
        while not speech.is_cancelled():
            data = stream.read(1600, exception_on_overflow=False)
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result()).get("text", "").strip()
                if result:
                    return result
            text = json.loads(recognizer.PartialResult()).get("partial", "")
            now = time.monotonic()
            if text and speaking_at is None:
                speaking_at = now
            if text != partial:
                partial = text
                speech.publish("partial", text)
            if speaking_at is None and now - started >= timeout:
                raise sr.WaitTimeoutError()
            if speaking_at is not None and now - speaking_at >= phrase_time_limit:
                return (
                    json.loads(recognizer.FinalResult()).get("text", "").strip() or None
                )
        return None
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        audio.terminate()
        speech.publish("partial", "")


def listen(timeout: float, phrase_time_limit: float) -> str | None:
    """Определить границы речи локальным VAD и распознать фразу Whisper INT8."""
    global _whisper
    import numpy as np
    import pyaudio
    import webrtcvad
    from collections import deque
    from faster_whisper import WhisperModel

    if _whisper is None:
        speech.publish("state", "Подготавливаю локальное распознавание…")
        _whisper = WhisperModel(
            str(assets() / "whisper-small"),
            device="cpu",
            compute_type="int8",
            cpu_threads=4,
            local_files_only=True,
        )
    audio = pyaudio.PyAudio()
    stream = None
    try:
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=480,
            input_device_index=settings.snapshot().get("microphone"),
        )
        vad = webrtcvad.Vad(2)
        lead = deque(maxlen=10)
        frames = []
        started = time.monotonic()
        speaking_at = None
        last_speech = started
        voiced = 0
        speech.publish("state", "Слушаю…")
        while not speech.is_cancelled():
            data = stream.read(480, exception_on_overflow=False)
            now = time.monotonic()
            detected = vad.is_speech(data, 16000)
            voiced = voiced + 1 if detected else 0
            if speaking_at is None:
                lead.append(data)
                if voiced >= 3:
                    speaking_at = now
                    frames.extend(lead)
                    speech.publish("state", "Слышу тебя…")
                elif now - started >= timeout:
                    raise sr.WaitTimeoutError()
            else:
                frames.append(data)
            if detected:
                last_speech = now
            if speaking_at is not None and (
                now - last_speech >= 0.9 or now - speaking_at >= phrase_time_limit
            ):
                break
        if speech.is_cancelled():
            return None
        stream.stop_stream()
        speech.publish("state", "Распознаю речь локально…")
        waveform = (
            np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768
        )
        segments, _ = _whisper.transcribe(
            waveform,
            language="ru",
            beam_size=3,
            condition_on_previous_text=False,
            initial_prompt="Варя. Джарвис. Браузер. Яндекс Музыка.",
        )
        text = " ".join(segment.text.strip() for segment in segments)
        return None if speech.is_cancelled() else text.strip() or None
    finally:
        if stream is not None:
            if stream.is_active():
                stream.stop_stream()
            stream.close()
        audio.terminate()


def speak(text: str, rate: int, volume: int) -> bool:
    """Синтезировать и воспроизвести русскую речь по частям без аудиофайлов."""
    global _voice
    from piper import PiperVoice, SynthesisConfig
    import pyaudio

    if _voice is None:
        speech.publish("state", "Подготавливаю локальный голос…")
        _voice = PiperVoice.load(assets() / "ru_RU-irina-medium.onnx")
    config = SynthesisConfig(length_scale=175 / rate, volume=volume / 100)
    audio = pyaudio.PyAudio()
    stream = None
    done = threading.Event()
    listener = None
    if speech.barge_in_enabled and speech.barge_in_callback:
        if not any(
            phrase in text.casefold()
            for phrase in (
                "варя стоп",
                "джарвис стоп",
                "варя остановись",
                "джарвис остановись",
            )
        ):
            listener = threading.Thread(
                target=listen_for_interrupt,
                args=(done,),
                daemon=True,
                name="VaryaVoiceInterrupt",
            )
            listener.start()
    try:
        for chunk in _voice.synthesize(text, config):
            if speech.is_cancelled():
                return False
            if stream is None:
                stream = audio.open(
                    format=pyaudio.paInt16,
                    channels=chunk.sample_channels,
                    rate=chunk.sample_rate,
                    output=True,
                )
            data = chunk.audio_int16_bytes
            for offset in range(0, len(data), 4096):
                if speech.is_cancelled():
                    return False
                stream.write(data[offset : offset + 4096])
        return True
    finally:
        done.set()
        if listener is not None:
            listener.join(timeout=3)
        if stream is not None:
            stream.stop_stream()
            stream.close()
        audio.terminate()
