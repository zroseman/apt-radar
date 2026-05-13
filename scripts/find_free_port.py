#!/usr/bin/env python3
"""
Print the first free TCP port in [start, end), starting at 5055 by default.

Usage:
  find_free_port.py            # tries 5055..5099
  find_free_port.py 5060 5070  # tries 5060..5069

Exits non-zero if no port is free in the range.
The wizard calls this once during setup, saves the result to APT_RADAR_PORT
in .env, so the same port is used by every restart afterward.
"""

import socket
import sys


def find_free(start: int, end: int) -> int | None:
    for port in range(start, end):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            continue
        finally:
            s.close()
        return port
    return None


if __name__ == "__main__":
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 5055
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 5100
    port = find_free(start, end)
    if port is None:
        print(f"ERROR: no free port in {start}-{end - 1}", file=sys.stderr)
        sys.exit(1)
    print(port)
