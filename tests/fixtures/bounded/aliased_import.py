import subprocess as sp
from subprocess import run


def start():
    return run(['true']), sp.run(['true'])
