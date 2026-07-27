#!/usr/bin/env python3
"""
Interactive build+flash+configure for the Heltec Wireless Paper weather-station
repeater. Prompts for WiFi credentials and location, builds and uploads the
firmware over USB, then sends the 'set' commands over the serial console so
the device comes up fully configured -- no credentials are ever written to
platformio.ini or committed to the repo.

Usage:
  python3 deploy_weather_station.py [--port /dev/cu.usbserial-0001] [--env Heltec_Wireless_Paper_repeater]
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import time
import getpass

try:
    import serial
except ImportError:
    print("This script needs pyserial. Install it with: pip3 install pyserial")
    sys.exit(1)

DEFAULT_ENV = "Heltec_Wireless_Paper_repeater"
BAUD = 115200


def find_ports():
    return sorted(glob.glob("/dev/cu.usbserial-*") + glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.wchusbserial*"))


def pick_port(explicit):
    if explicit:
        return explicit
    ports = find_ports()
    if not ports:
        print("No USB serial device found. Plug in the board or pass --port explicitly.")
        sys.exit(1)
    if len(ports) == 1:
        print(f"Using serial port: {ports[0]}")
        return ports[0]
    print("Multiple serial ports found:")
    for i, p in enumerate(ports):
        print(f"  {i + 1}) {p}")
    choice = input(f"Pick a port [1-{len(ports)}]: ").strip()
    return ports[int(choice) - 1]


def prompt_config():
    print("\n--- WiFi / weather station configuration ---")
    ssid = input("WiFi SSID: ").strip()
    password = getpass.getpass("WiFi password (hidden): ")
    lat = input("Latitude (e.g. 52.1930): ").strip()
    lon = input("Longitude, negative for West (e.g. -0.9031): ").strip()
    interval = input("Weather fetch interval in seconds [900]: ").strip() or "900"
    return ssid, password, lat, lon, interval


def find_pio():
    """Locate the pio binary: PATH first, then alongside the running interpreter
    (so running this script with a venv's python just works)."""
    found = shutil.which("pio")
    if found:
        return found
    sibling = os.path.join(os.path.dirname(sys.executable), "pio")
    if os.path.exists(sibling):
        return sibling
    print("Could not find 'pio'. Install it into a venv and use that venv's python:")
    print("  python3 -m venv ~/.venvs/pio && ~/.venvs/pio/bin/pip install platformio pyserial")
    print("  ~/.venvs/pio/bin/python deploy_weather_station.py")
    sys.exit(1)


def build_and_upload(env, port):
    print(f"\n--- Building and flashing env '{env}' on {port} ---")
    cmd = [find_pio(), "run", "-e", env, "-t", "upload", "--upload-port", port]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("Build/upload failed.")
        sys.exit(result.returncode)


# '[weather] ...' lines noticed while waiting for or draining around CLI replies.
# Prefs survive a firmware upload, so an already-configured board often connects
# and fetches during the CLI wait -- without this the watch below misses it and
# then sits there until weather_interval elapses.
_weather_seen = []


def note_weather(text):
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[weather]"):
            _weather_seen.append(line)


def drain(ser, quiet=0.4, limit=4.0):
    """Read and discard until the device has been quiet for `quiet` seconds.

    reset_input_buffer() alone races with bytes still in flight, which made the
    previous command's reply show up against the next command.
    """
    deadline = time.time() + limit
    last_data = time.time()
    ser.reset_input_buffer()
    while time.time() < deadline:
        chunk = ser.read(4096)
        if chunk:
            note_weather(chunk.decode(errors="replace"))
            last_data = time.time()
        elif time.time() - last_data >= quiet:
            return
        time.sleep(0.05)


def read_reply(ser, timeout):
    """Read until the device emits a '-> ...' reply line, or timeout. Returns it or None."""
    deadline = time.time() + timeout
    buf = ""
    while time.time() < deadline:
        chunk = ser.read(4096).decode(errors="replace")
        if chunk:
            note_weather(chunk)
            buf += chunk
        # only the device's "-> ..." reply line matters, not the character echo
        for line in buf.splitlines():
            if "->" in line:
                return line.strip()
        time.sleep(0.2)
    return None


def wait_for_cli(ser, timeout=45):
    """Poll 'ver' until the CLI answers.

    The board needs well over the couple of seconds the old fixed sleep allowed
    before its CLI is up; sending config into a still-booting device wedged it
    part-way through the sequence.
    """
    print("  waiting for CLI to come up...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        ser.reset_input_buffer()
        ser.write(b"ver\r")
        reply = read_reply(ser, 3)
        if reply:
            print(f" ready ({reply.lstrip('-> ').strip()})")
            drain(ser)
            return True
        print(".", end="", flush=True)
    print(" TIMED OUT")
    return False


def send_command(ser, cmd, attempts=3, timeout=8):
    label = cmd if "wifi_pwd" not in cmd else "set wifi_pwd ****"
    for attempt in range(1, attempts + 1):
        drain(ser)
        ser.write((cmd + "\r").encode())
        reply = read_reply(ser, timeout)
        if reply:
            print(f"  {label}  {reply}")
            return True
        if attempt < attempts:
            print(f"  {label}  (no reply, retrying {attempt}/{attempts - 1})")
    print(f"  {label}  FAILED -- no reply after {attempts} attempts")
    return False


def watch_weather(ser, timeout=90):
    """Tail the '[weather] ...' log lines until the first fetch resolves."""
    print(f"\n--- Watching for the first fetch (up to {timeout}s) ---")
    for line in _weather_seen:
        print(f"  {line}")
    if any("fetch" in line for line in _weather_seen):
        return

    deadline = time.time() + timeout
    buf = ""
    while time.time() < deadline:
        buf += ser.read(4096).decode(errors="replace")
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if line.startswith("[weather]"):
                print(f"  {line}")
                if "fetch" in line:
                    return
        time.sleep(0.2)
    print("  (no fetch seen in this window -- an already-connected device won't")
    print("   refetch until weather_interval elapses; it will retry on its own)")


def configure_device(port, ssid, password, lat, lon, interval):
    print(f"\n--- Sending configuration over {port} ---")
    # Plain open only: do NOT set dtr/rts here. Opening normally leaves the board
    # running, but explicitly driving DTR/RTS low hard-resets it on both open and
    # close (verified via 'stats-core' uptime_secs).
    with serial.Serial(port, BAUD, timeout=0.5) as ser:
        if not wait_for_cli(ser):
            print("Device CLI never responded. Power-cycle the board and re-run with --skip-flash.")
            sys.exit(1)

        ok = True
        # weather settings first, WiFi credentials last: setting the credentials
        # kicks off the connect, and the fetch that follows blocks the loop
        if lat:
            ok &= send_command(ser, f"set weather_lat {lat}")
        if lon:
            ok &= send_command(ser, f"set weather_lon {lon}")
        ok &= send_command(ser, f"set weather_interval {interval}")
        ok &= send_command(ser, "set weather on")
        ok &= send_command(ser, f"set wifi_ssid {ssid}")
        ok &= send_command(ser, f"set wifi_pwd {password}")

        if ok:
            watch_weather(ser)

    if not ok:
        print("\nSome settings did not confirm. Re-run with --skip-flash to retry them.")
        sys.exit(1)
    print("\nDone. The device should connect to WiFi and start showing weather shortly.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Serial port (auto-detected if omitted)")
    parser.add_argument("--env", default=DEFAULT_ENV, help=f"PlatformIO env to build (default: {DEFAULT_ENV})")
    parser.add_argument("--skip-flash", action="store_true", help="Skip build/upload, only send configuration")
    args = parser.parse_args()

    port = pick_port(args.port)

    if not args.skip_flash:
        build_and_upload(args.env, port)

    ssid, password, lat, lon, interval = prompt_config()
    configure_device(port, ssid, password, lat, lon, interval)


if __name__ == "__main__":
    main()
