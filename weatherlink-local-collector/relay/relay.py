#!/usr/bin/env python3
"""Forwards a WeatherLink Live's UDP real-time broadcast to one unicast address.

The device broadcasts to its own subnet, and a router does not forward
broadcasts. Run this on a host in the device's subnet to hand each packet to a
collector on another subnet. Only datagrams from --source are forwarded, and
the payload is passed on unchanged.
"""
import argparse
import socket
import sys
import time


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} weatherlink-relay: {message}", file=sys.stderr, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="IP of the WeatherLink Live; datagrams from others are dropped")
    parser.add_argument("--target", required=True, help="IP to forward each datagram to, by unicast")
    parser.add_argument("--port", type=int, default=22222)
    parser.add_argument("--device", help="interface to listen on, such as br4 (Linux only)")
    args = parser.parse_args()

    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if args.device:
        rx.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, args.device.encode())
    rx.bind(("", args.port))
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    log(f"forwarding UDP {args.port} from {args.source} to {args.target}")

    failing = False
    forwarded = 0
    while True:
        data, (sender, _port) = rx.recvfrom(65535)
        if sender != args.source:
            continue
        try:
            tx.sendto(data, (args.target, args.port))
        except OSError as exc:
            if not failing:
                failing = True
                log(f"cannot forward to {args.target}: {exc}")
            continue
        if failing:
            failing = False
            log("forwarding again")
        forwarded += 1
        if forwarded == 1:
            log(f"forwarding packets from {sender}")


if __name__ == "__main__":
    main()
