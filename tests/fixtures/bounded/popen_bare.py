import subprocess


def start():
    proc = subprocess.Popen(['true'])
    return proc.communicate()
