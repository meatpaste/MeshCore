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


def build_and_upload(env, port):
    print(f"\n--- Building and flashing env '{env}' on {port} ---")
    cmd = ["pio", "run", "-e", env, "-t", "upload", "--upload-port", port]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("Build/upload failed.")
        sys.exit(result.returncode)


def send_command(ser, cmd, settle=1.0):
    ser.reset_input_buffer()
    ser.write((cmd + "\r").encode())
    time.sleep(settle)
    reply = ser.read(4096).decode(errors="replace")
    # only print the device's "-> ..." reply line, not the character echo
    for line in reply.splitlines():
        if "->" in line:
            print(f"  {cmd}  {line.strip()}")
            return
    print(f"  {cmd}  (no reply -- check device is booted and CLI is responsive)")


def configure_device(port, ssid, password, lat, lon, interval):
    print(f"\n--- Sending configuration over {port} ---")
    # upload already hard-resets the board; give it a moment to finish setup()
    time.sleep(2.5)
    with serial.Serial(port, BAUD, timeout=0.3) as ser:
        time.sleep(0.3)
        send_command(ser, f"set wifi_ssid {ssid}")
        send_command(ser, f"set wifi_pwd {password}")
        if lat:
            send_command(ser, f"set lat {lat}")
        if lon:
            send_command(ser, f"set lon {lon}")
        send_command(ser, f"set weather_interval {interval}")
        send_command(ser, "set weather on")
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
