"""Локальное чтение видимого текста через Windows OCR. Снимок остаётся в памяти."""

import asyncio

from PIL import Image, ImageGrab
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

import desktop


async def recognize(image: Image.Image) -> str:
    """Распознать русский текст или использовать установленный язык пользователя."""
    engine = OcrEngine.try_create_from_language(Language("ru-RU"))
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise ValueError(
            "Добавь распознавание текста для русского языка в языковых параметрах Windows."
        )
    limit = OcrEngine.max_image_dimension
    if max(image.size) > limit:
        image.thumbnail((limit, limit))
    writer = DataWriter()
    try:
        writer.write_bytes(image.convert("RGBA").tobytes("raw", "BGRA"))
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, *image.size
        )
        try:
            result = await asyncio.wait_for(engine.recognize_async(bitmap), timeout=20)
            return "\n".join(line.text for line in result.lines).strip()[:10000]
        finally:
            bitmap.close()
    finally:
        writer.close()


def read(target: tuple[int, int, str] | None = None) -> str:
    if target is not None and target not in desktop.windows():
        raise ValueError("Выбранное окно изменилось. Выбери его ещё раз.")
    with ImageGrab.grab(window=target[0] if target else None) as image:
        return asyncio.run(recognize(image))
