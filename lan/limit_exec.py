#!/usr/bin/python3 -I
"""Music Lens LAN API v0.6 decoder helper: set resource limits in this process, then execv afconvert.

Usage: limit_exec.py SRC DST  (absolute paths; DST must not exist)
The threaded server never uses preexec_fn; it runs this helper with start_new_session=True.
"""
import os
import resource
import sys

AFCONVERT = "/usr/bin/afconvert"
CPU_SECONDS = 60
FSIZE_BYTES = 24 * 1024 * 1024
NOFILE = 32


def apply_limits():
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE_BYTES, FSIZE_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (NOFILE, NOFILE))


def command(src, dst):
    return [AFCONVERT, "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", src, dst]


def check_path(p):
    return isinstance(p, str) and os.path.isabs(p) and ".." not in p.split("/") and "\x00" not in p


def main(argv):
    if len(argv) != 3 or not check_path(argv[1]) or not check_path(argv[2]):
        sys.stderr.write("usage: limit_exec.py SRC DST (absolute)\n")
        return 2
    src, dst = argv[1], argv[2]
    if os.path.lexists(dst):
        sys.stderr.write("destination exists\n")
        return 2
    apply_limits()
    os.execv(AFCONVERT, command(src, dst))
    return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv))
