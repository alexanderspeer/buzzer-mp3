/*
Plays the song extracted from:
David_Bowie_-_Space_Oddity.arduino.json
:contentReference[oaicite:0]{index=0}

Hardware:
- Passive buzzer on D9 (D9 -> 300Ω -> buzzer+ ; buzzer- -> GND)

Timing:
- MIDI ticks_per_beat = 384 (from JSON)
- Set BPM below to control overall speed
*/

#include <Arduino.h>
#include <avr/pgmspace.h>

const uint8_t BUZZ_PIN = 9;

// Change this to match the tempo you want
const uint16_t BPM = 120;

// From JSON
const uint16_t TICKS_PER_BEAT = 384;

// Optional global transpose (semitones). 0 = original.
const int8_t TRANSPOSE = 0;

// Notes are MIDI note numbers; -1 = rest
const int16_t SONG_NOTES[] PROGMEM = {
  -1, 76, 83, 67, 76, 72, 76, 83, 76, 72, 74, 76, 72, 83,
  76, 72, 74, 76, 88, 93, 86, 93, 88, 79, 80, 81, 80, 79,
  81, 80, 79, 81, 79, 80, 81, 80, 79, 81, 80, 79, 81, 76,
  77, 76, 79, 77, 53, 76, 64, 77, 60, 79, 76, 64, 77, 79,
  76, 93, 91, 93, 76, 91, 90, 92, 79, 80, 81, 80, 79, 81,
  80, 79, 81, 86, 96, 86, 96, 81, 96, 86, 81, 84, 83, 96,
  95, 81, 79, 77, 76, 77, 76, 79, 77, 53, 76, 64, 77, 60,
  79, 59, 76, 64, 77, 79, 59, 79, 76, 93, 91, 93, 76, 91,
  90, 92
};

const int16_t SONG_TICKS[] PROGMEM = {
  2688, 7488, 1344, 96, 3744, 96, 96, 1344, 1440, 96, 384, 4896, 96, 1344,
  1632, 96, 384, 2688, 672, 768, 864, 192, 384, 1536, 1536, 1536, 768, 768,
  1536, 768, 768, 1536, 1536, 1536, 1536, 768, 768, 1536, 768, 768, 1536, 6144,
  768, 768, 2112, 96, 96, 96, 96, 96, 96, 192, 672, 96, 192, 192,
  576, 1536, 1536, 1440, 96, 1536, 1536, 1536, 1536, 1536, 1536, 768, 768, 1536,
  768, 768, 1536, 1536, 96, 288, 1056, 96, 96, 864, 576, 1536, 1536, 96,
  1056, 384, 1536, 1536, 1536, 768, 768, 2112, 96, 96, 96, 96, 96, 96,
  96, 96, 576, 96, 96, 96, 96, 96, 576, 1536, 1536, 1440, 96, 1536,
  1536, 3072
};

const uint16_t N = sizeof(SONG_NOTES) / sizeof(SONG_NOTES[0]);

static uint16_t ticksToMs(int16_t ticks) {
  // ms_per_tick = (60000 / BPM) / TICKS_PER_BEAT
  // Do in integer math with rounding.
  uint32_t numerator = (uint32_t)ticks * 60000UL;
  uint32_t denom = (uint32_t)BPM * (uint32_t)TICKS_PER_BEAT;
  return (uint16_t)((numerator + denom / 2) / denom);
}

static uint16_t midiNoteToFreq(int16_t midiNote) {
  // A4 (69) = 440 Hz
  // f = 440 * 2^((n-69)/12)
  float n = (float)midiNote;
  float f = 440.0f * powf(2.0f, (n - 69.0f) / 12.0f);
  if (f < 60.0f) return 0;       // treat too-low as rest
  if (f > 4000.0f) return 4000;  // clamp for buzzer/tone()
  return (uint16_t)(f + 0.5f);
}

void setup() {
  pinMode(BUZZ_PIN, OUTPUT);
}

void loop() {
  for (uint16_t i = 0; i < N; i++) {
    int16_t note = (int16_t)pgm_read_word(&SONG_NOTES[i]);
    int16_t tks  = (int16_t)pgm_read_word(&SONG_TICKS[i]);

    uint16_t durMs = ticksToMs(tks);

    if (note < 0) {
      noTone(BUZZ_PIN);
      delay(durMs);
      continue;
    }

    int16_t playNote = note + TRANSPOSE;
    if (playNote < 0) playNote = 0;
    if (playNote > 127) playNote = 127;

    uint16_t freq = midiNoteToFreq(playNote);
    if (freq == 0) {
      noTone(BUZZ_PIN);
      delay(durMs);
    } else {
      tone(BUZZ_PIN, freq);
      delay(durMs);
      noTone(BUZZ_PIN);
      delay(5); // tiny separation
    }
  }

  delay(2000); // pause before repeating
}
