import subprocess


def start():
    return subprocess.run(['true'], timeout=60)
