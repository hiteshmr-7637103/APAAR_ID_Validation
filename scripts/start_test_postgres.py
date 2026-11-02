#!/usr/bin/env python3
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PGDATA = Path("/tmp/pgdata")
PG_BIN = Path(
    "/home/kishorveeraragavan/Documents/APAAR_ID_Validation/backend/.venv/lib/python3.11/site-packages/pgserver/pginstall/bin"
)

os.environ["XDG_RUNTIME_DIR"] = "/tmp/runtime-1000"
os.environ["TMPDIR"] = "/tmp"

if not (PGDATA / "PG_VERSION").exists():
    PGDATA.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(PG_BIN / "initdb"),
            "--auth=trust",
            "--auth-local=trust",
            "--encoding=utf8",
            "-U",
            "postgres",
            "-D",
            str(PGDATA),
        ],
        check=True,
    )
    with open(PGDATA / "postgresql.conf", "a") as f:
        f.write("\nport = 55432\nlisten_addresses = '127.0.0.1'\nfsync = off\n")

# Run postgres in foreground so this daemon process keeps it running
proc = subprocess.Popen(
    [
        str(PG_BIN / "postgres"),
        "-D",
        str(PGDATA),
        "-k",
        str(PGDATA),
        "-p",
        "55432",
        "-h",
        "127.0.0.1",
    ]
)


def _term(*args):
    proc.terminate()
    sys.exit(0)


signal.signal(signal.SIGTERM, _term)
signal.signal(signal.SIGINT, _term)

# Wait until postgres is ready
for _ in range(30):
    time.sleep(0.5)
    res = subprocess.run(
        [str(PG_BIN / "pg_isready"), "-h", "127.0.0.1", "-p", "55432"], check=False
    )
    if res.returncode == 0:
        break

# Create databases if needed
subprocess.run(
    [
        str(PG_BIN / "createdb"),
        "-h",
        "127.0.0.1",
        "-p",
        "55432",
        "-U",
        "postgres",
        "apaar",
    ],
    check=False,
)
subprocess.run(
    [
        str(PG_BIN / "createdb"),
        "-h",
        "127.0.0.1",
        "-p",
        "55432",
        "-U",
        "postgres",
        "apaar_test",
    ],
    check=False,
)
print("POSTGRES DAEMON READY ON 127.0.0.1:55432", flush=True)

proc.wait()
