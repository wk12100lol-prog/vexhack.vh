#!/usr/bin/env python3
import sys, os, ctypes

myappid = "vexhack.vexarchive"
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except: pass

from gui import run_gui

if __name__ == "__main__":
    run_gui()
