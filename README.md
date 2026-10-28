# Xbox 360 Controller Test

A terminal game that puts a wired Xbox 360 controller through every test: stick drift, buttons, D-pad, triggers, stick range, precision, rumble, LEDs, and the cable.

Built for **macOS (including Apple Silicon)**, where there is no working Xbox 360 driver. It talks to the controller directly over USB via libusb, so no kernel extension is needed.

## Run

```sh
./play.sh        # all 9 stages
./play.sh 7      # just one stage (e.g. 7 = rumble)
```

The first run installs `libusb` (Homebrew) and creates a Python venv with `pyusb`.
Keys: `s` skips a stage, `q` quits. Results are saved to `report.json`.

## Stages

| # | Stage | What it measures |
|---|-------|------------------|
| 1 | Drift detector | Resting offset and jitter, plus a snap-back test (flick 4 directions, release, see where it settles). Graded against the XInput deadzone (24% left / 26.5% right) |
| 2 | Button blitz | All 15 buttons incl. Guide and L3/R3: reaction time, dead buttons, wrong inputs, contact chatter/bounce |
| 3 | D-pad compass | All 8 directions incl. diagonals, phantom inputs |
| 4 | Trigger control | Full 0–255 range, number of distinct steps, precision holds |
| 5 | Spin cycle | Outer reach / circularity per 5° of rotation, weak angles |
| 6 | Target practice | Fine stick control |
| 7 | Rumble check | Each motor + both; power level at which each motor starts spinning |
| 8 | Ring of light | Player quadrants and animated LED patterns |
| 9 | Cable stress | Hold A and wiggle the cable: USB disconnects and dropouts |

USB polling rate, report jitter, and malformed reports are measured in the background and shown on the results screen.

> The right rumble motor is supposed to feel much lighter than the left. It has a small weight and makes a high-pitched buzz, while the left makes a heavy rumble.

## Supported controllers

Wired Xbox 360 controllers and XInput-compatible pads (tested on a PDP Afterglow `24c6:fafd`). Other pads are found automatically by their XInput USB interface class. The wireless receiver is not supported.
