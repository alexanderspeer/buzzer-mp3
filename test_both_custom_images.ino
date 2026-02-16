const int BUZZ_PIN = 9;

// Frequencies in Hz (rounded, low freqs treated as rests)
const int melody[] = {
  0, 262, 0, 392, 0, 523, 0, 659, 392, 196, 0, 622,
  311, 0, 98, 0, 131, 0, 98, 0, 131, 0, 98, 0,
  131, 0, 98, 0, 131, 0, 98, 0, 131, 0, 98, 0,
  262, 0, 392, 0, 523, 0, 622, 784, 0, 1047,
  523, 330, 0, 98, 0, 131, 0, 98, 0, 131, 0,
  98, 0, 131, 0, 98, 0, 131, 0, 98, 0, 131,
  0, 98, 0, 262, 0, 392, 0, 523, 0, 659, 131,
  0, 880, 698, 523, 147, 87, 880, 988, 1047,
  1175, 1319, 698, 1397, 1568, 1319, 1397,
  1568, 784, 1760, 880, 988, 1976, 2093, 1047
};

// Durations in milliseconds (copied directly)
const int durations[] = {
  4057,1886,222,1833,212,3322,289,112,19,84,112,3693,
  78,107,88,207,89,209,113,195,93,229,105,212,
  96,249,110,234,102,289,118,264,110,313,115,358,
  1674,318,1801,148,3462,345,19,103,177,3692,
  10,10,154,92,238,73,240,105,226,86,228,88,
  253,89,269,102,264,93,282,96,304,98,315,103,
  91,251,1746,323,1735,334,3493,272,181,27,
  72,3522,10,128,13,38,362,338,2775,1338,
  588,13,675,2863,300,300,2313,38,1466,47,
  16,1797,9363,62
};

const int N = sizeof(melody) / sizeof(melody[0]);

void setup() {
  pinMode(BUZZ_PIN, OUTPUT);
}

void loop() {
  for (int i = 0; i < N; i++) {
    int freq = melody[i];
    int dur  = durations[i];

    if (freq <= 0) {
      noTone(BUZZ_PIN);
      delay(dur);
    } else {
      // Clamp to buzzer-friendly range
      if (freq < 60) freq = 60;
      if (freq > 4000) freq = 4000;

      tone(BUZZ_PIN, freq);
      delay(dur);
      noTone(BUZZ_PIN);
    }

    delay(10); // small separation between events
  }

  delay(3000); // pause before repeating
}
