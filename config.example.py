"""Example deployment settings for the ESP32-S3 internet monitor.

Copy this file to config.py and fill in your local network details before
running the project on the device.
"""

WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"

# Use "static" or "dhcp".
NETWORK_MODE = "static"

# Used only when NETWORK_MODE is "static". The address includes its CIDR mask.
STATIC_IP = "192.168.1.3/24"
STATIC_GATEWAY = "192.168.1.1"
STATIC_DNS = "192.168.1.2"

# Supported values:
#   "UTC"
#   "Europe/Berlin"  (CET/CEST with automatic EU daylight-saving changes)
#   A fixed offset such as "+05:30" or "-04:00"
TIMEZONE = "+02:00"
