"""Wi-Fi/internet monitor with automatic router power cycling.

Copy this file and sh1107.py to the MicroPython device.  Adjust the values in
the CONFIGURATION section before running it as main.py.
"""

from machine import Pin, I2C
import network
import socket
import time

try:
    import sh1107
except ImportError:
    sh1107 = None


# ----------------------------- CONFIGURATION -----------------------------
WIFI_SSID = "ac2478ch"
WIFI_PASSWORD = "18012008Ac"

SCL_PIN = 5
SDA_PIN = 4
RELAY_PIN = 6                 # Change this to the GPIO wired to the relay.

# Set these for your relay/wiring. For many active-low relay modules, router
# power ON is 0 and power OFF is 1. Verify this before connecting the router.
ROUTER_POWER_ON_LEVEL = 0
ROUTER_POWER_OFF_LEVEL = 1

CHECK_INTERVAL_S = 15
FAILED_CHECKS_BEFORE_RESET = 4   # 4 * 15 seconds = about 1 minute offline.
ROUTER_POWER_OFF_S = 10
ROUTER_BOOT_WAIT_S = 120
WIFI_CONNECT_TIMEOUT_S = 25
RESET_COOLDOWN_S = 10 * 60       # Never power-cycle more than once per 10 min.

# Numeric addresses avoid relying on DNS. A successful TCP connection to any
# target counts as internet access. Port 443 is generally firewall-friendly.
CHECK_TARGETS = (
    ("1.1.1.1", 443),
    ("8.8.8.8", 443),
)
SOCKET_TIMEOUT_S = 4
# -------------------------------------------------------------------------


relay = Pin(RELAY_PIN, Pin.OUT, value=ROUTER_POWER_ON_LEVEL)
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.ifconfig((
    "192.168.1.3",
    "255.255.255.0",
    "192.168.1.1",
    "192.168.1.1",
))


def create_display():
    if sh1107 is None:
        return None
    try:
        i2c = I2C(0, scl=Pin(SCL_PIN), sda=Pin(SDA_PIN), freq=400000)
        return sh1107.SH1107_I2C(128, 128, i2c, address=0x3C, rotate=0)
    except Exception as exc:
        print("OLED unavailable:", exc)
        return None


oled = create_display()


def show_status(title, *lines):
    """Print status and mirror it to the optional OLED."""
    message = " | ".join((title,) + lines)
    print(message)
    if oled is None:
        return
    try:
        oled.fill(0)
        oled.rect(0, 0, 128, 128, 1)
        oled.text(title[:16], 4, 6, 1)
        y = 24
        for line in lines[:6]:
            oled.text(str(line)[:16], 4, y, 1)
            y += 16
        oled.show()
    except Exception as exc:
        print("OLED update failed:", exc)


def connect_wifi():
    """Connect to the configured access point within a fixed timeout."""
    if wlan.isconnected():
        return True

    show_status("WiFi", "connecting...")
    wlan.disconnect()
    time.sleep_ms(250)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    started = time.ticks_ms()
    timeout_ms = WIFI_CONNECT_TIMEOUT_S * 1000
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), started) >= timeout_ms:
            show_status("WiFi failed", "status: {}".format(wlan.status()))
            return False
        time.sleep_ms(500)

    ip_address = wlan.ifconfig()[0]
    show_status("WiFi connected", ip_address)
    return True


def internet_is_available():
    """Return True when at least one external TCP endpoint is reachable."""
    if not wlan.isconnected():
        return False

    for address in CHECK_TARGETS:
        sock = None
        try:
            sock = socket.socket()
            sock.settimeout(SOCKET_TIMEOUT_S)
            sock.connect(address)
            return True
        except OSError:
            pass
        finally:
            if sock is not None:
                sock.close()
    return False


def power_cycle_router():
    """Turn router power off, restore it, then allow time for it to boot."""
    show_status("ROUTER RESET", "power off")
    relay.value(ROUTER_POWER_OFF_LEVEL)
    time.sleep(ROUTER_POWER_OFF_S)

    relay.value(ROUTER_POWER_ON_LEVEL)
    show_status("ROUTER RESET", "power on", "wait {} sec".format(ROUTER_BOOT_WAIT_S))
    time.sleep(ROUTER_BOOT_WAIT_S)

    # Force a fresh association after the access point returns.
    wlan.disconnect()
    time.sleep(1)


def main():
    if WIFI_SSID == "YOUR_WIFI_NAME":
        show_status("SETUP NEEDED", "edit WiFi config")
        raise ValueError("Set WIFI_SSID and WIFI_PASSWORD in main.py")

    failed_checks = 0
    internet_ok_count = 0
    internet_fail_count = 0
    router_reset_count = 0
    last_reset_ms = None
    show_status("Monitor start", "router power ON")

    while True:
        connected = connect_wifi()
        online = connected and internet_is_available()

        if online:
            failed_checks = 0
            internet_ok_count += 1
            show_status(
                "Internet OK",
                wlan.ifconfig()[0],
                "OK: {}".format(internet_ok_count),
                "Failed: {}".format(internet_fail_count),
                "Resets: {}".format(router_reset_count),
            )
        else:
            failed_checks += 1
            internet_fail_count += 1
            show_status(
                "Internet DOWN",
                "failure {}/{}".format(failed_checks, FAILED_CHECKS_BEFORE_RESET),
                "OK: {}".format(internet_ok_count),
                "Failed: {}".format(internet_fail_count),
                "Resets: {}".format(router_reset_count),
            )

        if failed_checks >= FAILED_CHECKS_BEFORE_RESET:
            now = time.ticks_ms()
            cooldown_over = (
                last_reset_ms is None
                or time.ticks_diff(now, last_reset_ms) >= RESET_COOLDOWN_S * 1000
            )
            if cooldown_over:
                router_reset_count += 1
                power_cycle_router()
                last_reset_ms = time.ticks_ms()
                failed_checks = 0
            else:
                remaining_ms = RESET_COOLDOWN_S * 1000 - time.ticks_diff(now, last_reset_ms)
                show_status("Reset blocked", "cooldown {} sec".format(remaining_ms // 1000))

        time.sleep(CHECK_INTERVAL_S)


try:
    main()
finally:
    # A crash or Ctrl-C should leave router power enabled.
    relay.value(ROUTER_POWER_ON_LEVEL)

