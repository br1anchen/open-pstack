import subprocess


def start():
    return subprocess.Popen(['true'])  # bounded: the caller kills it
