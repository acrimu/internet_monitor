"""Wi-Fi/internet monitor with automatic router power cycling.

Copy main.py, config.py, and sh1107.py to the MicroPython device. Adjust
deployment settings in config.py before running main.py.
"""

from machine import Pin, I2C
import config
import network
import ntptime
import os
import socket
import time

try:
    import sh1107
except ImportError:
    sh1107 = None


# ----------------------------- CONFIGURATION -----------------------------
WIFI_SSID = config.WIFI_SSID
WIFI_PASSWORD = config.WIFI_PASSWORD
NETWORK_MODE = config.NETWORK_MODE
STATIC_IP = config.STATIC_IP
STATIC_GATEWAY = config.STATIC_GATEWAY
STATIC_DNS = config.STATIC_DNS
TIMEZONE = config.TIMEZONE

SCL_PIN = 5
SDA_PIN = 4
RELAY_PIN = 6                 # Change this to the GPIO wired to the relay.
DISPLAY_BUTTON_PIN = 7        # Momentary button between this pin and GND.
DISPLAY_TIMEOUT_S = 5 * 60

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

LOG_FILE = "/activity.log"
OLD_LOG_FILE = "/activity.old"
LOG_MAX_BYTES = 64 * 1024
LOG_HTTP_PORT = 80
# -------------------------------------------------------------------------


def validate_ipv4(value, setting_name):
    """Validate an IPv4 address and return its normalized text."""
    parts = value.split(".")
    if len(parts) != 4:
        raise ValueError("{} must be an IPv4 address".format(setting_name))
    normalized = []
    for part in parts:
        try:
            number = int(part)
        except ValueError:
            raise ValueError("{} must be an IPv4 address".format(setting_name))
        if number < 0 or number > 255:
            raise ValueError("{} must be an IPv4 address".format(setting_name))
        normalized.append(str(number))
    return ".".join(normalized)


def prefix_to_netmask(prefix):
    """Convert a CIDR prefix length to a dotted IPv4 netmask."""
    try:
        prefix = int(prefix)
    except ValueError:
        raise ValueError("STATIC_IP must include a CIDR mask such as /24")
    if prefix < 0 or prefix > 32:
        raise ValueError("STATIC_IP CIDR mask must be between /0 and /32")
    mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF if prefix else 0
    return "{}.{}.{}.{}".format(
        (mask >> 24) & 255,
        (mask >> 16) & 255,
        (mask >> 8) & 255,
        mask & 255,
    )


def configure_network_interface(interface):
    """Apply DHCP or validated static IPv4 settings from config.py."""
    mode = str(NETWORK_MODE).strip().lower()
    if mode == "static":
        address_parts = str(STATIC_IP).split("/")
        if len(address_parts) != 2:
            raise ValueError("STATIC_IP must look like 192.168.1.3/24")
        address = validate_ipv4(address_parts[0], "STATIC_IP")
        netmask = prefix_to_netmask(address_parts[1])
        gateway = validate_ipv4(str(STATIC_GATEWAY), "STATIC_GATEWAY")
        dns = validate_ipv4(str(STATIC_DNS), "STATIC_DNS")
        interface.ifconfig((address, netmask, gateway, dns))
    elif mode == "dhcp":
        # Reactivating the interface selects DHCP on ESP32. Newer MicroPython
        # versions also expose ipconfig(dhcp4=True), so use it when available.
        try:
            interface.ipconfig(dhcp4=True)
        except (AttributeError, TypeError, OSError):
            pass
    else:
        raise ValueError('NETWORK_MODE must be "static" or "dhcp"')


def days_in_month(year, month):
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    return 30 if month in (4, 6, 9, 11) else 31


def weekday_sunday_zero(year, month, day):
    """Return weekday using Sunday=0 without requiring a timezone database."""
    offsets = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
    adjusted_year = year - 1 if month < 3 else year
    return (
        adjusted_year
        + adjusted_year // 4
        - adjusted_year // 100
        + adjusted_year // 400
        + offsets[month - 1]
        + day
    ) % 7


def berlin_is_dst(utc_now):
    """Apply the EU rule: last Sunday of March through last Sunday of October."""
    year, month, day, hour = utc_now[0], utc_now[1], utc_now[2], utc_now[3]
    if 4 <= month <= 9:
        return True
    if month < 3 or month > 10:
        return False

    last_day = days_in_month(year, month)
    last_sunday = last_day - weekday_sunday_zero(year, month, last_day)
    if month == 3:
        return day > last_sunday or (day == last_sunday and hour >= 1)
    return day < last_sunday or (day == last_sunday and hour < 1)


def timezone_offset_minutes(utc_now):
    """Return configured offset minutes and its ISO-8601 suffix."""
    zone = str(TIMEZONE).strip()
    if zone.upper() == "UTC":
        return 0, "Z"
    if zone.lower() == "europe/berlin":
        if berlin_is_dst(utc_now):
            return 120, "+02:00"
        return 60, "+01:00"

    if len(zone) == 6 and zone[0] in ("+", "-") and zone[3] == ":":
        try:
            hours = int(zone[1:3])
            minutes = int(zone[4:6])
        except ValueError:
            hours = 99
            minutes = 99
        if hours <= 23 and minutes <= 59:
            offset = hours * 60 + minutes
            if zone[0] == "-":
                offset = -offset
            return offset, zone

    raise ValueError(
        'TIMEZONE must be "UTC", "Europe/Berlin", or an offset such as "+05:30"'
    )


def format_log_timestamp():
    """Format the synchronized UTC RTC using the configured timezone."""
    utc_now = time.gmtime()
    offset_minutes, suffix = timezone_offset_minutes(utc_now)
    local_now = time.gmtime(time.time() + offset_minutes * 60)
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}{}".format(
        local_now[0],
        local_now[1],
        local_now[2],
        local_now[3],
        local_now[4],
        local_now[5],
        suffix,
    )


relay = Pin(RELAY_PIN, Pin.OUT, value=ROUTER_POWER_ON_LEVEL)
wlan = network.WLAN(network.STA_IF)
wlan.active(False)
wlan.active(True)
configure_network_interface(wlan)
clock_is_synced = False
log_server = None
log_client = None
log_client_deadline_ms = 0
log_request_buffer = b""


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
display_enabled = oled is not None
display_deadline_ms = time.ticks_add(time.ticks_ms(), DISPLAY_TIMEOUT_S * 1000)
display_button_event = False
display_button_wait_release = False
display_button_release_ms = None
last_oled_title = "Monitor"
last_oled_lines = ()


def display_button_irq(pin):
    """Latch a button press; all real work stays outside interrupt context."""
    global display_button_event
    display_button_event = True


display_button = Pin(DISPLAY_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
display_button.irq(trigger=Pin.IRQ_FALLING, handler=display_button_irq)


def render_oled(title, *lines):
    """Render immediately without changing the saved display status."""
    if oled is None or not display_enabled:
        return
    oled.fill(0)
    oled.rect(0, 0, 128, 128, 1)
    oled.text(title[:16], 4, 6, 1)
    y = 24
    for line in lines[:6]:
        oled.text(str(line)[:16], 4, y, 1)
        y += 16
    oled.show()


def set_oled_power(enabled):
    """Power the OLED panel on or off using the available driver API."""
    global display_enabled, display_deadline_ms
    if oled is None or enabled == display_enabled:
        return
    try:
        if enabled:
            if hasattr(oled, "poweron"):
                oled.poweron()
            elif hasattr(oled, "sleep"):
                oled.sleep(False)
            else:
                oled.write_cmd(0xAF)
            display_enabled = True
            display_deadline_ms = time.ticks_add(
                time.ticks_ms(), DISPLAY_TIMEOUT_S * 1000
            )
            render_oled(last_oled_title, *last_oled_lines)
            log_activity("DISPLAY ON")
        else:
            if hasattr(oled, "poweroff"):
                oled.poweroff()
            elif hasattr(oled, "sleep"):
                oled.sleep(True)
            else:
                oled.write_cmd(0xAE)
            display_enabled = False
            log_activity("DISPLAY OFF")
    except Exception as exc:
        print("OLED power control failed:", exc)


def handle_display_button():
    """Accept one toggle per press and enforce the display timeout."""
    global display_button_event, display_button_wait_release
    global display_button_release_ms
    now = time.ticks_ms()

    if display_button_wait_release:
        # Ignore contact bounce until the button has remained released HIGH
        # continuously for at least 50 ms.
        display_button_event = False
        if display_button.value():
            if display_button_release_ms is None:
                display_button_release_ms = now
            elif time.ticks_diff(now, display_button_release_ms) >= 50:
                display_button_wait_release = False
                display_button_release_ms = None
        else:
            display_button_release_ms = None
    elif display_button_event:
        display_button_event = False
        display_button_wait_release = True
        display_button_release_ms = None
        set_oled_power(not display_enabled)

    if display_enabled and time.ticks_diff(now, display_deadline_ms) >= 0:
        set_oled_power(False)


def show_on_oled(title, *lines):
    """Show status on the optional OLED without writing to serial."""
    global last_oled_title, last_oled_lines
    last_oled_title = title
    last_oled_lines = lines
    try:
        render_oled(title, *lines)
    except Exception as exc:
        print("OLED update failed:", exc)


def show_status(title, *lines):
    """Print status and mirror it to the optional OLED."""
    message = " | ".join((title,) + lines)
    print(message)
    show_on_oled(title, *lines)


def log_activity(message):
    """Append a configured-timezone or uptime event and rotate at 64 KB."""
    try:
        if clock_is_synced:
            timestamp = format_log_timestamp()
        else:
            timestamp = "UPTIME:{:010d}".format(time.ticks_ms() // 1000)
        entry = "{} {}\n".format(timestamp, message)

        try:
            log_size = os.stat(LOG_FILE)[6]
        except OSError:
            log_size = 0

        if log_size + len(entry) > LOG_MAX_BYTES:
            try:
                os.remove(OLD_LOG_FILE)
            except OSError:
                pass
            os.rename(LOG_FILE, OLD_LOG_FILE)

        with open(LOG_FILE, "a") as log:
            log.write(entry)
    except OSError as exc:
        print("Log write failed:", exc)


def synchronize_time():
    """Set the RTC to UTC using NTP; return True when successful."""
    global clock_is_synced
    try:
        # Validate the configured zone independently of the NTP operation.
        timezone_offset_minutes(time.gmtime())
        ntptime.settime()
        clock_is_synced = True
        log_activity("TIME SYNCED timezone={}".format(TIMEZONE))
        return True
    except Exception as exc:
        clock_is_synced = False
        print("Time sync/configuration failed:", exc)
        return False


def send_all(client, data):
    """Send all bytes on a MicroPython socket."""
    sent_total = 0
    while sent_total < len(data):
        sent = client.send(data[sent_total:])
        if not sent:
            raise OSError("socket closed while sending")
        sent_total += sent


def send_http_response(client, status, content_type, content):
    """Send a complete in-memory HTTP response."""
    header = (
        "HTTP/1.1 {}\r\n"
        "Content-Type: {}\r\n"
        "Content-Length: {}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    ).format(status, content_type, len(content))
    send_all(client, header.encode())
    send_all(client, content)


def send_log_file(client, filename):
    """Stream a log file without loading all 64 KB into RAM."""
    try:
        file_size = os.stat(filename)[6]
    except OSError:
        send_http_response(client, "404 Not Found", "text/plain", b"Log not found\n")
        return

    header = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "Content-Length: {}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    ).format(file_size)
    send_all(client, header.encode())
    with open(filename, "rb") as log:
        while True:
            chunk = log.read(512)
            if not chunk:
                break
            send_all(client, chunk)


def start_log_server():
    """Start the non-blocking LAN log server once Wi-Fi is connected."""
    global log_server
    if log_server is not None:
        return
    server = None
    try:
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", LOG_HTTP_PORT))
        server.listen(1)
        server.setblocking(False)
        log_server = server
        print("Log server: http://{}:{}/".format(wlan.ifconfig()[0], LOG_HTTP_PORT))
    except OSError as exc:
        if server is not None:
            server.close()
        print("Log server start failed:", exc)


def serve_log_request():
    """Advance one pending browser request without blocking the monitor."""
    global log_client, log_client_deadline_ms, log_request_buffer
    if log_server is None:
        return

    if log_client is None:
        try:
            log_client, _ = log_server.accept()
            log_client.setblocking(False)
            log_client_deadline_ms = time.ticks_add(time.ticks_ms(), 3000)
            log_request_buffer = b""
        except OSError:
            return

    try:
        chunk = log_client.recv(512)
        if not chunk:
            log_client.close()
            log_client = None
            log_request_buffer = b""
            return
        log_request_buffer += chunk
    except OSError:
        if time.ticks_diff(time.ticks_ms(), log_client_deadline_ms) >= 0:
            log_client.close()
            log_client = None
            log_request_buffer = b""
        return

    if b"\n" not in log_request_buffer and len(log_request_buffer) < 1024:
        return

    client = log_client
    try:
        client.settimeout(2)
        request_line = log_request_buffer.split(b"\n", 1)[0].rstrip(b"\r")
        parts = request_line.split()
        path = parts[1].split(b"?", 1)[0] if len(parts) >= 2 else b""

        if path == b"/log":
            send_log_file(client, LOG_FILE)
        elif path == b"/log-old":
            send_log_file(client, OLD_LOG_FILE)
        elif path == b"/":
            page = (
                b"<!doctype html><meta name='viewport' content='width=device-width'>"
                b"<title>ESP32 Internet Monitor</title>"
                b"<h1>ESP32 Internet Monitor</h1>"
                b"<p><a href='/log'>Current activity log</a></p>"
                b"<p><a href='/log-old'>Previous activity log</a></p>"
            )
            send_http_response(client, "200 OK", "text/html; charset=utf-8", page)
        else:
            send_http_response(client, "404 Not Found", "text/plain", b"Not found\n")
    except OSError as exc:
        print("Log server request failed:", exc)
    finally:
        client.close()
        log_client = None
        log_request_buffer = b""


def responsive_wait(seconds):
    """Wait while continuing to handle browser log requests."""
    started = time.ticks_ms()
    duration_ms = seconds * 1000
    while time.ticks_diff(time.ticks_ms(), started) < duration_ms:
        handle_display_button()
        serve_log_request()
        time.sleep_ms(100)


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
    show_status("ROUTER RESET", "power on")
    for remaining_s in range(ROUTER_BOOT_WAIT_S, 0, -1):
        show_on_oled(
            "ROUTER RESET",
            "power on",
            "wait {} sec".format(remaining_s),
        )
        handle_display_button()
        serve_log_request()
        time.sleep(1)

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
    previous_online = None
    time_synced = False
    show_status("Monitor start", "router power ON")
    log_activity("MONITOR START router_power=ON")

    while True:
        connected = connect_wifi()
        if connected:
            start_log_server()
            serve_log_request()
        online = connected and internet_is_available()

        if online:
            failed_checks = 0
            internet_ok_count += 1
            if not time_synced:
                time_synced = synchronize_time()
            if previous_online is not True:
                log_activity(
                    "INTERNET UP ip={} ok={} failed={}".format(
                        wlan.ifconfig()[0],
                        internet_ok_count,
                        internet_fail_count,
                    )
                )
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
            if previous_online is not False:
                log_activity(
                    "INTERNET DOWN consecutive={} failed={}".format(
                        failed_checks,
                        internet_fail_count,
                    )
                )
            show_status(
                "Internet DOWN",
                "failure {}/{}".format(failed_checks, FAILED_CHECKS_BEFORE_RESET),
                "OK: {}".format(internet_ok_count),
                "Failed: {}".format(internet_fail_count),
                "Resets: {}".format(router_reset_count),
            )

        previous_online = online

        if failed_checks >= FAILED_CHECKS_BEFORE_RESET:
            now = time.ticks_ms()
            cooldown_over = (
                last_reset_ms is None
                or time.ticks_diff(now, last_reset_ms) >= RESET_COOLDOWN_S * 1000
            )
            if cooldown_over:
                router_reset_count += 1
                log_activity(
                    "ROUTER RESET count={} failed={}".format(
                        router_reset_count,
                        internet_fail_count,
                    )
                )
                power_cycle_router()
                last_reset_ms = time.ticks_ms()
                failed_checks = 0
            else:
                remaining_ms = RESET_COOLDOWN_S * 1000 - time.ticks_diff(now, last_reset_ms)
                show_status("Reset blocked", "cooldown {} sec".format(remaining_ms // 1000))

        responsive_wait(CHECK_INTERVAL_S)


try:
    main()
finally:
    # A crash or Ctrl-C should leave router power enabled.
    relay.value(ROUTER_POWER_ON_LEVEL)

