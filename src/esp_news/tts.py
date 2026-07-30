"""Phase 8 — text-to-speech for the digest.

Synthesis happens here rather than on the ESP32 for three reasons: no API key
on the device, no audio decoder in firmware, and the audio can be cached so a
replay costs nothing.

Output is **raw PCM**, not MP3. OpenAI's speech endpoint can return
``response_format="pcm"`` — 24 kHz, 16-bit signed little-endian, mono — which
the firmware can push straight into ``i2s_write()``. That single choice is what
keeps the device side simple: fitting an MP3 decoder into RAM and keeping it
fed is where most ESP32 speech projects fall over.
"""

from __future__ import annotations

import hashlib
import logging
import os

import numpy as np
from pathlib import Path

from openai import OpenAI

from esp_news.embeddings import MissingAPIKeyError

logger = logging.getLogger(__name__)

# digests/audio/ — src/esp_news/tts.py -> parents[2].
DEFAULT_AUDIO_DIR = Path(__file__).resolve().parents[2] / "digests" / "audio"

DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "alloy"

# OpenAI's "pcm" response format is fixed at 24 kHz.
OPENAI_PCM_RATE = 24000

# ...but the device runs at 16 kHz, and that is not negotiable from this side:
# audio_beep_init() programs the ES8311's internal clock dividers for 16 kHz,
# and calling i2s_set_clk(24000) on the ESP32 only re-clocks the MCU half. The
# codec keeps decoding at 16 kHz, and speech comes out garbled — audible as
# "loud and unintelligible", and completely unaffected by volume.
#
# Reprogramming the codec's clock coefficients for 24 kHz is the alternative,
# but that audio path was hard-won (see the project's CLAUDE.md) and is not
# worth destabilising to save a resample here.
PCM_SAMPLE_RATE = 16000
PCM_BITS = 16
PCM_CHANNELS = 1

# Long summaries make for long waits and long API calls; the device only ever
# shows a trimmed summary anyway.
MAX_SPEECH_CHARS = 800


def resample_pcm(raw: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample 16-bit mono PCM by linear interpolation.

    Speech at these rates doesn't warrant a windowed-sinc filter; interpolation
    is smooth enough that the aliasing it lets through sits well above where
    intelligibility lives.
    """
    if src_rate == dst_rate or not raw:
        return raw

    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    out_len = int(len(samples) * dst_rate / src_rate)
    if out_len < 1:
        return b""

    resampled = np.interp(
        np.linspace(0, len(samples) - 1, out_len),
        np.arange(len(samples)),
        samples,
    )
    return np.clip(np.round(resampled), -32768, 32767).astype("<i2").tobytes()


class TTSClient:
    """Synthesises speech to raw PCM, cached on disk by content hash."""

    def __init__(
        self,
        model: str = DEFAULT_TTS_MODEL,
        voice: str = DEFAULT_VOICE,
        audio_dir: str | Path | None = DEFAULT_AUDIO_DIR,
    ) -> None:
        self.model = model
        self.voice = voice
        self.audio_dir = Path(audio_dir) if audio_dir else None
        self._client: OpenAI | None = None
        self.api_calls = 0
        self.cache_hits = 0

    def _openai(self) -> OpenAI:
        if self._client is None:
            if not os.getenv("OPENAI_API_KEY"):
                raise MissingAPIKeyError(
                    "OPENAI_API_KEY is not set — add it to .env (see .env.example)."
                )
            self._client = OpenAI()
        return self._client

    def cache_path(self, text: str) -> Path | None:
        """Where a given utterance is cached. Keyed by model+voice+text, so
        changing the voice doesn't serve back the old one."""
        if not self.audio_dir:
            return None
        key = hashlib.sha256(
            f"{self.model}\x00{self.voice}\x00{PCM_SAMPLE_RATE}\x00{text}".encode()
        ).hexdigest()[:24]
        return self.audio_dir / f"{key}.pcm"

    def synthesize(self, text: str) -> bytes:
        """Return raw PCM for ``text``, from cache when possible.

        Raises ValueError on empty input rather than billing an API call to
        render silence.
        """
        text = (text or "").strip()[:MAX_SPEECH_CHARS]
        if not text:
            raise ValueError("nothing to speak")

        path = self.cache_path(text)
        if path and path.exists():
            self.cache_hits += 1
            logger.info("TTS cache hit (%s, %d bytes)", path.name, path.stat().st_size)
            return path.read_bytes()

        logger.info("Synthesising %d chars with %s/%s", len(text), self.model, self.voice)
        response = self._openai().audio.speech.create(
            model=self.model,
            voice=self.voice,
            input=text,
            response_format="pcm",
        )
        audio = resample_pcm(response.read(), OPENAI_PCM_RATE, PCM_SAMPLE_RATE)
        self.api_calls += 1

        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".pcm.tmp")
            tmp.write_bytes(audio)
            tmp.replace(path)   # atomic, so a concurrent read can't see a partial file

        logger.info(
            "TTS produced %d bytes (~%.1fs of audio)",
            len(audio),
            duration_seconds(len(audio)),
        )
        return audio


def duration_seconds(byte_count: int) -> float:
    """Playback length of a raw PCM buffer, in seconds."""
    return byte_count / (PCM_SAMPLE_RATE * PCM_CHANNELS * (PCM_BITS // 8))


def speech_text(article: dict) -> str:
    """What to actually read aloud for an article.

    Title then summary, with a pause between: hearing the headline first is
    what makes it possible to decide whether to keep listening.
    """
    title = (article.get("title") or "").strip()
    summary = (article.get("summary") or "").strip()
    source = (article.get("source") or "").strip()

    parts = [title]
    if source:
        parts.append(f"From {source}.")
    if summary:
        parts.append(summary)
    return "\n\n".join(p for p in parts if p)
