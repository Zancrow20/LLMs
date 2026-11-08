"""MCP-сервер с ML-тулами задания 1. Работает на хосте (GPU Metal недоступен из VM).

Запуск из корня репо: uv run python task3-agent/mcp_ml/server.py
Агент в песочнице подключается к host.docker.internal:8765/mcp
"""
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "task1-ml-models"))
import audio_stt, image_classify, text_sentiment, video_detect  # noqa: E402

WORKSPACE = ROOT / "task3-agent" / "workspace"
# mcp 2.x: FastMCP переименован в MCPServer, host/port уходят в run()
mcp = MCPServer("ml-tools")

_video_cache: dict[tuple, dict] = {}


def safe(path: str) -> Path:
    """Пути только внутри workspace/ — иначе выход за песочницу через аргумент тула."""
    p = (WORKSPACE / path).resolve()
    if not p.is_relative_to(WORKSPACE):
        raise ValueError(f"путь вне workspace: {path}")
    return p


@mcp.tool()
def detect_video(path: str, conf: float = 0.4, track: bool = True, max_frames: int | None = None) -> dict:
    """Найти объекты на видео: классы, число уникальных объектов (track=True), FPS."""
    p = safe(path)
    key = (str(p), p.stat().st_mtime, conf, track, max_frames)
    if key not in _video_cache:  # длинное видео считается десятки секунд
        _video_cache[key] = video_detect.detect_video(p, conf=conf, track=track, max_frames=max_frames)
    return _video_cache[key]


@mcp.tool()
def transcribe(path: str, language: str = "russian") -> str:
    """Расшифровать аудиофайл (whisper-small). Язык: russian, english и т.п."""
    import soundfile as sf

    # transformers 5.x не принимает путь: ждёт ndarray c sampling_rate
    audio, sr = sf.read(safe(path))
    return audio_stt.transcribe({"raw": audio, "sampling_rate": sr}, language=language)


@mcp.tool()
def sentiment(text: str) -> dict:
    """Тональность английского текста (DistilBERT SST-2). Для русского использует LLM агент."""
    return text_sentiment.predict(text)


@mcp.tool()
def classify_image(path: str, k: int = 5) -> list:
    """Top-k классов ImageNet для картинки (ResNet50)."""
    return image_classify.predict(safe(path), k=k)


if __name__ == "__main__":
    WORKSPACE.mkdir(exist_ok=True)
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8765)
