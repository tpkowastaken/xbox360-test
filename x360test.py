#!/usr/bin/env python3
"""Xbox 360 (XInput-over-USB) controller driver/state tracker for macOS via libusb.

Used by game.py. Reads the pad directly over USB, so no kernel driver is needed.
"""
import math, statistics, threading, time
from collections import deque

import usb.core, usb.util

# Known wired Xbox 360 / XInput-compatible VID:PIDs (first match wins; falls back to class scan)
KNOWN = [(0x24c6, 0xfafd), (0x045e, 0x028e), (0x045e, 0x028f)]

BUTTONS = ["DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "START", "BACK", "LS", "RS",
           "LB", "RB", "GUIDE", None, "A", "B", "X", "Y"]
CHATTER_MS = 25      # press->release->press faster than this is flagged as chatter/bounce
ANGLE_BINS = 72      # 5 deg per bin for circularity


def blank_stick():
    return {"minx": 0, "maxx": 0, "miny": 0, "maxy": 0,
            "rad": [0.0] * ANGLE_BINS,  # max normalized radius seen per angle bin
            "center": None}


class Tester:
    def __init__(self):
        self.lock = threading.Lock()
        self.dev = None
        self.connected = False
        self.info = {}
        self.disconnects = 0
        self.raw = ""
        self.out_q = deque()
        self.reset()

    def reset(self):
        with getattr(self, "lock", threading.Lock()):
            self.buttons = {b: False for b in BUTTONS if b}
            self.press_count = {b: 0 for b in BUTTONS if b}
            self.chatter = {b: 0 for b in BUTTONS if b}
            self.last_change = {b: 0.0 for b in BUTTONS if b}
            self.last_release = {b: 0.0 for b in BUTTONS if b}
            self.lt = self.rt = 0
            self.trig = {"LT": {"min": 255, "max": 0, "seen": set()},
                         "RT": {"min": 255, "max": 0, "seen": set()}}
            self.sticks = {"L": [0, 0], "R": [0, 0]}
            self.stick_stats = {"L": blank_stick(), "R": blank_stick()}
            self.intervals = deque(maxlen=2000)
            self.last_report_t = None
            self.reports = 0
            self.bad_reports = 0
            self.dpad_combos = set()
            self.center_capture = None  # {"until": t, "L": [], "R": []}

    # ---------- USB ----------
    def find(self):
        for vid, pid in KNOWN:
            d = usb.core.find(idVendor=vid, idProduct=pid)
            if d:
                return d
        for d in usb.core.find(find_all=True):
            try:
                for intf in d.get_active_configuration():
                    if intf.bInterfaceClass == 0xFF and intf.bInterfaceSubClass == 0x5D and intf.bInterfaceProtocol == 0x01:
                        return d
            except Exception:
                pass
        return None

    def connect(self):
        d = self.find()
        if not d:
            return False
        try:
            d.set_configuration()
        except usb.core.USBError:
            pass
        try:
            usb.util.claim_interface(d, 0)
        except usb.core.USBError:
            pass

        def s(i):
            try:
                return usb.util.get_string(d, i) if i else ""
            except Exception:
                return ""
        speed = {1: "Low (1.5 Mb/s)", 2: "Full (12 Mb/s)", 3: "High (480 Mb/s)"}.get(d.speed, str(d.speed))
        ep_in = usb.util.find_descriptor(d.get_active_configuration()[(0, 0)], bEndpointAddress=0x81)
        self.info = {"vid": f"{d.idVendor:04x}", "pid": f"{d.idProduct:04x}",
                     "manufacturer": s(d.iManufacturer), "product": s(d.iProduct),
                     "serial": s(d.iSerialNumber), "bcdDevice": f"{d.bcdDevice >> 8}.{d.bcdDevice & 0xff:02x}",
                     "speed": speed, "maxPower_mA": d.get_active_configuration().bMaxPower * 2,
                     "bInterval_ms": ep_in.bInterval if ep_in else None}
        self.dev = d
        self.connected = True
        self._rumble = None
        self.set_led(6)  # player 1
        return True

    def send(self, data):
        self.out_q.append(bytes(data))

    def set_led(self, pattern):
        self.send([0x01, 0x03, pattern & 0x0F])

    def rumble(self, left, right, force=False):
        # Only send on change: flooding the pad with identical commands is pointless and
        # the motor state persists until the next command anyway.
        val = (max(0, min(255, int(left))), max(0, min(255, int(right))))
        if val == getattr(self, "_rumble", None) and not force:
            return
        self._rumble = val
        self.send([0x00, 0x08, 0x00, val[0], val[1], 0, 0, 0])

    def loop(self):
        while True:
            if not self.connected:
                if not self.connect():
                    time.sleep(0.5)
                    continue
            try:
                while self.out_q:
                    self.dev.write(0x02, self.out_q.popleft(), 100)
                try:
                    r = bytes(self.dev.read(0x81, 32, 5))
                except usb.core.USBTimeoutError:
                    continue
                self.handle(r, time.perf_counter())
            except usb.core.USBError as e:
                if getattr(e, "errno", None) in (60, 110):  # timeout variants
                    continue
                self.connected = False
                self.disconnects += 1
                try:
                    usb.util.dispose_resources(self.dev)
                except Exception:
                    pass
                self.dev = None
                time.sleep(0.5)

    # ---------- parsing ----------
    def handle(self, r, t):
        if len(r) < 2 or r[0] != 0x00:
            return  # status / LED / headset messages
        if r[1] != 0x14 or len(r) < 20:
            self.bad_reports += 1
            return
        self.raw = r.hex(" ")
        with self.lock:
            self.reports += 1
            if self.last_report_t is not None:
                self.intervals.append((t - self.last_report_t) * 1000)
            self.last_report_t = t
            bits = r[2] | (r[3] << 8)
            for i, name in enumerate(BUTTONS):
                if not name:
                    continue
                down = bool(bits & (1 << i))
                if down != self.buttons[name]:
                    if down:
                        self.press_count[name] += 1
                        if self.last_release[name] and (t - self.last_release[name]) * 1000 < CHATTER_MS:
                            self.chatter[name] += 1
                    else:
                        self.last_release[name] = t
                    self.buttons[name] = down
            dp = tuple(n for n in BUTTONS[:4] if self.buttons[n])
            if dp:
                self.dpad_combos.add("+".join(x[5:] for x in dp))
            self.lt, self.rt = r[4], r[5]
            for k, v in (("LT", r[4]), ("RT", r[5])):
                tr = self.trig[k]
                tr["min"] = min(tr["min"], v); tr["max"] = max(tr["max"], v); tr["seen"].add(v)
            lx, ly, rx, ry = (int.from_bytes(r[i:i + 2], "little", signed=True) for i in (6, 8, 10, 12))
            for k, (x, y) in (("L", (lx, ly)), ("R", (rx, ry))):
                self.sticks[k] = [x, y]
                st = self.stick_stats[k]
                st["minx"] = min(st["minx"], x); st["maxx"] = max(st["maxx"], x)
                st["miny"] = min(st["miny"], y); st["maxy"] = max(st["maxy"], y)
                nx, ny = x / 32768, y / 32768
                rad = math.hypot(nx, ny)
                if rad > 0.3:
                    b = int((math.degrees(math.atan2(ny, nx)) % 360) / (360 / ANGLE_BINS)) % ANGLE_BINS
                    st["rad"][b] = max(st["rad"][b], rad)
                cc = self.center_capture
                if cc and t < cc["until"]:
                    cc[k].append((x, y))

    def start_center(self, secs=3.0):
        with self.lock:
            self.center_capture = {"until": time.perf_counter() + secs, "L": [], "R": []}

    def finish_center(self):
        cc = self.center_capture
        if not cc or time.perf_counter() < cc["until"]:
            return
        for k in ("L", "R"):
            # XInput only reports on change; if no samples came in, the stick sat perfectly still at its last value
            pts = cc[k] or [tuple(self.sticks[k])]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            mx, my = statistics.mean(xs), statistics.mean(ys)
            maxdev = max(math.hypot(x, y) for x, y in pts)
            self.stick_stats[k]["center"] = {
                "samples": len(pts), "mean_x": round(mx), "mean_y": round(my),
                "offset_pct": round(100 * math.hypot(mx, my) / 32768, 2),
                "noise_x": round(statistics.pstdev(xs), 1), "noise_y": round(statistics.pstdev(ys), 1),
                "max_dev_pct": round(100 * maxdev / 32768, 2),
                "suggested_deadzone_pct": round(100 * maxdev / 32768 * 1.2 + 1, 1)}
        self.center_capture = None

    # ---------- snapshot for UI ----------
    def snapshot(self):
        self.finish_center()
        with self.lock:
            iv = list(self.intervals)
            # only use intervals from continuous motion (gaps > 50 ms mean "nothing changed")
            act = sorted(x for x in iv if x < 50)
            poll = None
            if len(act) > 20:
                poll = {"n": len(act), "mean_ms": round(statistics.mean(act), 3),
                        "median_ms": round(act[len(act) // 2], 3),
                        "min_ms": round(act[0], 3), "p99_ms": round(act[int(len(act) * .99) - 1], 3),
                        "jitter_ms": round(statistics.pstdev(act), 3),
                        "hz": round(1000 / statistics.median(act), 1)}
            sticks = {}
            for k, st in self.stick_stats.items():
                filled = [v for v in st["rad"] if v > 0]
                circ = None
                if len(filled) >= ANGLE_BINS * 0.9:
                    # average circularity error vs a perfect unit circle (0% = perfect circle)
                    circ = {"avg_err_pct": round(100 * statistics.mean(abs(v - 1) for v in filled), 1),
                            "min_r": round(min(filled), 3), "max_r": round(max(filled), 3)}
                sticks[k] = {"pos": self.sticks[k], "range": [st["minx"], st["maxx"], st["miny"], st["maxy"]],
                             "rad": st["rad"], "coverage": round(100 * len(filled) / ANGLE_BINS),
                             "circularity": circ, "center": st["center"]}
            return {
                "connected": self.connected, "info": self.info, "disconnects": self.disconnects,
                "raw": self.raw, "reports": self.reports, "bad_reports": self.bad_reports,
                "buttons": self.buttons, "press_count": self.press_count, "chatter": self.chatter,
                "dpad_combos": sorted(self.dpad_combos),
                "lt": self.lt, "rt": self.rt,
                "trig": {k: {"min": v["min"], "max": v["max"], "distinct": len(v["seen"])} for k, v in self.trig.items()},
                "sticks": sticks, "poll": poll,
                "centering": bool(self.center_capture),
            }


T = Tester()
