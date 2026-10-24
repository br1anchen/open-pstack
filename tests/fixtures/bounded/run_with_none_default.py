import subprocess


def start(timeout=None):
    return subprocess.run(['true'], timeout=timeout)
