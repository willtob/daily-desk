#pragma once

#ifdef __cplusplus
extern "C" {
#endif

void audio_beep_init(void);
void audio_beep_play(void);

/* ES8311 DAC volume, register 0x32. 0xFF = 0 dB (maximum — what init leaves it
 * at); each step down is -0.5 dB, so 0xE0 is about -15.5 dB and 0xD0 about
 * -23.5 dB. This is the real analog gain control: attenuating samples in
 * software still drives the amplifier at full gain, which distorts. */
void audio_set_dac_volume(unsigned char vol);

#ifdef __cplusplus
}
#endif
