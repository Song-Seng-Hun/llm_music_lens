#!/usr/bin/python3 -I
"""Music Lens LAN API v0.6 helper: set resource limits in this process, then execv a fixed command.

Usage: limit_exec.py SRC DST            afconvert to WAVE 16 kHz mono s16 (analyze)
       limit_exec.py --listen SRC DST   afconvert to WAVE 44.1 kHz mono s16 (ear check, for listening)
       limit_exec.py --earcheck PYTHON SCRIPT RESULT SRC START END OUT LAYERS
                                        PYTHON -I SCRIPT RESULT SRC START END OUT LAYERS (ear check synth)
       limit_exec.py --roll PYTHON SCRIPT RESULT SRC PAGE OUT
                                        PYTHON -I SCRIPT RESULT SRC PAGE OUT (sheet PNG page, roll.py)
       limit_exec.py --roll-marks PYTHON SCRIPT RESULT SRC PAGE OUT MARKS
                                        PYTHON -I SCRIPT RESULT SRC PAGE OUT MARKS (page with marks + JSON)
       limit_exec.py --roll-poster PYTHON SCRIPT RESULT SRC START END OUT TITLE CREDIT
                                        PYTHON -I SCRIPT --poster RESULT SRC START END OUT TITLE CREDIT
Paths are absolute; DST/OUT must not exist. Every argv is fixed; no free-form options.
The threaded server never uses preexec_fn; it runs this helper with start_new_session=True.
"""
import os
import re
import resource
import sys

AFCONVERT = "/usr/bin/afconvert"
CPU_SECONDS = 60
FSIZE_BYTES = 24 * 1024 * 1024
LISTEN_FSIZE_BYTES = 56 * 1024 * 1024  # 600 s at 44.1 kHz mono s16 is 50.5 MiB
EARCHECK_FSIZE_BYTES = 16 * 1024 * 1024  # 30 s stereo s16 at 44.1 kHz is 5.05 MiB
NOFILE = 32
EARCHECK_NOFILE = 64  # numpy's import opens more files than afconvert
RE_SECONDS = re.compile(r"\A(0|[1-9][0-9]{0,5})(\.[0-9]{1,6})?\Z")
ROLL_FSIZE_BYTES = 8 * 1024 * 1024  # one page PNG is about 0.5 MB
RE_PAGE = re.compile(r"\A[1-9][0-9]{0,3}\Z")
TEXT_MAX = 80  # roll.py TEXT_MAX: title and credit, printable characters only
RE_LAYERS = re.compile(r"\A[a-z]{1,8}(,[a-z]{1,8}){0,5}\Z")


def apply_limits(fsize=FSIZE_BYTES, nofile=NOFILE):
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))


def command(src, dst, listen=False):
    return [AFCONVERT, "-f", "WAVE", "-d", "LEI16@44100" if listen else "LEI16@16000", "-c", "1", src, dst]


def earcheck_command(python, script, result, src, start, end, out, layers):
    return [python, "-I", script, result, src, start, end, out, layers]


def check_path(p):
    return isinstance(p, str) and os.path.isabs(p) and ".." not in p.split("/") and "\x00" not in p


def roll_command(argv):
    """None: not a roll mode; (): a roll mode with bad arguments; else (child argv, outputs)."""
    modes = {"--roll": 8, "--roll-marks": 9, "--roll-poster": 11}
    if len(argv) < 2 or argv[1] not in modes:
        return None
    if len(argv) != modes[argv[1]]:
        return ()
    python, script, result, src = argv[2:6]
    if argv[1] == "--roll-poster":
        start, end, out, title, credit = argv[6:]
        ok = (RE_SECONDS.match(start) and RE_SECONDS.match(end) and check_path(out)
              and all(len(t) <= TEXT_MAX and t.isprintable() for t in (title, credit)))
        child, outs = [python, "-I", script, "--poster", result, src, start, end, out, title, credit], [out]
    else:
        outs = argv[7:]
        ok = RE_PAGE.match(argv[6]) and all(check_path(p) for p in outs)
        child = [python, "-I", script, result, src, argv[6]] + outs
    if not ok or not all(check_path(p) for p in (python, script, result, src)):
        return ()
    return child, outs


def main(argv):
    roll = roll_command(argv)
    if roll is not None:
        if not roll:
            sys.stderr.write("usage: limit_exec.py --roll | --roll-marks | --roll-poster (see the module docstring)\n")
            return 2
        if any(os.path.lexists(p) for p in roll[1]):
            sys.stderr.write("destination exists\n")
            return 2
        apply_limits(ROLL_FSIZE_BYTES, EARCHECK_NOFILE)  # matplotlib fits in 64 as well
        os.execv(roll[0][0], roll[0])
        return 127
    if len(argv) == 10 and argv[1] == "--earcheck":
        python, script, result, src, start, end, out, layers = argv[2:]
        if (not all(check_path(p) for p in (python, script, result, src, out)) or not RE_SECONDS.match(start)
                or not RE_SECONDS.match(end) or not RE_LAYERS.match(layers)):
            sys.stderr.write("usage: limit_exec.py --earcheck PYTHON SCRIPT RESULT SRC START END OUT LAYERS\n")
            return 2
        if os.path.lexists(out):
            sys.stderr.write("destination exists\n")
            return 2
        apply_limits(EARCHECK_FSIZE_BYTES, EARCHECK_NOFILE)
        os.execv(python, earcheck_command(python, script, result, src, start, end, out, layers))
        return 127
    listen = len(argv) == 4 and argv[1] == "--listen"
    args = argv[2:] if listen else argv[1:]
    if len(args) != 2 or not check_path(args[0]) or not check_path(args[1]):
        sys.stderr.write("usage: limit_exec.py [--listen] SRC DST (absolute)\n")
        return 2
    src, dst = args
    if os.path.lexists(dst):
        sys.stderr.write("destination exists\n")
        return 2
    apply_limits(LISTEN_FSIZE_BYTES if listen else FSIZE_BYTES)
    os.execv(AFCONVERT, command(src, dst, listen))
    return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv))
