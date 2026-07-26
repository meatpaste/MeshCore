# CLAUDE.md — fork-specific notes

This file documents the "Weatherstation Repeater Mod" fork on top of upstream
[meshcore-dev/MeshCore](https://github.com/meshcore-dev/MeshCore). See the
top of `README.md` for the user-facing description.

## Git workflow — read this first

- All fork-specific work lives on the **`weather-station`** branch. **Never
  commit directly to `main`.** Commit to `weather-station`, then fast-forward
  merge into `main`, then push both:
  ```
  git checkout weather-station
  git commit -m "..."
  git checkout main
  git merge weather-station   # should fast-forward
  git push origin main
  git push origin weather-station
  ```
- `main` is kept identical to `origin/main`'s history plus fast-forwards from
  `weather-station`, so it can always cleanly pull from `upstream` without
  the fork's changes getting in the way of that sync:
  ```
  git checkout main
  git fetch upstream && git merge upstream/main && git push origin main
  git checkout weather-station
  git rebase main   # replay fork commits on top; resolve conflicts (see below)
  ```
- `upstream` remote = `https://github.com/meshcore-dev/MeshCore.git` (already
  added). `origin` = this fork (`meatpaste/MeshCore`).
- Likely rebase-conflict hotspots against upstream: `src/helpers/CommonCLI.h`
  / `.cpp` (NodePrefs struct — fields were appended at the end, offsets
  documented inline), the two `UITask.cpp` files, and
  `variants/heltec_wireless_paper/platformio.ini`.

## What this fork adds

WiFi-fetched weather (temp/condition/humidity/wind+direction) rendered on the
Heltec Wireless Paper v1.1 e-ink display, for both `simple_repeater` and
`companion_radio` builds of that variant. Repeater build also shows router
stats (active neighbours, packets relayed/heard). Config is via serial CLI
`set` commands — **never** bake WiFi credentials into `platformio.ini` or
source; the whole point of `deploy_weather_station.py` is to keep them out of
git.

Key new/changed files:
- `examples/companion_radio/ui-new/WeatherClient.h/.cpp` and
  `examples/simple_repeater/WeatherClient.h/.cpp` — **deliberately
  duplicated**, not shared. companion_radio uses its own `NodePrefs`
  (`examples/companion_radio/NodePrefs.h`); simple_repeater/room_server/etc.
  share a different `NodePrefs` from `src/helpers/CommonCLI.h`. Don't try to
  unify these without checking every other role (room_server, sensor,
  bridges) that also uses `CommonCLI.h`'s struct.
- `src/helpers/ui/WeatherIcons.h`, `src/helpers/ui/WindArrows.h` — shared XBM
  bitmaps (56x56 weather condition icons, 24x24 wind-direction arrows).
  `DisplayDriver::drawXbm()` expects **MSB-first** bit packing with each row
  padded to a byte boundary (not classic LSB XBM) — see
  `E213Display::drawXbm()`. Icons were generated with Pillow + a small Python
  packer script, not hand-drawn.
- `src/helpers/ui/DisplayDriver.h` / `E213Display.h/.cpp` — added
  `setLargeFont`/`setMediumFont`/`setSmallFont(bool)` (no-op default on the
  base class, so other boards/displays are unaffected). These switch to
  bundled Adafruit-GFX `FreeSansBold24pt7b` / `FreeSansBold12pt7b` /
  `FreeSans9pt7b` fonts from the `heltec-eink-modules` lib for smoother large
  text. **Gotcha**: GFX custom fonts anchor `y` at the text **baseline**, not
  top-left like the classic bitmap font — positioning is very different from
  the rest of the UI, and each E213Display font-switch method calls
  `setTextSize(1)` afterward to avoid double-scaling on top of the font's own
  size.
- `deploy_weather_station.py` — one-command build+flash+configure. Sends
  `set wifi_ssid/wifi_pwd/lat/lon/weather_interval/weather on` over serial
  after upload. Requires `pyserial`.

## Hardware/serial gotchas (Heltec Wireless Paper v1.1, ESP32-S3)

- Board shows up as `/dev/cu.usbserial-0001` on macOS (CP210x-style USB-UART
  bridge, not `usbmodem`).
- **Opening the serial port (pyserial/`stty`) does NOT reliably reset this
  board** — DTR/RTS toggling on open was flaky/unreliable in testing. Only
  `esptool`'s flash-triggered hard reset (i.e. after `pio run -t upload`)
  reliably reboots it. Don't assume you can open+reset+read in one shot for
  debugging; flash something (even a no-op re-upload) to guarantee a reset.
- `simple_repeater`'s serial CLI is **always active from boot** (see
  `examples/simple_repeater/main.cpp` `loop()`) — no button press needed.
  This is why the repeater build was much easier to script/test against than
  companion_radio.
- `companion_radio`'s CLI is normally binary BLE/USB framing; there's a
  separate plaintext "CLI Rescue" mode (`enterCLIRescue()` in `MyMesh.cpp`)
  reachable only by **long-pressing the USR button within 8 seconds of
  boot**. This was never successfully exercised end-to-end in this session
  (timing is hard to hit non-interactively) — if you need to configure a
  companion_radio build's weather settings, expect to do it by hand with a
  real terminal, not scripted.
- Display is `E213Display`, **250x122** landscape e-ink (not the small
  128x64 OLED some other Heltec variants use) — there's more room than a
  first glance at existing HomeScreen code (tuned for smaller displays)
  suggests.

## Weather data

- Source: Open-Meteo (`https://api.open-meteo.com/v1/forecast`), no API key.
  Query params used: `current=temperature_2m,relative_humidity_2m,
  wind_speed_10m,wind_direction_10m,weather_code&wind_speed_unit=mph`.
- Location uses a dedicated `weather_lat`/`weather_lon` pair (`set weather_lat`/
  `set weather_lon`), separate from the node's own `node_lat`/`node_lon` (used
  for advertising position) — makes sense since a repeater's advertised
  position isn't necessarily where you want weather for. On `simple_repeater`
  (`src/helpers/CommonCLI.h`'s shared `NodePrefs`), `weather_lat`/`weather_lon`
  default to the node's `node_lat`/`node_lon` on first load if unset, so
  existing configured devices keep working after upgrading. companion_radio
  (`examples/companion_radio/NodePrefs.h`) already had its own separate
  `weather_lat`/`weather_lon` fields from the start.
- `WiFiClientSecure::setInsecure()` is used — no TLS cert pinning, a
  deliberate simplification for embedded use, flagged here in case that's
  ever revisited.
- `HTTPClient::GET()` blocks the main loop for ~1-3s during a fetch (no
  async HTTP client wired in). Default fetch interval is 900s (15 min,
  `set weather_interval <secs>`, range 60-86400) so this is an accepted
  tradeoff, not a bug.
- A response-parsing gotcha hit during bring-up: `deserializeJson()` reading
  directly off `http.getStream()` failed with chunked transfer encoding;
  buffering the body with `http.getString()` first fixed it. Keep it that
  way if touching `doFetch()`.

## Build/flash

```
export PATH="$PATH:$HOME/Library/Python/3.9/bin"   # if pio isn't already on PATH
pio run -e Heltec_Wireless_Paper_repeater -t upload --upload-port /dev/cu.usbserial-0001
pio run -e Heltec_Wireless_Paper_companion_radio_usb -t upload --upload-port /dev/cu.usbserial-0001
```
Or just run `python3 deploy_weather_station.py` for the full interactive flow.
