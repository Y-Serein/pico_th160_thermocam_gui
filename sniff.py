#!/usr/bin/env python3
"""Diagnose the frame stream on /dev/ttyACM{0,1}.

v2: sends the 0x11 command (UBM11 + UOM_CALI_U8 per baud_get2() in main.c)
to force the firmware into CALI_U8 @ 2Mbaud, then reads a clean window.

Also suppresses DTR so opening the port does not bounce the MCU via the
CH346 bridge's DTR line.
"""
import sys
import time
import collections

import serial

DEVS = ('/dev/ttyACM0', '/dev/ttyACM1')
BAUD = 2000000
READ_BYTES = 60000
TIMEOUT_S = 3.0
FRAME_SIZE = 19231


def open_no_dtr(dev):
    s = serial.Serial()
    s.port = dev
    s.baudrate = BAUD
    s.timeout = TIMEOUT_S
    s.dtr = False
    s.rts = False
    s.open()
    return s


def find_all(data, pattern):
    pos, start = [], 0
    while True:
        i = data.find(pattern, start)
        if i < 0:
            break
        pos.append(i)
        start = i + 1
    return pos


def sniff(dev):
    print(f'=== {dev} ===')
    try:
        s = open_no_dtr(dev)
    except Exception as e:
        print(f'  open failed: {e}')
        return
    try:
        # purge any stale bytes
        s.reset_input_buffer()
        # request CALI_U8 @ 2Mbaud
        s.write(bytes([0x11]))
        s.flush()
        time.sleep(0.8)
        s.reset_input_buffer()
        time.sleep(0.2)
        t0 = time.time()
        d = s.read(READ_BYTES)
        dt = time.time() - t0
    finally:
        s.close()

    if not d:
        print(f'  NO DATA in {dt:.1f}s')
        return

    print(f'  got {len(d)} bytes in {dt:.2f}s  ({len(d)/max(dt,1e-3)/1024:.0f} KB/s)')
    print(f'  head: {d[:48].hex()}')

    ff_pos = find_all(d, b'\xff')
    print(f'  0xFF count: {len(ff_pos)}')
    if len(ff_pos) >= 2:
        diffs = [ff_pos[i+1] - ff_pos[i] for i in range(len(ff_pos)-1)]
        print(f'  0xFF gaps (first 12): {diffs[:12]}')
        from collections import Counter
        gc = Counter(diffs)
        print(f'  most-common gaps: {gc.most_common(5)}')
        if any(abs(g - FRAME_SIZE) <= 2 for g in diffs):
            exact = sum(1 for g in diffs if abs(g - FRAME_SIZE) <= 2)
            print(f'  >>> CALI_U8 candidate: {exact} gap(s) near {FRAME_SIZE}')

    ffff = find_all(d, b'\xff\xff')
    print(f'  FFFF count: {len(ffff)}')
    if ffff:
        ffff_diffs = [ffff[i+1] - ffff[i] for i in range(len(ffff)-1)]
        print(f'  FFFF gaps (first 8): {ffff_diffs[:8]}')

    pr = sum(1 for b in d[:256] if 0x20 <= b < 0x7f or b in (0x0a, 0x0d, 0x09))
    print(f'  ASCII ratio (first 256B): {pr/256:.0%}')
    if pr / 256 > 0.8:
        print(f'  text: {d[:200].decode("ascii", errors="replace")!r}')

    hist = collections.Counter(d)
    top = hist.most_common(6)
    print(f'  top bytes: {[(hex(b), n) for b,n in top]}')


def main():
    for dev in DEVS:
        sniff(dev)
        print()


if __name__ == '__main__':
    main()
