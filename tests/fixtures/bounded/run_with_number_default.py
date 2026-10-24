import subprocess


def start(timeout=60):
    return subprocess.run(['true'], timeout=timeout)
