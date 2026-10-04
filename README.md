# ESP32-S3 Internet Monitor

This project runs on MicroPython on an ESP32-S3 and monitors internet connectivity. It checks public IP endpoints, detects outages, and can power-cycle a connected router through a relay when the WAN goes down. It also shows status information on an optional SH1107 OLED display.

## Features

- Wi-Fi connection management with DHCP or static-IP support
- Periodic connectivity checks against public IP endpoints
- Automatic router power cycling using a relay
- Optional OLED status display and button wake-up
- Local activity logging and a small web UI for checking device state

## Hardware

This project is designed for an ESP32-S3 board with:

- SH1107 128x128 OLED display on I2C
- relay module connected to GPIO 6
- momentary button on GPIO 7 for display wake-up
- router or modem powered through the relay board

See [hardware/hardware_wiring.txt](hardware/hardware_wiring.txt) for the wiring notes used by this project.

## Quick start

1. Update the Wi-Fi credentials and network settings in `config.py`.
2. Adjust the timezone and static IP values if needed.
3. Copy these files to the MicroPython device:
   - `main.py`
   - `config.py`
   - `sh1107.py`
4. Run `main.py` on the board.

## Configuration

The project expects a local `config.py` file at the project root. This file contains the Wi-Fi SSID, password, network mode, static-IP settings, and timezone. The included file already has placeholder values that you must replace before deployment.

Example settings are defined in the project `config.py`:

- `WIFI_SSID` and `WIFI_PASSWORD`
- `NETWORK_MODE` set to `"dhcp"` or `"static"`
- `STATIC_IP`, `STATIC_GATEWAY`, and `STATIC_DNS` when using static mode
- `TIMEZONE` set to `"UTC"`, `"Europe/Berlin"`, or an offset such as `"+05:30"`

## Project files

- `main.py` — monitoring logic, networking, relay control, OLED handling, and runtime loop
- `config.py` — local deployment settings for Wi-Fi and network behavior
- `sh1107.py` — driver for the SH1107 OLED display
- `hardware/hardware_wiring.txt` — hardware connection notes
- `LICENSE` — MIT license

## Runtime files

The device creates log files such as `/activity.log` and `/activity.old` while running. These are runtime artifacts and are excluded from version control via `.gitignore`.

## Notes

- Keep Wi-Fi credentials and other local values out of public commits.
- The relay logic and GPIO assumptions should be verified against the actual hardware before connecting the router or modem.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
