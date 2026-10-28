#!/usr/bin/env python3
"""CONTROLLER GAUNTLET — a game that puts an Xbox 360 pad through every test.

Run: ./play.sh      Keys: [s] skip stage   [q] quit
"""
import curses, json, math, random, statistics, threading, time

from x360test import T

LEFT_DZ, RIGHT_DZ = 7849 / 32768, 8689 / 32768   # official XInput deadzones
FACE = ["A", "B", "X", "Y", "LB", "RB", "START", "BACK", "LS", "RS", "GUIDE",
        "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT"]
DPAD_TARGETS = [("UP",), ("UP", "RIGHT"), ("RIGHT",), ("DOWN", "RIGHT"), ("DOWN",),
                ("DOWN", "LEFT"), ("LEFT",), ("UP", "LEFT")]
NICE = {"LS": "L3 (click left stick)", "RS": "R3 (click right stick)", "GUIDE": "GUIDE (Xbox button)",
        "DPAD_UP": "D-PAD ↑", "DPAD_DOWN": "D-PAD ↓", "DPAD_LEFT": "D-PAD ←", "DPAD_RIGHT": "D-PAD →"}


class Skip(Exception): pass
class Quit(Exception): pass


class Game:
    def __init__(self, scr):
        self.scr = scr
        self.score = 0
        self.report = {}
        self.msg = ""
        self.prev_counts = dict(T.press_count)
        curses.curs_set(0)
        scr.nodelay(True)
        curses.start_color(); curses.use_default_colors()
        for i, c in enumerate([curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_YELLOW,
                               curses.COLOR_CYAN, curses.COLOR_MAGENTA], 1):
            curses.init_pair(i, c, -1)
        self.G, self.R, self.Y, self.C, self.M = (curses.color_pair(i) for i in range(1, 6))

    # ---------- input helpers ----------
    def state(self):
        with T.lock:
            return {"b": dict(T.buttons), "L": [v / 32768 for v in T.sticks["L"]],
                    "R": [v / 32768 for v in T.sticks["R"]], "lt": T.lt, "rt": T.rt,
                    "raw": {k: list(v) for k, v in T.sticks.items()}, "conn": T.connected}

    def new_presses(self):
        with T.lock:
            cur = dict(T.press_count)
        out = [b for b in cur if cur[b] > self.prev_counts.get(b, 0)]
        self.prev_counts = cur
        return out

    # ---------- drawing ----------
    def put(self, y, x, s, attr=0):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and x < w:
            try: self.scr.addstr(y, max(0, x), s[: max(0, w - x - 1)], attr)
            except curses.error: pass

    def center(self, y, s, attr=0):
        self.put(y, (self.scr.getmaxyx()[1] - len(s)) // 2, s, attr)

    def frame(self, stage, draw):
        """Draw one frame; returns after ~1/60 s. Raises Skip/Quit on keys."""
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        self.put(0, 0, f" 🎮 CONTROLLER GAUNTLET  │  {stage}", curses.A_REVERSE | curses.A_BOLD)
        self.put(0, w - 22, f" SCORE {self.score:>7} ", curses.A_REVERSE | self.Y)
        if not T.connected:
            self.center(h // 2, "⚠  CONTROLLER DISCONNECTED — plug it back in", self.R | curses.A_BOLD)
        else:
            draw()
        if self.msg:
            self.center(h - 3, self.msg, self.G | curses.A_BOLD)
        self.put(h - 1, 0, " [s] skip stage   [q] quit ", curses.A_DIM)
        self.scr.refresh()
        k = self.scr.getch()
        if k in (ord("q"), ord("Q")): raise Quit
        if k in (ord("s"), ord("S")): raise Skip
        time.sleep(1 / 60)

    def stick_box(self, y0, x0, pos, label, targets=(), dz=None, trail=None, size=9):
        """Draw a stick plot: box of (2*size+1) rows × (4*size+1) cols."""
        H, W = size, size * 2
        for r in range(-H, H + 1):
            line = ""
            for c in range(-W, W + 1):
                nx, ny = c / W, -r / H
                d = math.hypot(nx, ny)
                ch = " "
                if dz and d < dz: ch = "·"
                if abs(d - 1) < 0.06: ch = "∙"
                if trail and trail.get((r, c)): ch = "░"
                line += ch
            self.put(y0 + r + H, x0, line, curses.A_DIM)
        for (tx, ty, rad, attr) in targets:
            for a in range(0, 360, 20):
                cx = round((tx + rad * math.cos(math.radians(a))) * W); cy = round(-(ty + rad * math.sin(math.radians(a))) * H)
                self.put(y0 + cy + H, x0 + cx + W, "o", attr)
        px = round(max(-1, min(1, pos[0])) * W); py = round(-max(-1, min(1, pos[1])) * H)
        self.put(y0 + py + H, x0 + px + W, "◉", self.Y | curses.A_BOLD)
        self.put(y0 + 2 * H + 1, x0 + W - len(label) // 2, label, curses.A_BOLD)

    def bar(self, y, x, val, width, zone=None, label=""):
        fill = round(val / 255 * width)
        s = "█" * fill + "─" * (width - fill)
        self.put(y, x, f"{label:>3} ▕", curses.A_BOLD)
        for i, ch in enumerate(s):
            attr = self.C
            if zone and zone[0] <= i / width * 255 <= zone[1]:
                attr = self.G | curses.A_BOLD if ch == "█" else self.G
            self.put(y, x + 5 + i, ch, attr)
        self.put(y, x + 6 + width, f"▏{val:3d}")

    def banner(self, stage, lines, secs=2.5):
        t0 = time.time()
        while time.time() - t0 < secs:
            def d():
                h = self.scr.getmaxyx()[0]
                for i, (s, a) in enumerate(lines):
                    self.center(h // 2 - len(lines) + i * 2, s, a)
            self.frame(stage, d)

    def ask(self, stage, question, during=None):
        """Yes/no via controller: A = yes, B = no."""
        self.new_presses()
        while True:
            if during: during()
            self.frame(stage, lambda: (self.center(8, question, curses.A_BOLD),
                                      self.center(11, "[A] YES        [B] NO", self.Y | curses.A_BOLD)))
            p = self.new_presses()
            if "A" in p: return True
            if "B" in p: return False

    # ======================= STAGES =======================
    def stage_drift(self):
        S = "STAGE 1 · DRIFT DETECTOR"
        self.banner(S, [("STAGE 1: DRIFT DETECTOR", self.M | curses.A_BOLD),
                        ("Put the controller down. HANDS OFF.", curses.A_BOLD)], 2.5)
        for n in (3, 2, 1):
            self.banner(S, [(f"Hands off in… {n}", self.Y | curses.A_BOLD)], 1)
        # capture resting samples
        samples = {"L": [], "R": []}
        t0 = time.time()
        while time.time() - t0 < 4:
            st = self.state()
            for k in "LR": samples[k].append(tuple(st["raw"][k]))
            self.frame(S, lambda: (self.center(3, f"MEASURING REST… {4 - (time.time() - t0):.1f}s  don't touch!", self.Y | curses.A_BOLD),
                                   self.stick_box(5, 8, self.state()["L"], "LEFT", dz=LEFT_DZ, size=8),
                                   self.stick_box(5, 48, self.state()["R"], "RIGHT", dz=RIGHT_DZ, size=8)))
        rest = {}
        for k in "LR":
            xs, ys = [p[0] for p in samples[k]], [p[1] for p in samples[k]]
            mx, my = statistics.mean(xs), statistics.mean(ys)
            rest[k] = {"x": round(mx), "y": round(my), "offset_pct": round(100 * math.hypot(mx, my) / 32768, 2),
                       "jitter": round(max(max(xs) - min(xs), max(ys) - min(ys)))}

        # snap-back test: flick each direction, let go, see where it lands
        snaps = {"L": [], "R": []}
        dirs = [("UP", 0, 1), ("RIGHT", 1, 0), ("DOWN", 0, -1), ("LEFT", -1, 0)]
        for k, name in (("L", "LEFT"), ("R", "RIGHT")):
            for dname, dx, dy in dirs:
                phase = "push"; t_rel = 0
                while True:
                    st = self.state(); x, y = st[k]
                    r = math.hypot(x, y)
                    if phase == "push" and r > 0.85 and (x * dx + y * dy) > 0.7: phase = "release"
                    elif phase == "release" and r < 0.35: phase, t_rel = "settle", time.time()
                    elif phase == "settle" and time.time() - t_rel > 0.6:
                        snaps[k].append(tuple(st["raw"][k])); self.score += 50; break
                    txt = {"push": f"Push {name} stick fully {dname} ➜",
                           "release": "…now LET GO! (just release it)", "settle": "settling…"}[phase]
                    tgt = [(dx * 0.92, dy * 0.92, 0.12, self.G)] if phase == "push" else []
                    self.frame(S + " · SNAP-BACK", lambda: (
                        self.center(3, txt, self.Y | curses.A_BOLD),
                        self.center(4, f"snap-back {len(snaps['L']) + len(snaps['R']) + 1}/8", curses.A_DIM),
                        self.stick_box(6, 26, st[k], f"{name} STICK", targets=tgt,
                                       dz=LEFT_DZ if k == "L" else RIGHT_DZ, size=8)))
        res = {}
        lines = [("DRIFT RESULTS", self.M | curses.A_BOLD)]
        for k, name, dz in (("L", "LEFT", LEFT_DZ), ("R", "RIGHT", RIGHT_DZ)):
            pts = snaps[k] + [(rest[k]["x"], rest[k]["y"])]
            worst = max(math.hypot(x, y) for x, y in pts) / 32768
            spread = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a in pts for b in pts) / 32768
            grade = ("EXCELLENT" if worst < 0.05 else "GOOD" if worst < 0.12 else
                     "WORN (hidden by deadzone)" if worst < dz else "DRIFTING — will show in games")
            res[k] = {"rest": rest[k], "snapback_points": snaps[k], "worst_offset_pct": round(worst * 100, 1),
                      "return_spread_pct": round(spread * 100, 1), "deadzone_pct": round(dz * 100, 1), "grade": grade}
            col = self.G if worst < 0.12 else self.Y if worst < dz else self.R
            self.score += max(0, int(500 * (1 - worst / dz)))
            lines += [(f"{name}: rest {rest[k]['offset_pct']}%  worst {worst*100:.1f}%  "
                       f"spread {spread*100:.1f}%  (deadzone {dz*100:.0f}%)", curses.A_BOLD),
                      (f"→ {grade}", col | curses.A_BOLD)]
        self.report["drift"] = res
        self.banner(S, lines, 6)

    def stage_buttons(self):
        S = "STAGE 2 · BUTTON BLITZ"
        self.banner(S, [("STAGE 2: BUTTON BLITZ", self.M | curses.A_BOLD),
                        ("Hit the button shown — as FAST as you can!", curses.A_BOLD)], 2.5)
        order = FACE[:]; random.shuffle(order)
        res = {}
        for i, btn in enumerate(order):
            self.new_presses()
            delay = random.uniform(0.6, 1.5); t0 = time.time(); wrong = []; early = 0
            while time.time() - t0 < delay:  # anticipation window
                if self.new_presses(): early += 1
                self.frame(S, lambda: self.center(8, "get ready…", curses.A_DIM))
            t_show = time.time()
            while True:
                p = self.new_presses()
                if btn in p:
                    rt = (time.time() - t_show) * 1000; break
                wrong += [b for b in p if b != btn]
                if time.time() - t_show > 15:
                    rt = None; break
                self.frame(S, lambda: (self.center(4, f"{i + 1}/{len(order)}", curses.A_DIM),
                                       self.center(8, f"▶  PRESS  {NICE.get(btn, btn)}  ◀", self.Y | curses.A_BOLD | curses.A_REVERSE),
                                       self.center(11, f"wrong: {', '.join(wrong)}" if wrong else "", self.R)))
            pts = 0 if rt is None else max(50, int(1000 - rt))
            self.score += pts
            res[btn] = {"reaction_ms": round(rt) if rt else None, "wrong_presses": wrong}
            self.msg = f"{btn}: {rt:.0f} ms  +{pts}" if rt else f"{btn}: NO RESPONSE ✗"
        self.msg = ""
        with T.lock: chatter = {b: c for b, c in T.chatter.items() if c}
        good = [r["reaction_ms"] for r in res.values() if r["reaction_ms"]]
        dead = [b for b, r in res.items() if r["reaction_ms"] is None]
        self.report["buttons"] = {"results": res, "dead": dead, "chatter": chatter,
                                  "avg_reaction_ms": round(statistics.mean(good)) if good else None}
        self.banner(S, [("BUTTON RESULTS", self.M | curses.A_BOLD),
                        (f"working: {len(good)}/{len(order)}   avg reaction {self.report['buttons']['avg_reaction_ms']} ms", curses.A_BOLD),
                        (f"dead: {', '.join(dead) or 'none'}", self.R if dead else self.G),
                        (f"bounce/chatter: {chatter or 'none'}", self.R if chatter else self.G)], 5)

    def stage_dpad(self):
        S = "STAGE 3 · D-PAD COMPASS"
        self.banner(S, [("STAGE 3: D-PAD COMPASS", self.M | curses.A_BOLD),
                        ("Hold the exact direction (incl. diagonals) until the bar fills", curses.A_BOLD)], 2.5)
        res = {}
        for tgt in DPAD_TARGETS:
            t0 = time.time(); hold = None; ghost = set()
            while True:
                b = self.state()["b"]
                held = tuple(d for d in ("UP", "DOWN", "LEFT", "RIGHT") if b[f"DPAD_{d}"])
                ok = set(held) == set(tgt)
                if held and not ok and set(tgt) <= set(held): ghost.add("+".join(held))
                if ok: hold = hold or time.time()
                else: hold = None
                prog = min(1, (time.time() - hold) / 0.5) if hold else 0
                if prog >= 1: break
                arrows = {"UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→"}
                self.frame(S, lambda: (self.center(7, "HOLD  " + " + ".join(arrows[d] for d in tgt), self.Y | curses.A_BOLD | curses.A_REVERSE),
                                       self.center(10, "▓" * int(prog * 30) + "░" * (30 - int(prog * 30)), self.G),
                                       self.center(12, "reading: " + ("+".join(held) or "—"), curses.A_DIM)))
            t = time.time() - t0
            self.score += max(50, int(600 - t * 200))
            res["+".join(tgt)] = {"seconds": round(t, 2), "phantom_combos": sorted(ghost)}
        self.report["dpad"] = res
        self.banner(S, [("D-PAD: all 8 directions ✓", self.G | curses.A_BOLD)], 2)

    def stage_triggers(self):
        S = "STAGE 4 · TRIGGER CONTROL"
        self.banner(S, [("STAGE 4: TRIGGER CONTROL", self.M | curses.A_BOLD),
                        ("Squeeze SLOWLY all the way, then release fully", curses.A_BOLD)], 2.5)
        res = {}
        for k, key in (("LT", "lt"), ("RT", "rt")):
            seen = set(); hit_max = False; released = False
            while not (hit_max and released):
                v = self.state()[key]; seen.add(v)
                if v == 255: hit_max = True
                if hit_max and v == 0: released = True
                self.frame(S, lambda: (self.center(5, f"Slowly squeeze {k} to 255, then release to 0", self.Y | curses.A_BOLD),
                                       self.bar(9, 6, v, 60, label=k),
                                       self.center(12, f"distinct values seen: {len(seen)}/256   max reached: {'✓' if hit_max else '…'}", curses.A_DIM)))
            # precision: hold inside a zone
            zones = []
            for _ in range(3):
                lo = random.randint(30, 190); z = (lo, lo + 35); t0 = time.time(); hold = None
                while True:
                    v = self.state()[key]
                    inside = z[0] <= v <= z[1]
                    hold = (hold or time.time()) if inside else None
                    prog = min(1, (time.time() - hold) / 1.0) if hold else 0
                    if prog >= 1: break
                    self.frame(S, lambda: (self.center(5, f"PRECISION: hold {k} inside the green zone for 1 s", self.Y | curses.A_BOLD),
                                           self.bar(9, 6, v, 60, zone=z, label=k),
                                           self.center(12, "▓" * int(prog * 30) + "░" * (30 - int(prog * 30)), self.G)))
                zones.append(round(time.time() - t0, 2)); self.score += max(50, int(800 - (time.time() - t0) * 150))
            res[k] = {"distinct_values": len(seen), "reached_max": hit_max, "rest_value": self.state()[key],
                      "precision_seconds": zones}
        self.report["triggers"] = res
        self.banner(S, [("TRIGGER RESULTS", self.M | curses.A_BOLD)] +
                    [(f"{k}: {r['distinct_values']}/256 steps seen, full range ✓, rests at {r['rest_value']}",
                      (self.G if r["distinct_values"] > 100 else self.Y) | curses.A_BOLD) for k, r in res.items()], 4)

    def stage_circles(self):
        S = "STAGE 5 · SPIN CYCLE"
        self.banner(S, [("STAGE 5: SPIN CYCLE", self.M | curses.A_BOLD),
                        ("Roll each stick around its full edge, slowly, a few times", curses.A_BOLD)], 2.5)
        res = {}
        for k, name in (("L", "LEFT"), ("R", "RIGHT")):
            bins = [0.0] * 72; trail = {}; t0 = time.time()
            while sum(1 for b in bins if b) < 72 or time.time() - t0 < 3:
                x, y = self.state()[k]; r = math.hypot(x, y)
                if r > 0.5:
                    i = int((math.degrees(math.atan2(y, x)) % 360) / 5) % 72; bins[i] = max(bins[i], r)
                trail[(round(-y * 8), round(x * 16))] = True
                cov = sum(1 for b in bins if b) / 72
                self.frame(S, lambda: (self.center(3, f"Spin the {name} stick around the rim", self.Y | curses.A_BOLD),
                                       self.center(4, f"coverage {cov*100:3.0f}%", self.G if cov == 1 else curses.A_DIM),
                                       self.stick_box(6, 26, (x, y), name, trail=trail, size=8)))
            avg = statistics.mean(bins); mn = min(bins)
            weak = [i * 5 for i, b in enumerate(bins) if b < 0.85]
            res[name] = {"avg_outer_radius": round(avg, 3), "min_outer_radius": round(mn, 3),
                         "weak_angles_deg": weak}
            self.score += int(500 * min(1, mn / 0.9))
        self.report["circularity"] = res
        self.banner(S, [("RANGE / CIRCULARITY", self.M | curses.A_BOLD)] +
                    [(f"{n}: avg rim {r['avg_outer_radius']}  weakest {r['min_outer_radius']}"
                      + (f"  short at {r['weak_angles_deg'][:6]}°" if r['weak_angles_deg'] else "  ✓ full reach"),
                      (self.G if r['min_outer_radius'] >= 0.85 else self.R) | curses.A_BOLD) for n, r in res.items()], 5)

    def stage_targets(self):
        S = "STAGE 6 · TARGET PRACTICE"
        self.banner(S, [("STAGE 6: TARGET PRACTICE", self.M | curses.A_BOLD),
                        ("Steer the ◉ into each target and hold still", curses.A_BOLD)], 2.5)
        res = {}
        for k, name in (("L", "LEFT"), ("R", "RIGHT")):
            times = []
            for n in range(6):
                a = random.uniform(0, 2 * math.pi); d = random.choice([0.3, 0.55, 0.8])
                tx, ty = d * math.cos(a), d * math.sin(a); t0 = time.time(); hold = None
                while True:
                    x, y = self.state()[k]
                    inside = math.hypot(x - tx, y - ty) < 0.12
                    hold = (hold or time.time()) if inside else None
                    prog = min(1, (time.time() - hold) / 0.5) if hold else 0
                    if prog >= 1: break
                    self.frame(S, lambda: (self.center(3, f"{name} stick → target {n + 1}/6", self.Y | curses.A_BOLD),
                                           self.center(4, "▓" * int(prog * 20) + "░" * (20 - int(prog * 20)), self.G),
                                           self.stick_box(6, 26, (x, y), name, targets=[(tx, ty, 0.12, self.G if inside else self.R)], size=8)))
                t = time.time() - t0; times.append(round(t, 2)); self.score += max(50, int(700 - t * 200))
            res[name] = {"seconds_per_target": times, "avg": round(statistics.mean(times), 2)}
        self.report["targets"] = res
        self.banner(S, [(f"{n}: avg {r['avg']} s per target", curses.A_BOLD) for n, r in res.items()], 3)

    def stage_rumble(self):
        S = "STAGE 7 · RUMBLE CHECK"
        self.banner(S, [("STAGE 7: RUMBLE CHECK", self.M | curses.A_BOLD),
                        ("Hold the controller. Answer with A (yes) / B (no)", curses.A_BOLD)], 2.5)
        res = {}
        # Steady rumble until answered (pulsing hides the light right motor while it spins up)
        tests = [("LEFT motor — deep, heavy rumble in the left grip", 255, 0),
                 ("RIGHT motor — light, high buzz in the right grip (weaker by design)", 0, 255),
                 ("BOTH motors — full", 255, 255)]
        for label, l, r in tests:
            T.rumble(0, 0); time.sleep(0.4)
            ok = self.ask(S, f"Feel it?  {label}", during=lambda: T.rumble(l, r))
            T.rumble(0, 0)
            res[label] = ok; self.score += 200 if ok else 0

        # Start-threshold sweep: ramp power up, press A the moment you feel it
        thresholds = {}
        for name, side in (("LEFT", 0), ("RIGHT", 1)):
            T.rumble(0, 0); self.new_presses()
            self.banner(S, [(f"THRESHOLD: {name} motor", self.M | curses.A_BOLD),
                            ("Power ramps up from 0. Press [A] the moment you FEEL it.", curses.A_BOLD)], 2.5)
            self.new_presses(); t0 = time.time(); felt = None
            while True:
                lvl = min(255, int((time.time() - t0) / 10 * 255))  # 0→255 over 10 s
                T.rumble(lvl if side == 0 else 0, lvl if side == 1 else 0)
                if "A" in self.new_presses(): felt = lvl; break
                if time.time() - t0 > 12: break
                self.frame(S, lambda: (self.center(6, f"{name} motor ramping… press [A] when you feel it", self.Y | curses.A_BOLD),
                                       self.bar(9, 6, lvl, 60, label=name[0] + "M")))
            T.rumble(0, 0)
            thresholds[name] = felt
            if felt is not None: self.score += 150
        res["start_threshold"] = thresholds
        self.report["rumble"] = res
        fmt = lambda v: f"{v}/255 ({v * 100 // 255}%)" if v is not None else "never felt ✗"
        self.banner(S, [("RUMBLE RESULTS", self.M | curses.A_BOLD)] +
                    [(f"{k}: {'✓' if v else '✗'}", (self.G if v else self.R) | curses.A_BOLD)
                     for k, v in res.items() if k != "start_threshold"] +
                    [(f"Felt from — left: {fmt(thresholds['LEFT'])}   right: {fmt(thresholds['RIGHT'])}", curses.A_BOLD)], 5)

    def stage_led(self):
        S = "STAGE 8 · RING OF LIGHT"
        self.banner(S, [("STAGE 8: RING OF LIGHT", self.M | curses.A_BOLD),
                        ("Look at the light around the Guide button", curses.A_BOLD)], 2.5)
        res = {}
        for pat, desc in ((6, "Quadrant 1 (top-left) lit"), (7, "Quadrant 2 (top-right) lit"),
                          (8, "Quadrant 3 (bottom-left) lit"), (9, "Quadrant 4 (bottom-right) lit"),
                          (10, "Ring ROTATING"), (13, "Ring ALTERNATING")):
            T.set_led(pat)
            ok = self.ask(S, f"Do you see: {desc}?")
            res[desc] = ok; self.score += 100 if ok else 0
        T.set_led(6)
        self.report["led"] = res

    def stage_cable(self):
        S = "STAGE 9 · CABLE STRESS"
        self.banner(S, [("STAGE 9: CABLE STRESS", self.M | curses.A_BOLD),
                        ("HOLD [A] and wiggle the cable at BOTH ends for 10 s", curses.A_BOLD),
                        ("Any dropout = a frayed cable or loose port", curses.A_DIM)], 3)
        while not self.state()["b"]["A"]:
            self.frame(S, lambda: self.center(8, "Press and HOLD [A] to start", self.Y | curses.A_BOLD))
        d0 = T.disconnects; drops = 0; was = True; t0 = time.time()
        while time.time() - t0 < 10:
            a = self.state()["b"]["A"]
            if was and not a: drops += 1
            was = a
            self.frame(S, lambda: (self.center(6, f"WIGGLE! keep holding A…  {10 - (time.time() - t0):.1f}s", self.Y | curses.A_BOLD),
                                   self.center(9, f"A releases: {drops}   USB disconnects: {T.disconnects - d0}",
                                               (self.R if drops or T.disconnects > d0 else self.G) | curses.A_BOLD)))
        dc = T.disconnects - d0
        self.report["cable"] = {"a_dropouts": drops, "usb_disconnects": dc}
        self.score += 500 if not (drops or dc) else 0
        self.banner(S, [("CABLE: SOLID ✓" if not (drops or dc) else f"CABLE: {dc} disconnects, {drops} dropouts ✗",
                         (self.G if not (drops or dc) else self.R) | curses.A_BOLD)], 3)

    def stage_polling(self):
        """Measured silently from everything played so far."""
        with T.lock:
            iv = sorted(x for x in T.intervals if x < 50)
            bad = T.bad_reports
        if len(iv) > 50:
            self.report["usb"] = {**T.info, "median_interval_ms": round(iv[len(iv) // 2], 2),
                                  "rate_hz": round(1000 / iv[len(iv) // 2]), "p99_ms": round(iv[int(len(iv) * .99)], 2),
                                  "jitter_ms": round(statistics.pstdev(iv), 2), "malformed_reports": bad}

    def results(self):
        self.stage_polling()
        json.dump(self.report, open("report.json", "w"), indent=2, default=str)
        lines = [("🏆  GAUNTLET COMPLETE  🏆", self.Y | curses.A_BOLD), (f"FINAL SCORE: {self.score}", curses.A_BOLD)]
        r = self.report
        if "drift" in r:
            for k, v in r["drift"].items():
                lines.append((f"Drift {k}: worst {v['worst_offset_pct']}% → {v['grade']}", curses.A_BOLD))
        if "buttons" in r:
            lines.append((f"Buttons: dead={r['buttons']['dead'] or 'none'} chatter={r['buttons']['chatter'] or 'none'}", curses.A_BOLD))
        if "usb" in r:
            u = r["usb"]
            lines.append((f"USB: {u['rate_hz']} Hz poll, p99 {u['p99_ms']} ms, jitter {u['jitter_ms']} ms", curses.A_BOLD))
        if "rumble" in r:
            rb = {k: v for k, v in r["rumble"].items() if k != "start_threshold"}
            th = r["rumble"].get("start_threshold", {})
            lines.append((f"Rumble: {sum(rb.values())}/{len(rb)} felt · starts at L={th.get('LEFT')} R={th.get('RIGHT')}", curses.A_BOLD))
        lines.append(("Full details saved to report.json — press q to exit", curses.A_DIM))
        while True:
            self.banner("RESULTS", lines, 1)

    def run(self, only=None):
        stages = [self.stage_drift, self.stage_buttons, self.stage_dpad, self.stage_triggers,
                  self.stage_circles, self.stage_targets, self.stage_rumble, self.stage_led, self.stage_cable]
        if only: stages = [stages[only - 1]]
        try:
            while not T.connected:
                self.frame("WAITING", lambda: self.center(8, "Waiting for controller…", self.Y))
            self.banner("READY", [("CONTROLLER GAUNTLET", self.M | curses.A_BOLD),
                                  (f"{T.info.get('product')}  ({T.info.get('vid')}:{T.info.get('pid')})", curses.A_BOLD),
                                  ("9 stages · tests every input, output and the cable", curses.A_DIM)], 3)
            for s in stages:
                try: s()
                except Skip: T.rumble(0, 0); self.msg = ""
            self.results()
        except Quit:
            pass
        finally:
            T.rumble(0, 0)
            json.dump(self.report, open("report.json", "w"), indent=2, default=str)


if __name__ == "__main__":
    threading.Thread(target=T.loop, daemon=True).start()
    import sys
    only = int(sys.argv[1]) if len(sys.argv) > 1 else None   # e.g. ./play.sh 7  → rumble stage only
    curses.wrapper(lambda scr: Game(scr).run(only))
    print("Report saved to report.json")
