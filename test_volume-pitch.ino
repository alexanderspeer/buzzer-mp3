
/*
Plays the song extracted from:
David_Bowie_-_Space_Oddity.arduino.json

Hardware:
- Passive buzzer on D9 (D9 -> 300Ω -> buzzer+ ; buzzer- -> GND)
- OLED I2C: SDA=A4, SCL=A5 (not used in this sketch)
- Volume pot wiper -> A1 (outer legs to 5V and GND)
- Pitch pot  wiper -> A2 (outer legs to 5V and GND)
- Button on D5 -> GND (uses INPUT_PULLUP)

Controls:
- A2 pitch: uniform multiplier on every note (about 0.5x to 2.0x)
- A1 volume: perceived loudness via fast on/off gating (duty cycle)
- D5 button: toggle pause/resume
*/

#include <Arduino.h>
#include <avr/pgmspace.h>
#include <math.h>

const uint8_t BUZZ_PIN      = 9;
const uint8_t VOL_POT_PIN   = A1;
const uint8_t PITCH_POT_PIN = A2;
const uint8_t BTN_PIN       = 5;

// Tempo control
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

// Volume gating slice length (ms). Smaller = smoother volume but more overhead.
const uint8_t SLICE_MS = 8;

// Button debounce
const uint16_t DEBOUNCE_MS = 25;

static bool paused = false;

static uint16_t ticksToMs(int16_t ticks) {
  uint32_t numerator = (uint32_t)ticks * 60000UL;
  uint32_t denom = (uint32_t)BPM * (uint32_t)TICKS_PER_BEAT;
  return (uint16_t)((numerator + denom / 2) / denom);
}

static uint16_t midiNoteToFreq(int16_t midiNote) {
  float n = (float)midiNote;
  float f = 440.0f * powf(2.0f, (n - 69.0f) / 12.0f);
  if (f < 60.0f) return 0;       // treat too-low as rest
  if (f > 4000.0f) return 4000;  // clamp for buzzer/tone()
  return (uint16_t)(f + 0.5f);
}

static float readPitchMultiplier() {
  int raw = analogRead(PITCH_POT_PIN); // 0..1023
  // 0.5x .. 2.0x
  return 0.5f + (raw / 1023.0f) * 1.5f;
}

static float readVolumeDuty() {
  int raw = analogRead(VOL_POT_PIN); // 0..1023
  return raw / 1023.0f;             // 0.0 .. 1.0
}

static void handlePauseButton() {
  static uint8_t lastStable = HIGH;
  static uint8_t lastRead = HIGH;
  static uint32_t lastChangeMs = 0;

  uint8_t r = digitalRead(BTN_PIN);

  if (r != lastRead) {
    lastRead = r;
    lastChangeMs = millis();
  }

  if ((millis() - lastChangeMs) > DEBOUNCE_MS && r != lastStable) {
    lastStable = r;

    // Toggle on press (HIGH->LOW because INPUT_PULLUP)
    if (lastStable == LOW) {
      paused = !paused;
      if (paused) noTone(BUZZ_PIN);
    }
  }
}

static void waitWhilePaused() {
  while (paused) {
    handlePauseButton();
    delay(5);
  }
}

static void playRest(uint16_t durMs) {
  noTone(BUZZ_PIN);
  uint32_t t0 = millis();
  while ((uint32_t)(millis() - t0) < durMs) {
    handlePauseButton();
    waitWhilePaused();
    delay(2);
  }
}

static void playToneGated(uint16_t freq, uint16_t durMs) {
  float pitchMul = readPitchMultiplier();
  float volDuty  = readVolumeDuty();

  uint32_t adj = (uint32_t)(freq * pitchMul + 0.5f);
  if (adj < 60) adj = 60;
  if (adj > 4000) adj = 4000;
  uint16_t adjFreq = (uint16_t)adj;

  // Convert duty to on-time per slice
  if (volDuty < 0.0f) volDuty = 0.0f;
  if (volDuty > 1.0f) volDuty = 1.0f;

  uint16_t remaining = durMs;

  while (remaining > 0) {
    handlePauseButton();
    waitWhilePaused();

    uint8_t slice = (remaining >= SLICE_MS) ? SLICE_MS : (uint8_t)remaining;
    uint8_t onMs  = (uint8_t)(slice * volDuty + 0.5f);
    if (onMs > slice) onMs = slice;

    if (onMs > 0) {
      tone(BUZZ_PIN, adjFreq);
      delay(onMs);
    } else {
      noTone(BUZZ_PIN);
    }

    if (slice > onMs) {
      noTone(BUZZ_PIN);
      delay((uint8_t)(slice - onMs));
    } else {
      noTone(BUZZ_PIN);
    }

    remaining -= slice;
  }

  // Tiny separation between notes
  delay(5);
}

void setup() {
  pinMode(BUZZ_PIN, OUTPUT);
  pinMode(BTN_PIN, INPUT_PULLUP);
  analogRead(VOL_POT_PIN);
  analogRead(PITCH_POT_PIN);
}

void loop() {
  for (uint16_t i = 0; i < N; i++) {
    handlePauseButton();
    waitWhilePaused();

    int16_t note = (int16_t)pgm_read_word(&SONG_NOTES[i]);
    int16_t tks  = (int16_t)pgm_read_word(&SONG_TICKS[i]);
    uint16_t durMs = ticksToMs(tks);

    if (note < 0) {
      playRest(durMs);
      continue;
    }

    int16_t playNote = note + TRANSPOSE;
    if (playNote < 0) playNote = 0;
    if (playNote > 127) playNote = 127;

    uint16_t baseFreq = midiNoteToFreq(playNote);
    if (baseFreq == 0) {
      playRest(durMs);
    } else {
      playToneGated(baseFreq, durMs);
    }
  }

  // Pause before repeating, still responsive to button
  playRest(2000);
}

