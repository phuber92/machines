"""Autonomous GitHub contribution updater for a continuously powered Pico 2 W."""

import os
import socket
import struct
import time

import config
import machine
import network
from github import GitHubClient
from updater import Updater


def connect():
    wlan = network.WLAN()
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(config.SSID, config.PASSWORD)
        for _ in range(20):
            if wlan.isconnected():
                break
            time.sleep(1)
        if not wlan.isconnected():
            wlan.active(False)
            raise OSError("Wi-Fi unavailable")


def sync_clock():
    address = socket.getaddrinfo(
        "time.cloudflare.com", 123, socket.AF_INET, socket.SOCK_DGRAM
    )[0][-1]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(10)
        sock.connect(address)
        request = bytearray(48)
        request[0] = 0x23
        nonce = os.urandom(8)
        request[40:48] = nonce
        sock.send(request)
        response = sock.recv(48)
        if (
            len(response) != 48
            or response[0] & 7 != 4
            or response[0] >> 6 == 3
            or not 1 <= response[1] <= 15
            or response[24:32] != nonce
        ):
            raise OSError("Invalid NTP response")
        seconds = struct.unpack("!I", response[40:44])[0] - 2_208_988_800
        if seconds < 0:  # NTP era rollover in 2036.
            seconds += 1 << 32
        year, month, day, hour, minute, second, weekday = time.gmtime(seconds)[:7]
        machine.RTC().datetime((year, month, day, weekday, hour, minute, second, 0))
    finally:
        sock.close()


def create_updater(cache_path="contributions.json"):
    username = getattr(config, "USERNAME", "phuber92")
    days = getattr(config, "WEEKS", 16) * 7
    client = GitHubClient(config.GITHUB_TOKEN, username, days)
    last_clock_attempt = int(time.time()) if time.gmtime()[0] >= 2024 else 0

    def fetch():
        nonlocal last_clock_attempt
        connect()
        now = int(time.time())
        valid_clock = time.gmtime()[0] >= 2024
        if (
            not valid_clock
            or now < last_clock_attempt
            or now - last_clock_attempt >= 86400
        ):
            last_clock_attempt = now
            try:
                sync_clock()
                last_clock_attempt = int(time.time())
            except OSError:
                if not valid_clock:
                    raise
                print("ghcontrib: clock resync failed; using RTC")
        return client.fetch()

    return Updater(fetch, username, days, cache_path)


def main():
    create_updater().run()


if __name__ == "__main__":
    main()
