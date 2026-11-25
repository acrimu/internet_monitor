# ESP32-S3 Internet Monitor firmware

The MicroPython firmware monitors IPv4 internet connectivity from an ESP32-S3 and can power-cycle a router or modem through a relay. It reports status over serial and an optional OLED, records activity on the device, and serves a local web dashboard.

## Startup and network configuration

- The firmware initializes the relay with router power enabled. The default wiring uses GPIO 6, with level 0 for power on and level 1 for power off.
- The ESP32 joins the configured Wi-Fi network in station mode. Set the SSID and password in `config.py`. The placeholder SSID triggers a setup message and stops the monitor.
- The ESP32 can obtain IPv4 settings via DHCP or use a static address with a CIDR prefix, gateway, and DNS server. The firmware validates the static settings before applying them.
- If Wi-Fi is disconnected, the firmware attempts to reconnect and allows up to 25 seconds for the connection. It reports connection progress and the assigned IP address over serial and on the OLED.

## Internet monitoring

Each monitoring iteration checks Wi-Fi and, when connected, attempts TCP connections to `1.1.1.1:443` and `8.8.8.8:443`. Either successful connection is enough to report `Internet OK`. Each target has a four-second socket timeout. Numeric addresses let the probes run independently of DNS.

If Wi-Fi cannot connect or both probes fail, the iteration counts as a failure. The firmware waits 30 seconds after each iteration, so connection attempts and probe timeouts add to the time between results. It tracks consecutive failures, total successful and failed checks, and automatic resets in memory. A successful check clears the consecutive failure count.

These probes measure reachability to the configured endpoints. A router whose Wi-Fi still works can therefore be reported offline, and a successful probe does not establish that DNS or every internet service works.

## Automatic router recovery

After eight consecutive failed checks, the firmware can trigger an automatic power cycle. This applies to both Wi-Fi disconnections and internet outages with Wi-Fi still connected.

| Stage | Firmware behavior |
| --- | --- |
| First eligible power cycle | Logs the reset and switches off the router for 15 seconds. A cooldown from a recent automatic reset can delay this step. |
| Router startup | Restores power and waits through a 120-second boot countdown on the OLED. At the end, it disconnects the ESP32 from Wi-Fi to force a fresh connection on the next iteration. |
| First two retries in the same outage | Each becomes eligible 30 minutes after the previous automatic power-cycle routine finishes, once eight failed checks have accumulated again. |
| Further retries in the same outage | Each becomes eligible after a two-hour cooldown, with the same failed-check threshold. |

The firmware keeps checking during the cooldown and reports the remaining time as `Reset blocked` once the failure threshold is met. A successful probe clears the per-outage retry count and restores the short cooldown policy. The timestamp of the previous automatic reset still applies. There is no retry limit during a continuous outage. For example, an expired SIM data plan can keep the monitor offline even after the router reboots.

If the main loop exits or raises an exception, its cleanup sets the relay back to the router-power-on level.

## OLED and serial status

The optional 128×128 SH1107 OLED uses I²C address `0x3C`, with SDA on GPIO 4 and SCL on GPIO 5. Monitoring continues if the display is unavailable. Serial output mirrors status messages such as the current IP address, internet check counts, and reset activity. The boot countdown appears on the OLED.

A momentary button between GPIO 7 and ground toggles the panel. The firmware debounces the button and restores the latest status when the display wakes. The panel turns off automatically after five minutes on, while monitoring continues.

## Activity logs and time

The firmware writes startup, internet state transitions, automatic resets, time synchronization, and display power changes to `/activity.log`. When another entry would push the file over 64 KiB, it moves the current log to `/activity.old`, replaces the previous archive, and starts a new current log.

Serial messages and log entries use uptime in seconds until an NTP synchronization succeeds. The firmware attempts synchronization on successful internet checks until it succeeds. Subsequent timestamps use ISO 8601 with the configured timezone. Supported settings are UTC, Europe/Berlin with daylight-saving rules, and fixed offsets such as `+05:30`.

## Local web dashboard

Once Wi-Fi connects, open the ESP32's IP address in a browser on the local network. The dashboard runs over HTTP on port 80 and can be reached during a WAN outage as long as the local connection remains available.

- The homepage displays the value of `FIRMWARE_VERSION` from `main.py`.
- The log buttons show the current or previous activity log and refresh the selected view every 10 seconds. **Hide log** closes the view and stops its refresh. The raw logs are available at `/api/log` and `/api/log-old`.
- The **Power cycle router** button asks for confirmation and runs the same relay and boot-wait sequence as an automatic reset. Manual cycles bypass the automatic threshold and cooldown and do not update automatic reset counters. The browser may lose Wi-Fi before it receives the response.

The dashboard has no authentication. Any client that can reach it can read the logs and request a manual power cycle.
