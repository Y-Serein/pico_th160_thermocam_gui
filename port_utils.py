"""Serial port discovery helper (cross-platform via pyserial)."""
import serial
from serial.tools import list_ports


def list_serial_ports():
    return [p.device for p in list_ports.comports()]


def probe_active_port(ports, timeout=0.2, skip=None):
    """Return the first port that currently has bytes arriving (= frame stream).

    Tries each candidate port at 115200 with a short timeout and reads 1 byte;
    if that byte arrives, the port is 'active'. Ports in `skip` (e.g. held by
    an active MonitorWorker) are ignored. Returns None if nothing responds.
    """
    skip = set(skip or ())
    for port in ports:
        if port in skip:
            continue
        try:
            with serial.Serial(port, 115200, timeout=timeout) as ser:
                if ser.read(1):
                    return port
        except Exception:
            continue
    return None
