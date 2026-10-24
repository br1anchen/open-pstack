import subprocess


def start(**kwargs):
    return subprocess.run(['true'], **kwargs)
