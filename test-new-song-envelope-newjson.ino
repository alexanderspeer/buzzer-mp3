/*
Space Oddity (melody-extracted) buzzer player with a simple volume envelope.

Source data: David_Bowie_-_Space_Oddity.melody.arduino.json :contentReference[oaicite:0]{index=0}

Hardware (from your setup):
- Passive buzzer: D9 -> 300Ω -> buzzer+ ; buzzer- -> GND
- Volume pot: A1  (optional, still works if not connected)
- Pitch pot:  A2  (optional, still works if not connected)

What this tries to do to sound more like the song:
- Uses the melody-track extraction data (already cleaner than global highest-note)
- Adds articulation (tiny gap between notes)
- Adds a simple attack/release envelope using fast gating (reduces click/harshness and improves phrasing)
- Optional: lets your pots control overall volume + transpose

Notes:
- Envelope is implemented by rapidly turning tone on/off (works on passive buzzer).
- If it feels too slow/fast, adjust BPM.
*/

#include <Arduino.h>
#include <math.h>
#include <avr/pgmspace.h>

static const uint8_t BUZZ_PIN = 9;

// Optional pots (safe even if disconnected)
static const uint8_t VOL_POT_PIN = A1;   // 0..1023
static const uint8_t PIT_POT_PIN = A2;   // 0..1023

// Timing from JSON
static const uint16_t TICKS_PER_BEAT = 384;

// Adjust to taste
static const uint16_t BPM = 132;         // try 110..150

// Envelope/gating parameters
static const uint8_t  GATE_PERIOD_MS = 3;   // 2-4ms. Smaller = smoother but more CPU
static const uint16_t MIN_NOTE_MS_GAP = 8;  // small silence between notes (phrase clarity)

// Attack/release behavior (as % of note duration, clamped)
static const uint8_t  ATTACK_PCT  = 10;      // percent of note duration
static const uint8_t  RELEASE_PCT = 12;      // percent of note duration
static const uint16_t ATTACK_MIN_MS  = 10;
static const uint16_t ATTACK_MAX_MS  = 35;
static const uint16_t RELEASE_MIN_MS = 12;
static const uint16_t RELEASE_MAX_MS = 50;

// ---------------------- Song data (note=-1 is rest) ----------------------
// This is copied from the JSON events. :contentReference[oaicite:1]{index=1}

static const int16_t SONG_NOTES[] PROGMEM = {
  -1,60,-1,60,-1,60,59,-1,60,-1,62,-1,60,-1,62,-1,60,62,-1,60,-1,62,-1,60,-1,60,-1,
  64,-1,64,67,65,64,-1,64,-1,64,-1,64,-1,64,-1,65,-1,65,-1,65,-1,65,-1,65,-1,67,
  64,-1,65,-1,65,-1,65,-1,65,-1,65,67,64,-1,65,-1,65,-1,65,-1,65,-1,65,67,64,-1,
  65,-1,65,-1,65,-1,65,-1,65,64,-1,64,-1,64,-1,64,-1,65,-1,65,-1,65,-1,65,-1,65,
  -1,65,-1,67,64,65,64,-1,62,65,64,62,60,-1,57,-1,60,59,57,-1,55,57,55,-1,50,-1,
  60,59,57,55,-1,58,-1,58,60,57,-1,57,-1,57,-1,55,-1,55,57,55,53,-1,
  64,-1,64,-1,67,65,64,-1,64,-1,64,-1,64,-1,64,-1,65,-1,65,-1,65,-1,65,-1,65,67,
  64,-1,64,-1,65,-1,65,-1,65,-1,62,65,64,62,60,-1,
  60,59,57,-1,55,57,55,-1,50,-1,
  60,59,57,55,-1,58,-1,58,60,57,-1,57,-1,57,-1,55,57,55,53
};

static const uint16_t SONG_TICKS[] PROGMEM = {
  15360,192,96,192,192,384,1056,960,96,96,96,96,96,96,96,96,192,96,96,96,96,96,96,192,96,864,13824,
  384,96,192,672,96,576,96,96,96,96,96,96,96,96,96,192,1056,192,96,96,96,96,288,96,192,96,96,
  768,96,192,96,864,96,96,96,288,96,96,96,192,192,192,576,96,1248,4608,96,96,96,96,96,96,96,96,96,96,
  96,192,192,192,192,192,192,96,96,960,192,96,96,96,96,96,96,96,96,192,96,192,96,96,96,192,192,96,288,2
  96,96,864,96,96,96,1152,192,192,288,96,96,96,96,768,192,1056,288,96,1248,384,192,96,288,192,288,96,96,
  96,96,96,96,192,288,96,1248,12672,
  288,96,192,96,672,96,576,96,96,96,96,96,96,96,96,96,192,1056,192,96,96,96,96,288,96,192,96,
  96,384,96,192,96,192,96,864,1824,96,288,96,96,96,672,8160,
  1152,192,192,288,96,96,96,96,768,1152,
  96,288,96,1248,384,192,96,288,192,288,96,96,96,96,96,288,288,96,1248
};

static const uint16_t N_EVENTS = sizeof(SONG_TICKS) / sizeof(SONG_TICKS[0]);

// ---------------------- Helpers ----------------------

static inline uint16_t ticksToMs(uint16_t ticks) {
  // ms_per_tick = (60000/BPM) / TICKS_PER_BEAT
  // Use integer math with rounding.
  uint32_t num = (uint32_t)ticks * 60000UL;
  uint32_t den = (uint32_t)BPM * (uint32_t)TICKS_PER_BEAT;
  return (uint16_t)((num + den / 2) / den);
}

static inline uint16_t midiNoteToFreq(int16_t midiNote) {
  // f = 440 * 2^((n-69)/12)
  // Using float here is acceptable at this event rate.
  float f = 440.0f * powf(2.0f, ((float)midiNote - 69.0f) / 12.0f);
  if (f < 60.0f) return 0;        // too low for your buzzer: treat as rest
  if (f > 4000.0f) return 4000;   // clamp
  return (uint16_t)(f + 0.5f);
}

static inline int8_t pitchPotToTranspose(int pot0_1023) {
  // Map to -12..+12 semitones with a deadzone near center.
  // Safe even if pot is floating; you can comment this out if not using pitch.
  int centered = pot0_1023 - 512;
  if (abs(centered) < 35) return 0;
  return (int8_t)map(pot0_1023, 0, 1023, -12, 12);
}

static inline uint8_t volPotToLevel(int pot0_1023) {
  // Map to 10..255 (avoid near-zero that can sound choppy)
  return (uint8_t)map(pot0_1023, 0, 1023, 10, 255);
}

static void playToneWithEnvelope(uint16_t freq, uint16_t durMs, uint8_t volLevel) {
  if (freq == 0 || durMs == 0) {
    noTone(BUZZ_PIN);
    delay(durMs);
    return;
  }

  // Compute attack/release in ms, clamped
  uint16_t attackMs  = (uint16_t)((uint32_t)durMs * ATTACK_PCT / 100UL);
  uint16_t releaseMs = (uint16_t)((uint32_t)durMs * RELEASE_PCT / 100UL);

  if (attackMs < ATTACK_MIN_MS) attackMs = ATTACK_MIN_MS;
  if (attackMs > ATTACK_MAX_MS) attackMs = ATTACK_MAX_MS;

  if (releaseMs < RELEASE_MIN_MS) releaseMs = RELEASE_MIN_MS;
  if (releaseMs > RELEASE_MAX_MS) releaseMs = RELEASE_MAX_MS;

  if (attackMs + releaseMs >= durMs) {
    // Very short notes: shrink envelope so we still have a hold section
    uint16_t half = durMs / 2;
    attackMs = min(attackMs, half);
    releaseMs = min(releaseMs, durMs - attackMs);
  }

  uint16_t holdMs = (durMs > (attackMs + releaseMs)) ? (durMs - attackMs - releaseMs) : 0;

  auto gateFor = [&](uint16_t ms, uint8_t levelStart, uint8_t levelEnd) {
    if (ms == 0) return;
uint16_t steps = (ms / GATE_PERIOD_MS > 0) ? (ms / GATE_PERIOD_MS) : 1;
    for (uint16_t s = 0; s < steps; s++) {
      // linear interpolate envelope level
      uint8_t lvl = (uint8_t)(levelStart + (uint32_t)(levelEnd - levelStart) * s / (steps - 1 ? steps - 1 : 1));

      // Convert lvl (0..255) into on/off within the gate period
      uint16_t onMs = (uint16_t)((uint32_t)GATE_PERIOD_MS * (uint32_t)lvl / 255UL);
      if (onMs > 0) {
        tone(BUZZ_PIN, freq);
        delay(onMs);
      }
      noTone(BUZZ_PIN);
      uint16_t offMs = GATE_PERIOD_MS - onMs;
      if (offMs > 0) delay(offMs);
    }
  };

  // Attack: 0 -> volLevel
  gateFor(attackMs, 0, volLevel);

  // Hold: constant volLevel (no need to interpolate)
  if (holdMs > 0) {
uint16_t steps = (ms / GATE_PERIOD_MS > 0) ? (ms / GATE_PERIOD_MS) : 1;
    for (uint16_t s = 0; s < steps; s++) {
      uint16_t onMs = (uint16_t)((uint32_t)GATE_PERIOD_MS * (uint32_t)volLevel / 255UL);
      if (onMs > 0) {
        tone(BUZZ_PIN, freq);
        delay(onMs);
      }
      noTone(BUZZ_PIN);
      uint16_t offMs = GATE_PERIOD_MS - onMs;
      if (offMs > 0) delay(offMs);
    }
  }

  // Release: volLevel -> 0
  gateFor(releaseMs, volLevel, 0);

  noTone(BUZZ_PIN);
}

// ---------------------- Main ----------------------

void setup() {
  pinMode(BUZZ_PIN, OUTPUT);
  pinMode(VOL_POT_PIN, INPUT);
  pinMode(PIT_POT_PIN, INPUT);
}

void loop() {
  for (uint16_t i = 0; i < N_EVENTS; i++) {
    int16_t note = (int16_t)pgm_read_word(&SONG_NOTES[i]);
    uint16_t ticks = (uint16_t)pgm_read_word(&SONG_TICKS[i]);
    uint16_t durMs = ticksToMs(ticks);

    // Optional live controls (safe if pots are absent; comment out if you want fixed playback)
    uint8_t  volLevel = volPotToLevel(analogRead(VOL_POT_PIN));
    int8_t   transpose = pitchPotToTranspose(analogRead(PIT_POT_PIN));

    if (note < 0) {
      noTone(BUZZ_PIN);
      delay(durMs);
      continue;
    }

    int16_t playNote = note + transpose;
    if (playNote < 0) playNote = 0;
    if (playNote > 127) playNote = 127;

    uint16_t freq = midiNoteToFreq(playNote);

    // Slightly shorten the sounding portion to create articulation, without breaking rhythm
    uint16_t gap = MIN_NOTE_MS_GAP;
    if (durMs <= gap + 5) gap = 0;

    uint16_t playMs = (gap > 0) ? (durMs - gap) : durMs;

    playToneWithEnvelope(freq, playMs, volLevel);

    if (gap > 0) {
      noTone(BUZZ_PIN);
      delay(gap);
    }
  }

  delay(2500);
}
