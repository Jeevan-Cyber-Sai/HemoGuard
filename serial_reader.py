"""HemoGuard serial reader.

Reads newline-delimited JSON sensor readings from an Arduino over serial and
mirrors the most recent one into sensor_data.json for the dashboard backend.

Usage:
    python serial_reader.py --port COM3 --baud 115200
    python serial_reader.py --list-ports
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import serial
from serial.tools import list_ports

RETRY_SECONDS = 3
DEFAULT_PORT = os.environ.get("HEMOGUARD_PORT", "COM3")
DEFAULT_BAUD = int(os.environ.get("HEMOGUARD_BAUD", "115200"))
DEFAULT_OUTPUT = "sensor_data.json"

EXPECTED_FIELDS = ("weight", "spo2", "pulse", "red", "green", "blue", "clear", "led")


def timestamp():
    return datetime.now().isoformat(timespec="milliseconds")


def available_ports():
    return [p.device for p in list_ports.comports()]


def parse_reading(line):
    """Turn one raw serial line into a reading dict, or None if unusable."""
    line = line.strip()
    if not line:
        return None

    try:
        reading = json.loads(line)
    except json.JSONDecodeError:
        print(f"[{timestamp()}] SKIP  malformed JSON: {line!r}")
        return None

    if not isinstance(reading, dict):
        print(f"[{timestamp()}] SKIP  expected a JSON object: {line!r}")
        return None

    missing = [field for field in EXPECTED_FIELDS if field not in reading]
    if missing:
        print(f"[{timestamp()}] WARN  missing fields {missing} in: {line!r}")

    return reading


def write_atomic(reading, output_path):
    """Write the reading to output_path atomically via temp file + rename."""
    payload = dict(reading)
    payload["timestamp"] = timestamp()

    directory = os.path.dirname(os.path.abspath(output_path))
    temp_path = os.path.join(directory, f".{os.path.basename(output_path)}.tmp")

    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(temp_path, output_path)
    return payload


def format_reading(reading):
    return (
        f"weight={reading.get('weight', '?'):>8} g  "
        f"spo2={reading.get('spo2', '?'):>3}%  "
        f"pulse={reading.get('pulse', '?'):>3} bpm  "
        f"rgb=({reading.get('red', '?')},{reading.get('green', '?')},{reading.get('blue', '?')})  "
        f"clear={reading.get('clear', '?')}  "
        f"led={reading.get('led', '?')}"
    )


def read_stream(connection, output_path):
    """Consume lines until the connection drops. Raises SerialException on loss."""
    while True:
        raw = connection.readline()
        if not raw:
            continue  # read timeout, nothing sent yet

        try:
            line = raw.decode("utf-8", errors="replace")
        except UnicodeDecodeError:
            print(f"[{timestamp()}] SKIP  undecodable bytes: {raw!r}")
            continue

        reading = parse_reading(line)
        if reading is None:
            continue

        write_atomic(reading, output_path)
        print(f"[{timestamp()}] {format_reading(reading)}")


def run(port, baud, output_path):
    print(f"[{timestamp()}] HemoGuard serial reader -> {os.path.abspath(output_path)}")
    print(f"[{timestamp()}] Target {port} @ {baud} baud. Ctrl+C to stop.")

    while True:
        try:
            with serial.Serial(port, baud, timeout=1) as connection:
                print(f"[{timestamp()}] CONNECTED to {port}")
                connection.reset_input_buffer()
                read_stream(connection, output_path)

        except serial.SerialException as exc:
            print(f"[{timestamp()}] DISCONNECTED: {exc}")
            ports = available_ports()
            print(f"[{timestamp()}] Available ports: {', '.join(ports) if ports else 'none'}")
            print(f"[{timestamp()}] Retrying in {RETRY_SECONDS}s...")
            time.sleep(RETRY_SECONDS)

        except OSError as exc:
            print(f"[{timestamp()}] PORT ERROR: {exc}")
            print(f"[{timestamp()}] Retrying in {RETRY_SECONDS}s...")
            time.sleep(RETRY_SECONDS)


def main():
    parser = argparse.ArgumentParser(description="HemoGuard Arduino serial reader")
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"serial port (default: {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"baud rate (default: {DEFAULT_BAUD})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"output file (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--list-ports", action="store_true", help="list serial ports and exit")
    args = parser.parse_args()

    # Keep readings visible when stdout is a pipe (e.g. launched by the backend).
    sys.stdout.reconfigure(line_buffering=True)

    if args.list_ports:
        for info in list_ports.comports():
            print(f"{info.device}\t{info.description}")
        if not list_ports.comports():
            print("No serial ports detected.")
        return 0

    try:
        run(args.port, args.baud, args.output)
    except KeyboardInterrupt:
        print(f"\n[{timestamp()}] Stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
