"""Shared .env loader. OS-level environment variables always win; the .env file only fills in
keys that aren't already set, so a system-env setup (setx/export) and a teammate's .env file both
work without one clobbering the other."""
import os


def load_env(path):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\r\n")
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1); k = k.strip(); v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        os.environ.setdefault(k, v)
