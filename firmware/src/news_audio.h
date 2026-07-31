/*
 * news_audio.h — reads an article aloud through the ES8311 codec.
 *
 * The backend synthesises speech and serves it as raw PCM (24 kHz, 16-bit
 * signed LE, mono), so there is no decoder here: bytes off the socket go
 * straight to i2s_write(). Codec/I2S bring-up is reused verbatim from
 * audio_beep.cpp — same signal path as the timer beeps.
 */
#pragma once
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Must match the backend's PCM format (esp_news/tts.py), which resamples to
 * this rate precisely because audio_beep_init() programs the ES8311's clock
 * dividers for 16 kHz. Changing it here alone re-clocks only the ESP32 side
 * and the codec keeps decoding at 16 kHz — garbled speech at any volume. */
#define NEWS_AUDIO_SAMPLE_RATE  16000

/* Read by the UI to label the play/stop button. Written only by audio_task. */
extern volatile bool news_audio_playing;
extern volatile int  news_audio_index;    /* article being read, -1 when idle */
extern volatile bool news_audio_failed;   /* last playback attempt failed */

void news_audio_init(void);               /* after i2c_master_Init() */
void news_audio_play(int article_index);  /* request playback; returns at once */
void news_audio_stop(void);               /* request stop; returns at once */

#ifdef __cplusplus
}
#endif
