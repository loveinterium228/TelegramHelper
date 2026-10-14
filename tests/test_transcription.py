"""Tests for TranscriptionService on genuine WAV audio files without downloading models.

Verifies:
- Generation and processing of valid 16kHz 16-bit PCM WAV audio files.
- Local faster-whisper decoding (mocked to prevent 500MB HuggingFace download).
- Database transcription caching in SQLite.
- OpenAI Whisper API mode.
- Hybrid fallback mode (local failure -> API fallback).
- Error handling and input validation.
"""
from dataclasses import dataclass
from pathlib import Path
import struct
from unittest.mock import AsyncMock, MagicMock, patch
import wave

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.transcription import TranscriptionService
from src.db.session import get_session, init_db


def create_real_wav_file(path: Path, duration_seconds: float = 0.2, sample_rate: int = 16000) -> Path:
    """Generate a genuine RIFF/WAV PCM audio file on disk."""
    n_samples = int(duration_seconds * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)  # Mono
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        # Synthetic audio samples
        data = struct.pack(f"<{n_samples}h", *([0] * n_samples))
        wf.writeframes(data)
    return path


@dataclass
class MockSegment:
    text: str


@dataclass
class MockAudioInfo:
    language: str = "ru"
    language_probability: float = 0.99


@pytest.fixture
async def memory_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    await init_db(custom_engine=engine)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    with patch("src.core.transcription.get_session", lambda: get_session(custom_sessionmaker=sm)):
        yield sm
    await engine.dispose()


@pytest.mark.asyncio
async def test_transcribe_local_on_real_wav(tmp_path: Path, memory_db):
    """Local faster-whisper transcription on a genuine WAV file without downloading models."""
    wav_path = create_real_wav_file(tmp_path / "sample.wav")
    assert wav_path.exists()

    # Verify wav file integrity
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000

    service = TranscriptionService(model_size="tiny")

    # Mock WhisperModel to simulate local faster-whisper without network download
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (
        [MockSegment("Привет,"), MockSegment("это"), MockSegment("тестовая"), MockSegment("запись.")],
        MockAudioInfo(),
    )
    service._model = mock_model

    result = await service.transcribe(wav_path, mode="local", language="ru")
    assert result == "Привет, это тестовая запись."
    mock_model.transcribe.assert_called_once_with(str(wav_path), language="ru")


@pytest.mark.asyncio
async def test_transcription_caching(tmp_path: Path, memory_db):
    """Verify that cached transcription returns immediately without invoking the model."""
    wav_path = create_real_wav_file(tmp_path / "voice.wav")
    file_id = "telegram_media_file_unique_123"

    service = TranscriptionService(model_size="tiny")
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (
        [MockSegment("Распознанный"), MockSegment("голос")],
        MockAudioInfo(),
    )
    service._model = mock_model

    # 1. First run: transcribes and writes to cache
    text1 = await service.transcribe(wav_path, file_id=file_id, mode="local")
    assert text1 == "Распознанный голос"
    assert mock_model.transcribe.call_count == 1

    # 2. Make the model raise if called again
    mock_model.transcribe.side_effect = RuntimeError("Model should NOT be called on cache hit!")

    # 3. Second run: must hit the SQLite cache
    text2 = await service.transcribe(wav_path, file_id=file_id, mode="local")
    assert text2 == "Распознанный голос"
    # Ensure transcribe was not called again
    assert mock_model.transcribe.call_count == 1


@pytest.mark.asyncio
async def test_transcribe_api_mode(tmp_path: Path, memory_db):
    """Verify OpenAI Whisper API mode with mocked AsyncOpenAI."""
    wav_path = create_real_wav_file(tmp_path / "api_test.wav")
    service = TranscriptionService()

    mock_client = MagicMock()
    mock_resp = MagicMock(text="API transcribed voice message")
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_resp)

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        result = await service.transcribe(wav_path, mode="api", openai_key="sk-test-fake-key", language="en")
        assert result == "API transcribed voice message"
        mock_client.audio.transcriptions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_transcribe_api_requires_key(tmp_path: Path):
    """API mode without openai_key must raise ValueError."""
    wav_path = create_real_wav_file(tmp_path / "err.wav")
    service = TranscriptionService()
    with pytest.raises(ValueError, match="OpenAI API key required"):
        await service.transcribe(wav_path, mode="api", openai_key=None)


@pytest.mark.asyncio
async def test_transcribe_hybrid_fallback_to_api(tmp_path: Path, memory_db):
    """Hybrid mode: if local decoding fails, fallback to API when key is available."""
    wav_path = create_real_wav_file(tmp_path / "hybrid.wav")
    service = TranscriptionService()

    # Make local transcription raise an error
    mock_model = MagicMock()
    mock_model.transcribe.side_effect = RuntimeError("CUDA out of memory")
    service._model = mock_model

    mock_client = MagicMock()
    mock_resp = MagicMock(text="Fallback API transcript")
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_resp)

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        result = await service.transcribe(
            wav_path, mode="hybrid", openai_key="sk-test-key", language="ru"
        )
        assert result == "Fallback API transcript"


@pytest.mark.asyncio
async def test_transcribe_hybrid_fails_without_api_key(tmp_path: Path, memory_db):
    """Hybrid mode: if local decoding fails and no API key is present, raise original error."""
    wav_path = create_real_wav_file(tmp_path / "fail.wav")
    service = TranscriptionService()

    mock_model = MagicMock()
    mock_model.transcribe.side_effect = RuntimeError("Local decoder crash")
    service._model = mock_model

    with pytest.raises(RuntimeError, match="Local decoder crash"):
        await service.transcribe(wav_path, mode="hybrid", openai_key=None)
