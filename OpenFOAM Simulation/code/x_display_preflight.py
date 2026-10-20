"""Check X connectivity before starting ParaView; exit 75 only on startup failure."""
import ctypes
import ctypes.util
import os
import sys
import time


def wait_for_display(x11, attempts=10, pause=time.sleep):
    for _ in range(attempts):
        display=x11.XOpenDisplay(None)
        if display:
            x11.XCloseDisplay(display)
            return True
        pause(0.5)
    return False


def main():
    x11=ctypes.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
    x11.XOpenDisplay.argtypes=[ctypes.c_char_p]
    x11.XOpenDisplay.restype=ctypes.c_void_p
    x11.XCloseDisplay.argtypes=[ctypes.c_void_p]
    x11.XCloseDisplay.restype=ctypes.c_int
    if not wait_for_display(x11):
        print('X display preflight failed: '+os.environ.get('DISPLAY','unset'),flush=True)
        return 75
    print('X display preflight OK: '+os.environ.get('DISPLAY','unset'),flush=True)
    os.execvpe(sys.argv[1],sys.argv[1:],os.environ)


if __name__=='__main__':
    sys.exit(main())
