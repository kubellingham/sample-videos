"""shrinking_ring: a ball bounces inside a slowly rotating, shrinking ring with a gap.

Each bounce plays the next note and either grows the ball (``mode: grow``) or
spawns a new ball (``mode: spawn``). The video ends with

* an escape burst - a ball slips out through the gap and the ring shatters, or
* a trapped freeze - the ball fills the ring (grow) or the ring packs full
  (spawn), or time runs out.

Balls keep a constant mechanical energy, so ``energy`` sets how high up the
ring they can reach. With the gap starting near the top, the ring's rotation
and shrink decide when the gap comes within reach; ball growth decides whether
the ball still fits through it. The ring tints red as the ball's diameter
approaches the gap's width, and the shrink speeds up over the final third.

The video opens mid-flight (``preroll``) with the first bounce inside
``first_bounce`` seconds. An escape plays ``slowmo`` seconds of quarter-speed
slow motion as the ball passes the gap, timed from the rehearsal, then bursts.

MIDI choreography (``sync: midi``, the default when the config names a MIDI
file): the hero ball bounces exactly on the score's notes. At each bounce the
next flight is solved for: a landing point on the wall that the ball reaches
in exactly the time to the next note (on the 240 Hz physics clock), staying
inside the shrinking ring and away from the gap, as close as possible to the
natural rebound and energy. A gap too long for one flight gets quiet "ghost"
bounces in between; notes too close for a bounce play without one. Growth per
note is calibrated to the score, and the video ends on its last note: the
ball pops to fill the ring and freezes, or flies out through the gap, the
burst landing a beat later (``midi_ending``).
"""

from __future__ import annotations

import bisect
import math

import pymunk

from ..palettes import mix
from ..scene import Arc, Circle, Effects, Label, Particle, Ripple, Scene
from ..timeline import Event
from .base import PHYSICS_HZ, Simulation

DT = 1.0 / PHYSICS_HZ
POP_SPEED = 1400.0  # midi trapped ending: px/s the ball grows to fill the ring on the last note

BALL, RING = 1, 2
GROW_AUTO = 85.0  # grow = GROW_AUTO / seconds of action (calibrated for the default ring)
SLOW = 4  # video ticks per physics step during slow motion (quarter speed)
DANGER = (255, 60, 80)  # ring tint as the ball outgrows the gap


class _Ball:
    __slots__ = ("id", "body", "shape", "r", "target_r", "color", "hero", "born",
                 "last_wall", "pending", "hit_t", "escaped", "touch_since", "touch_seen", "expect")

    def __init__(self, id_, body, shape, r, color, hero, born):
        self.id = id_
        self.body = body
        self.shape = shape
        self.r = r
        self.target_r = r
        self.color = color
        self.hero = hero
        self.born = born
        self.last_wall = -1.0
        self.pending = 0.0  # approach speed of a bounce detected this step
        self.hit_t = -1.0  # last bounce time (for the rim flash)
        self.escaped = False
        self.touch_since: float | None = None  # start of the current wall contact
        self.touch_seen = -1.0  # last step the ball touched the wall
        self.expect: tuple[float, int | None] | None = None  # midi: next planned bounce (time, note index or ghost)


class _Spark:
    __slots__ = ("x", "y", "vx", "vy", "t0", "life", "r", "color", "drag", "grav", "glow")

    def __init__(self, x, y, vx, vy, t0, life, r, color, drag=2.0, grav=0.0, glow=False):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.t0, self.life, self.r, self.color = t0, life, r, color
        self.drag, self.grav, self.glow = drag, grav, glow


class _Shard:
    __slots__ = ("start", "sweep", "px", "py", "dx", "dy", "vx", "vy", "rot", "vrot")

    def __init__(self, start, sweep, px, py, vx, vy, vrot):
        self.start, self.sweep, self.px, self.py = start, sweep, px, py
        self.dx = self.dy = self.rot = 0.0
        self.vx, self.vy, self.vrot = vx, vy, vrot


def _ease_out(k: float) -> float:
    k = min(1.0, max(0.0, k))
    return 1.0 - (1.0 - k) ** 3


def _smooth(k: float) -> float:
    k = min(1.0, max(0.0, k))
    return k * k * (3 - 2 * k)


class ShrinkingRing(Simulation):
    name = "shrinking_ring"
    description = "Ball bounces in a rotating, shrinking ring with a gap; grows or multiplies per bounce."
    preroll = 0.25  # the video opens with the ball already in flight
    defaults = {
        "mode": "grow",  # grow | spawn
        "center": [540, 940],
        "ring_radius": 452.0,  # inner radius at t=0 (px); the ring spans ~85% of the width
        "ring_min_radius": 230.0,
        "ring_shrink": 3.0,  # steady shrink (px/s)
        "ring_squeeze": 90.0,  # extra shrink over the final third, accelerating (px)
        "ring_width": 14.0,
        "gap": 62.0,  # gap size (degrees)
        "spin": [2.5, 6.0],  # rotation speed range (deg/s); direction is random
        "gap_start": [-125.0, -55.0],  # initial gap angle range (deg; -90 is straight up)
        "gravity": 6000.0,
        "energy": [105.0, 180.0],  # how far above the ring center balls can reach (px)
        "ball_radius": 44.0,
        "first_bounce": [0.16, 0.28],  # first bounce lands this many seconds into the video
        "grow": "auto",  # px added to the radius per bounce (grow mode); auto scales with duration
        "grow_speed": 140.0,  # max visual growth rate (px/s)
        "fill": 0.93,  # grow mode: trapped once ball radius >= fill * ring radius
        "slowmo": 0.4,  # escape: seconds of quarter-speed slow motion before the burst (0 = off)
        "spawn_radius": 16.0,  # spawn mode: radius of new balls
        "spawn_from": "hero",  # hero: only the first ball's bounces spawn | any
        "spawn_cooldown": 0.15,  # spawn mode with spawn_from=any (s)
        "max_balls": 160,
        "pack": 0.42,  # spawn mode: trapped once balls cover this share of the ring
        "trail": 0.16,  # trail length (s)
        "counter": True,  # bounce / ball counter under the ring
        "escape_text": "ESCAPED!",  # shown after an escape ("" to hide)
        "trapped_text": "TRAPPED!",  # shown after a trapped freeze ("" to hide)
        "sync": "auto",  # auto | free | midi (auto: midi when the config names a MIDI file)
        "midi_ending": "auto",  # midi: trapped | escape | auto (from the seed)
        "min_flight": 0.09,  # midi: shortest flight between notes (s); closer notes play without a bounce
    }

    @classmethod
    def pick_variant(cls, cfg, palette):
        """With MIDI the score fixes the pacing: no variants to try."""
        p = {**cls.all_defaults(), **cfg.params}
        if p["sync"] == "midi" or (p["sync"] == "auto" and (cfg.sound or {}).get("midi")):
            return 0, cls(cfg, palette, variant=0).run(fps=None)
        return super().pick_variant(cfg, palette)

    # ------------------------------------------------------------------ setup
    def setup(self) -> None:
        p, rng = self.p, self.rng
        if p.mode not in ("grow", "spawn"):
            raise ValueError("shrinking_ring: mode must be 'grow' or 'spawn'")
        self.cx, self.cy = float(p.center[0]), float(p.center[1])
        self.R0 = self.R = float(p.ring_radius)
        self.w = float(p.ring_width)
        self.gap = float(p.gap)
        self.g = float(p.gravity)
        self.h = rng.uniform(*p.energy)
        self.spin = rng.uniform(*p.spin) * rng.choice((-1.0, 1.0))
        angle0 = rng.uniform(*p.gap_start)  # gap direction at t=0
        # The action should be over by t_end so the ending can hold and loop back.
        self.t_end = max(4.0, self.cfg.duration - self.tail)
        self.t_squeeze = self.t_end * 2 / 3
        self.squeeze_k = float(p.ring_squeeze) / max(1.0, self.t_end - self.t_squeeze) ** 2
        if p.grow == "auto":
            # Calibrated so a ball that never escapes fills the ring around the end of the action.
            self.grow = GROW_AUTO / self.t_end
        else:
            self.grow = float(p.grow)
        self.angle0 = angle0
        sync = str(p.sync)
        if sync == "auto":
            sync = "midi" if (self.cfg.sound or {}).get("midi") else "free"
        if sync not in ("free", "midi"):
            raise ValueError("shrinking_ring: sync must be auto, free or midi")
        self.midi = sync == "midi"
        self.final_t: float | None = None  # midi: time of the last note
        self.popping = False
        self.loose: list[float] = []  # midi: notes that play without a bounce (for a glow)
        if self.midi:
            self._midi_setup()

        space = pymunk.Space()
        space.gravity = (0.0, self.g)
        space.iterations = 20
        self.space = space

        body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        body.position = (self.cx, self.cy)
        body.angle = math.radians(angle0 + self.spin * self.t)  # self.t < 0 during the pre-roll
        body.angular_velocity = math.radians(self.spin)
        space.add(body)
        self.ring_body = body
        n = 140
        a0, a1 = math.radians(self.gap / 2), math.radians(360 - self.gap / 2)
        self.seg_angles = [a0 + (a1 - a0) * k / n for k in range(n + 1)]
        self.segments = []
        for k in range(n):
            seg = pymunk.Segment(body, (0, 0), (1, 0), self.w / 2)
            seg.elasticity = 1.0
            seg.friction = 0.0
            seg.collision_type = RING
            self.segments.append(seg)
        space.add(*self.segments)
        self._seg_R = None
        self._set_ring_radius(self.R)

        self.balls: list[_Ball] = []
        self._ball_of: dict = {}
        self.sparks: list[_Spark] = []
        self.ripples: list[tuple] = []  # (t0, x, y, color, r0, r1, dur, width)
        self.shards: list[_Shard] = []
        self.bounces = 0
        self.last_bounce_t = -10.0
        self.last_color = self.palette.ring
        self.last_spawn_t = -10.0
        self.sealed_t: float | None = None
        self.escape_ball: _Ball | None = None
        self.color_i = 0

        hero = self._add_ball(*self._launch(), float(p.ball_radius), hero=True)
        if self.midi:
            first = self.targets[0][0]
            if self._aim(hero, first, self.t):  # exact on the physics clock
                hero.expect = (first, 0)

        # Slow motion: the rehearsal says when (in physics time) the ball gets
        # through. The slow stretch starts on the frame grid at or just after
        # ``slowmo`` before that, lasts exactly ``slowmo`` seconds of video, and
        # the burst waits for its end (at most FRAME_GRID steps after the crossing).
        self.slow_from = self.slow_to = None
        self.slow_flagged = False
        if self.plan is not None and self.plan.ending == "escape" and float(p.slowmo) > 0:
            s_e = round(self.plan.of_kind("escape")[0].data["pt"] * PHYSICS_HZ)
            steps = round(float(p.slowmo) / SLOW * PHYSICS_HZ)
            start = self.slow_start_step(s_e - steps)
            self.slow_from, self.slow_to = start / PHYSICS_HZ, (start + steps) / PHYSICS_HZ

        space.on_collision(BALL, RING, begin=self._on_wall, pre_solve=self._on_wall_touch,
                           separate=self._on_wall_separate)
        space.on_collision(BALL, BALL, begin=self._on_ball)

    def _ring_radius(self, t: float) -> float:
        """Steady shrink, plus an accelerating squeeze over the final third."""
        t = max(0.0, t)
        R = self.R0 - float(self.p.ring_shrink) * t
        if t > self.t_squeeze:
            R -= self.squeeze_k * (t - self.t_squeeze) ** 2
        return max(float(self.p.ring_min_radius), R)

    def _launch(self) -> tuple[float, float, float, float]:
        """Start state (at the start of the pre-roll) for a ball already in
        flight that first hits the lower ring ``first_bounce`` seconds into the
        video: pick the hit, then run the parabola backwards."""
        p, rng, g = self.p, self.rng, self.g
        r = float(p.ball_radius)
        reach = self.R0 - r - 0.5  # ball-center distance at contact
        for _ in range(200):
            tau = rng.uniform(*p.first_bounce) - self.t  # flight time from launch to the hit
            if self.midi:
                tau = self.targets[0][0] - self.t  # the first note
            phi = math.radians(90 + rng.uniform(-50, 50))  # hit point on the lower ring
            hx, hy = self.cx + reach * math.cos(phi), self.cy + reach * math.sin(phi)
            speed = self._speed_at(hy)
            a = phi + math.radians(rng.choice((-1, 1)) * rng.uniform(15, 50))
            vx, vy = speed * math.cos(a), speed * math.sin(a)  # heading into the wall
            x0, y0 = hx - vx * tau, hy - vy * tau + 0.5 * g * tau * tau
            vy0 = vy - g * tau
            inside = all(
                math.hypot(x0 + vx * s - self.cx, y0 + vy0 * s + 0.5 * g * s * s - self.cy) < reach - 3
                for s in (tau * i / 16 for i in range(16))
            )
            if inside:
                return x0, y0, vx, vy0
        speed = self._speed_at(self.cy)  # fallback: from the center, straight down
        return self.cx, self.cy, 0.0, speed

    def video_ticks(self) -> int:
        if self.slow_from is not None and self.slow_from - 1e-9 <= self.t < self.slow_to - 1e-9:
            return SLOW
        return 1

    def _set_ring_radius(self, R: float) -> None:
        if self._seg_R is not None and abs(self._seg_R - R) < 0.05:
            return
        rc = R + self.w / 2
        pts = [(rc * math.cos(a), rc * math.sin(a)) for a in self.seg_angles]
        for seg, a, b in zip(self.segments, pts[:-1], pts[1:]):
            seg.unsafe_set_endpoints(a, b)
        self._seg_R = R

    def _speed_at(self, y: float) -> float:
        """Speed a ball must have at height ``y`` to carry the target energy."""
        return math.sqrt(max(0.0, 2.0 * self.g * (y - self.cy + self.h)))

    def _add_ball(self, x, y, vx, vy, r, hero=False) -> _Ball:
        mass = max(0.1, r * r * 0.01)
        body = pymunk.Body(mass, pymunk.moment_for_circle(mass, 0, r))
        body.position = (x, y)
        body.velocity = (vx, vy)
        shape = pymunk.Circle(body, r)
        shape.elasticity = 1.0
        shape.friction = 0.0
        shape.collision_type = BALL
        self.space.add(body, shape)
        color = self.palette.accent(self.color_i)
        self.color_i += 1
        ball = _Ball(len(self.balls), body, shape, r, color, hero, self.t_next)
        self.balls.append(ball)
        self._ball_of[shape] = ball
        return ball

    # -------------------------------------------------------------- collisions
    def _on_wall(self, arb: pymunk.Arbiter, space, data) -> None:
        if self.ended:
            return
        a, b = arb.shapes
        ball = self._ball_of.get(a) or self._ball_of.get(b)
        if ball is None:
            return
        n = arb.normal
        v = ball.body.velocity
        approach = abs(v.x * n.x + v.y * n.y)
        if approach < 80.0 or self.t_next - ball.last_wall < 0.05:
            return
        ball.last_wall = self.t_next
        ball.pending = max(ball.pending, approach)

    def _on_wall_touch(self, arb: pymunk.Arbiter, space, data) -> None:
        a, b = arb.shapes
        ball = self._ball_of.get(a) or self._ball_of.get(b)
        if ball is not None:
            if ball.touch_since is None:
                ball.touch_since = self.t_next
            ball.touch_seen = self.t_next

    def _on_wall_separate(self, arb: pymunk.Arbiter, space, data) -> None:
        a, b = arb.shapes
        ball = self._ball_of.get(a) or self._ball_of.get(b)
        if ball is not None:
            ball.touch_since = None

    def _unstick(self, t: float) -> None:
        """A grazing ball can end up skating along the curved wall in permanent
        contact, which never fires a new bounce. Kick it back inwards and count
        that as a bounce."""
        for ball in self.balls:
            if ball.touch_seen != t or ball.touch_since is None or t - ball.touch_since < 0.1:
                continue
            body = ball.body
            nx, ny = self.cx - body.position.x, self.cy - body.position.y
            d = math.hypot(nx, ny) or 1.0
            nx, ny = nx / d, ny / d
            v = body.velocity
            mag = math.hypot(v.x, v.y)
            if mag < 1.0:
                continue
            vr = v.x * nx + v.y * ny
            vx, vy = v.x + (0.45 * mag - vr) * nx, v.y + (0.45 * mag - vr) * ny
            k = mag / (math.hypot(vx, vy) or 1.0)
            body.velocity = (vx * k, vy * k)
            ball.touch_since = t
            if t - ball.last_wall >= 0.05:
                ball.last_wall = t
                ball.pending = max(ball.pending, 0.45 * mag)

    def _on_ball(self, arb: pymunk.Arbiter, space, data) -> None:
        if self.ended:
            return
        a, b = arb.shapes
        ba, bb = self._ball_of.get(a), self._ball_of.get(b)
        if ba is None or bb is None:
            return
        rel = ba.body.velocity - bb.body.velocity
        n = arb.normal
        approach = abs(rel.x * n.x + rel.y * n.y)
        if approach < 250.0:
            return
        pt = arb.contact_point_set.points[0].point_a if arb.contact_point_set.points else ba.body.position
        self.emit("hit", pt.x, pt.y, strength=min(1.0, approach / 2500.0))
        if self.midi and (ba.hero or bb.hero):
            self.hero_disturbed = True

    # -------------------------------------------------------------------- step
    def step(self, dt: float) -> None:
        p = self.p
        t = self.t_next
        if self.ended and self.timeline.ending != "escape":
            return  # trapped / timeout: the world freezes

        if not self.ended:
            self.R = self._ring_radius(t)
            self._set_ring_radius(self.R)
            if self.slow_from is not None and not self.slow_flagged and self.t >= self.slow_from - 1e-9:
                # the riser in the audio runs from here to the burst
                self.slow_flagged = self.hold_timeout = True
                self.timeline.add(Event(t=self.vt, kind="slowmo", x=self.balls[0].body.position.x,
                                        y=self.balls[0].body.position.y,
                                        data={"duration": round((self.slow_to - self.t) * SLOW, 6)}))

        # grow mode: ease the radius towards its target
        if p.mode == "grow" and not self.ended:
            speed = POP_SPEED if self.popping else float(p.grow_speed)
            for ball in self.balls:
                if ball.r < ball.target_r:
                    ball.r = min(ball.target_r, ball.r + speed * dt)
                    ball.shape.unsafe_set_radius(ball.r)

        self.space.step(dt)

        if not self.ended:
            self._unstick(t)
            self._resolve_bounces(t)
            if self.midi and self.hero_disturbed:
                self._replan(self.balls[0], t)
            self._hold_energy()
            self._check_escape(t)
            if not self.ended:
                self._check_trapped(t)

        self._update_particles(dt, t)

    def _resolve_bounces(self, t: float) -> None:
        p = self.p
        spawned_this_step = False
        for ball in list(self.balls):
            if ball.pending <= 0.0:
                continue
            approach, ball.pending = ball.pending, 0.0
            body = ball.body
            if self.midi and ball.hero:
                if self._midi_bounce(ball, t, approach):
                    continue
            elif self.midi:
                # other balls tap softly: the score's notes belong to the hero
                self.emit("tap", body.position.x, body.position.y, strength=0.5, ball=ball.id)
                continue
            # Keep energy constant: a bounce re-energises the ball.
            speed = self._speed_at(body.position.y)
            v = body.velocity
            mag = math.hypot(v.x, v.y)
            if speed > 1.0 and mag > 1.0:
                body.velocity = (v.x * speed / mag, v.y * speed / mag)

            dx, dy = body.position.x - self.cx, body.position.y - self.cy
            d = math.hypot(dx, dy) or 1.0
            hx, hy = self.cx + dx / d * (d + ball.r), self.cy + dy / d * (d + ball.r)
            ref = self._speed_at(self.cy + self.R)
            self.bounces += 1
            new_color = self.palette.accent(self.color_i)
            if p.mode == "grow" or ball.hero:
                self.color_i += 1
                ball.color = new_color
            ball.hit_t = t
            self.last_bounce_t = t
            self.last_color = ball.color
            if self.midi:  # a note bounce: lands exactly on the note, plays the score's pitches
                at, idx = ball.expect
                self.timeline.add(Event(t=at, kind="bounce", x=hx, y=hy, strength=self.targets[idx][2],
                                        data={"ball": ball.id, "n": self.bounces, "r": round(ball.r, 2),
                                              "hero": True, "notes": self.targets[idx][1]}))
            else:
                self.emit("bounce", hx, hy, strength=min(1.0, approach / ref),
                          ball=ball.id, n=self.bounces, r=round(ball.r, 2), hero=ball.hero)
            if ball.hero:
                self.ripples.append((t, hx, hy, ball.color, 14.0, 120.0, 0.45, 7.0))
            else:
                self.ripples.append((t, hx, hy, ball.color, 6.0, 42.0, 0.25, 3.0))
            self._sparks_at(hx, hy, dx / d, dy / d, ball.color, t, count=7 if ball.hero else 2)

            if p.mode == "grow":
                cap = self.R * float(p.fill) + 2.0
                ball.target_r = min(cap, ball.target_r + self.grow)
            elif not spawned_this_step and len(self.balls) < int(p.max_balls):
                if (p.spawn_from == "hero" and ball.hero) or (
                    p.spawn_from == "any" and t - self.last_spawn_t >= float(p.spawn_cooldown)
                ):
                    spawned_this_step = self._spawn(t)
            if self.midi and ball.hero:
                self._midi_after_note(ball, t)  # after growing: the next flight allows for the new size

    # ------------------------------------------------------- midi choreography
    def _midi_setup(self) -> None:
        """Targets from the score, the ending, and growth per note."""
        from ..audio import harmony

        p = self.p
        score = self.load_score()
        first = score.onsets()[0][0]
        off = max(0.0, 0.2 - first)  # the first note lands at least 0.2 s in (the hook)
        score = score.segment(max(1.0, self.t_end - off)).shifted(off)
        self.targets = [(t, [[n.pitch, round(0.3 + 0.7 * n.vel / 127, 4)] for n in g],
                         round(max(n.vel for n in g) / 127, 4))
                        for t, g in score.onsets() if t <= self.t_end + 1e-9]
        if not self.targets:
            raise ValueError("shrinking_ring: the MIDI score has no notes before the end of the action")
        self.ti = 0  # next note to schedule
        self.hero_disturbed = False
        self.contacts: list[tuple[float, float]] = []  # (planned, actual) time of each note bounce
        h = harmony.from_score(score)
        self.beat = 60.0 / h.tempo
        self.timeline.music = h.to_dict()
        self.timeline.audio_timing = "exact"
        ending = str(p.midi_ending)
        if ending == "auto":
            ending = "escape" if self.rng.random() < 0.5 else "trapped"
        if ending not in ("trapped", "escape"):
            raise ValueError("shrinking_ring: midi_ending must be auto, trapped or escape")
        self.midi_ending = ending
        t_last = self.targets[-1][0]
        R = self._ring_radius(t_last)
        end_r = float(p.ball_radius)
        if p.mode == "grow" and p.grow == "auto":
            # grow evenly over the notes: still small enough to slip out after the last note (escape),
            # or about 60% of the ring before popping to fill it on the last note (trapped)
            opening = 2 * (R + self.w / 2) * math.sin(math.radians(self.gap / 2)) - self.w
            end_r = 0.3 * opening if ending == "escape" else 0.6 * R
            self.grow = max(0.3, (end_r - float(p.ball_radius)) / max(1, len(self.targets) - 1))
        # Gravity: a flight can last at most ~2 sqrt(4 room / g). Lower it (never below 40%) so the
        # score's usual gap between notes still fits in one flight when the room is smallest.
        gaps = sorted(b[0] - a[0] for a, b in zip(self.targets, self.targets[1:]))
        if gaps:
            usual = gaps[min(len(gaps) - 1, int(0.75 * len(gaps)))]
            room = max(20.0, R - end_r)
            self.g = max(0.4 * self.g, min(self.g, 12.0 * room / max(0.05, usual) ** 2))

    def _gap_angle(self, t: float) -> float:
        return math.radians(self.angle0 + self.spin * t)

    def _r_at(self, ball: _Ball, k: int) -> float:
        """Ball radius ``k`` steps from now (growth eases towards its target)."""
        speed = POP_SPEED if self.popping else float(self.p.grow_speed)
        return min(ball.target_r, ball.r + speed * k * DT) if self.p.mode == "grow" else ball.r

    def _aim(self, ball: _Ball, at: float, t0: float, escape: bool = False) -> bool:
        """Give ``ball`` (at its position now, time ``t0``) the velocity that
        makes it touch the wall exactly at time ``at`` on the physics clock, or
        (``escape``) cross the ring line through the gap then. Pymunk moves
        first and applies gravity after, so after N steps the ball is at
        p0 + N dt v0 + g dt^2 N (N - 1) / 2. Among landing points on the wall
        (every 2 degrees) whose flight stays inside the ring and clear of the
        gap, picks the one closest to the natural rebound (direction and
        energy). Returns False if none works."""
        n = round((at - t0) * PHYSICS_HZ)
        if n < 2:
            return False
        body = ball.body
        x0, y0 = body.position.x, body.position.y
        vrx, vry = body.velocity.x, body.velocity.y
        vref = math.hypot(vrx, vry) or 1.0
        vnat = max(300.0, self._speed_at(y0))
        g = self.g
        drop = g * DT * DT * n * (n - 1) / 2
        r_end = self._r_at(ball, n)
        R_end = self._ring_radius(at)
        rc = R_end + self.w / 2
        gap_c = self._gap_angle(at)
        half_gap = math.radians(self.gap / 2)
        d0 = math.hypot(x0 - self.cx, y0 - self.cy)
        at_wall = d0 > self._ring_radius(t0) - ball.r - 6
        ks = sorted({max(1, n * j // 24) for j in range(1, 24)} | {n - 1})
        limits = [(k, self._ring_radius(t0 + k * DT) - self._r_at(ball, k)) for k in ks]
        caps = [(self.cx + rc * math.cos(gap_c + s_ * half_gap), self.cy + rc * math.sin(gap_c + s_ * half_gap))
                for s_ in (-1, 1)]
        if escape:
            angles = [gap_c]
            reach = rc + 4.0  # just past the ring line
        else:
            angles = [math.radians(a) for a in range(0, 360, 2)]
            reach = R_end - r_end + 0.5  # 0.5 px into the wall: the contact starts on step n, not n + 1
            # the ball touches the wall at one point: keep that point on solid wall, clear of the rounded ends
            margin = half_gap + (self.w / 2 + 4.0) / max(1.0, R_end)
        best = None
        for phi in angles:
            if not escape and abs((phi - gap_c + math.pi) % (2 * math.pi) - math.pi) < margin:
                continue  # would land in (or on the edge of) the gap
            qx, qy = self.cx + reach * math.cos(phi), self.cy + reach * math.sin(phi)
            vx = (qx - x0) / (n * DT)
            vy = (qy - y0 - drop) / (n * DT)
            sp = math.hypot(vx, vy)
            if sp > (6500.0 if escape else 5000.0) or sp < 60.0:
                continue
            if at_wall and ((x0 - self.cx) * vx + (y0 - self.cy) * vy) / max(d0, 1.0) > -40.0:
                continue  # must leave the wall inwards
            if not escape:
                vnx, vny = vx, vy + g * DT * (n - 1)
                if vnx * math.cos(phi) + vny * math.sin(phi) < 250.0:
                    continue  # must arrive moving into the wall (a clear bounce, on the right step)
            ok = True
            for k, lim in limits:
                px = x0 + k * DT * vx - self.cx
                py = y0 + k * DT * vy + g * DT * DT * k * (k - 1) / 2 - self.cy
                d = math.hypot(px, py)
                if d <= lim - (0.05 if k == n - 1 else 1.0):
                    continue
                if at_wall and k <= 6 and d <= d0 + 0.05:
                    continue  # still peeling off the wall it bounced from
                if escape:  # outside the wall's reach only inside the gap, clear of its rounded ends
                    t_k = t0 + k * DT
                    ga = self._gap_angle(t_k)
                    inside_gap = abs((math.atan2(py, px) - ga + math.pi) % (2 * math.pi) - math.pi) < half_gap
                    rk = self._r_at(ball, k)
                    if inside_gap and all(math.hypot(px + self.cx - cx_, py + self.cy - cy_) > rk + self.w / 2 + 1
                                          for cx_, cy_ in caps):
                        continue
                ok = False
                break
            if not ok:
                continue
            cosang = max(-1.0, min(1.0, (vx * vrx + vy * vry) / (sp * vref)))
            cost = math.acos(cosang) + 0.8 * abs(math.log(sp / vnat))
            if best is None or cost < best[0]:
                best = (cost, vx, vy)
        if best is None:
            return False
        body.velocity = (best[1], best[2])
        return True

    def _loose_note(self, i: int) -> None:
        """A note the ball cannot bounce on: it still plays, on time."""
        at, notes, vel = self.targets[i]
        hero = self.balls[0]
        self.timeline.add(Event(t=at, kind="note", x=hero.body.position.x, y=hero.body.position.y, strength=vel,
                                data={"notes": notes, "loose": True}))
        bisect.insort(self.loose, at)

    def _plan(self, ball: _Ball, t0: float) -> None:
        """Aim at the next note from here, with ghost bounces if it is too far for one flight."""
        min_flight = float(self.p.min_flight)
        last = len(self.targets) - 1
        while self.ti <= last:
            at = self.targets[self.ti][0]
            if at - t0 < min_flight:
                self._loose_note(self.ti)
                self.ti += 1
                continue
            if self._aim(ball, at, t0):
                ball.expect = (at, self.ti)
                return
            for k in (2, 3, 4, 5, 6, 8):
                ghost = t0 + (at - t0) / k
                if ghost - t0 < min_flight:
                    break
                if self._aim(ball, ghost, t0):
                    ball.expect = (ghost, None)
                    return
            self._loose_note(self.ti)  # out of reach: the note plays without a bounce
            self.ti += 1
        ball.expect = None

    def _replan(self, ball: _Ball, t: float) -> None:
        """The hero was knocked off course (another ball): fly to the same note from here."""
        self.hero_disturbed = False
        if ball.expect is not None and not self._aim(ball, ball.expect[0], t):
            if ball.expect[1] is not None:
                self.ti = ball.expect[1]
            self._plan(ball, t)

    def _midi_bounce(self, ball: _Ball, t: float, approach: float) -> bool:
        """Handle a hero bounce in midi mode. Returns True when it is not a
        note bounce (a ghost or an unplanned touch), which taps softly and
        replans; False lets the normal bounce (count, colour, growth) run."""
        exp = ball.expect
        pos = ball.body.position
        if exp is not None and exp[1] == -1:  # a ghost bounce on the way out
            self.emit("tap", pos.x, pos.y, strength=0.4, ball=ball.id, ghost=True)
            if not self._try_escape(ball, t):
                ball.expect = None
                self._end_trapped(ball, t)
            return True
        if exp is not None and abs(t - exp[0]) <= 2.5 * DT and exp[1] is not None:
            self.ti = exp[1] + 1
            self.contacts.append((exp[0], t))
            return False
        if exp is not None and exp[1] is not None and exp[0] < t + float(self.p.min_flight):
            self._loose_note(exp[1])  # touched down too early for this note: it plays on its own
            self.ti = exp[1] + 1
        self.emit("tap", pos.x, pos.y, strength=0.4, ball=ball.id, ghost=exp is not None and exp[1] is None)
        dx, dy = pos.x - self.cx, pos.y - self.cy
        d = math.hypot(dx, dy) or 1.0
        hx, hy = self.cx + dx / d * (d + ball.r), self.cy + dy / d * (d + ball.r)
        self.ripples.append((t, hx, hy, ball.color, 8.0, 50.0, 0.25, 3.0))
        if exp is not None and exp[1] is not None and exp[0] >= t + float(self.p.min_flight):
            if self._aim(ball, exp[0], t):  # still on the way to that note
                return True
        self._plan(ball, t)
        return True

    def _midi_after_note(self, ball: _Ball, t: float) -> None:
        """After a note bounce: plan the next one, or end on the last note."""
        if self.ti < len(self.targets):
            self._plan(ball, t)
            return
        self.final_t = ball.expect[0]
        ball.expect = None
        self.escape_tries = 0
        if self.midi_ending == "escape" and self.p.mode == "grow" and self._try_escape(ball, t):
            return
        self._end_trapped(ball, t)

    def _end_trapped(self, ball: _Ball, t: float) -> None:
        """Pop to fill the ring (the freeze sound is placed on the last note)."""
        if self.p.mode == "grow":
            self.popping = True
            ball.target_r = float(self.p.fill) * self.R + 2.0
        else:
            self._freeze(t, "trapped")

    def _try_escape(self, ball: _Ball, t: float) -> bool:
        """Aim out through the gap so the burst lands on a beat after the last
        note (slow motion stretches the last 1/4 of the flight's final
        ``slowmo`` seconds, so the crossing comes that much earlier). If the
        gap is out of reach from here, take a ghost bounce and try again."""
        slow = 3 * float(self.p.slowmo) / SLOW if float(self.p.slowmo) > 0 else 0.0
        for beats in range(1, 7):
            flight = self.final_t + beats * self.beat - slow - t
            if flight >= 0.25 and self._aim(ball, t + flight, t, escape=True):
                ball.expect = None
                return True
        self.escape_tries += 1
        if self.escape_tries <= 4:
            for flight in (0.3, 0.4, 0.22, 0.5):
                if self._aim(ball, t + flight, t):
                    ball.expect = (t + flight, -1)  # a ghost on the way out
                    return True
        return False

    def _hold_energy(self) -> None:
        """Keep every ball at the target energy. Ball-ball collisions (and growth
        pushing a ball off the wall) would otherwise move energy around: one ball
        could climb past the reach height, another could end up sliding along
        the floor without ever bouncing again. Collisions still change direction."""
        for ball in self.balls:
            if self.midi and ball.hero:
                continue  # choreographed: pure ballistic flights
            body = ball.body
            v = body.velocity
            mag = math.hypot(v.x, v.y)
            if mag < 1.0:
                continue
            target = self._speed_at(body.position.y)
            if target < 120.0:
                continue  # near the top of its arc: leave it to gravity, or it would hover
            if abs(mag - target) > 0.5:
                k = target / mag
                body.velocity = (v.x * k, v.y * k)

    def _spawn(self, t: float) -> bool:
        p, rng = self.p, self.rng
        r = float(p.spawn_radius)
        reach = max(10.0, (self.R - r) * 0.55)
        for _ in range(24):
            ang = rng.uniform(0, 2 * math.pi)
            dist = reach * math.sqrt(rng.random())
            x, y = self.cx + dist * math.cos(ang), self.cy + dist * math.sin(ang)
            if y < self.cy - self.h * 0.5:
                continue  # spawning above the reach height would add energy
            if all(math.hypot(b.body.position.x - x, b.body.position.y - y) > b.r + r + 3
                   for b in self.balls if not b.escaped):
                break
        else:
            return False
        speed = self._speed_at(y)
        va = rng.uniform(0, 2 * math.pi)
        ball = self._add_ball(x, y, speed * math.cos(va), speed * math.sin(va), r)
        self.last_spawn_t = t
        self.emit("spawn", x, y, strength=0.8, ball=ball.id, count=len(self.balls))
        self.ripples.append((t, x, y, ball.color, r * 0.5, r * 3.5, 0.35, 4.0))
        return True

    def _opening(self) -> float:
        """Clear width of the gap between the two rounded ring ends."""
        rc = self.R + self.w / 2
        return 2 * rc * math.sin(math.radians(self.gap / 2)) - self.w

    def _check_escape(self, t: float) -> None:
        """A ball whose center has crossed the ring line (only possible through
        the gap) while moving outwards is through: burst."""
        if self.slow_to is not None and self.t_next < self.slow_to - 1e-9:
            return  # through already, but the burst lands at the end of the slow stretch
        limit = self.R + self.w / 2 + 2.0
        for ball in self.balls:
            pos, v = ball.body.position, ball.body.velocity
            dx, dy = pos.x - self.cx, pos.y - self.cy
            if math.hypot(dx, dy) > limit and dx * v.x + dy * v.y > 0:
                self._escape(ball, t)
                return

    def _check_trapped(self, t: float) -> None:
        p = self.p
        if p.mode == "grow":
            hero = self.balls[0]
            if self.sealed_t is None and 2 * hero.target_r > self._opening():
                self.sealed_t = t
                if not self.midi:  # with a score, every pitch comes from the score
                    self.emit("sealed", hero.body.position.x, hero.body.position.y, strength=0.6)
            if hero.r >= float(p.fill) * self.R:
                self._freeze(t, "trapped")
        else:
            area = sum(b.r * b.r for b in self.balls if not b.escaped)
            if area >= float(p.pack) * self.R * self.R:
                self._freeze(t, "trapped")

    def on_timeout(self) -> None:
        self._freeze(self.t_next, "timeout")

    # ----------------------------------------------------------------- endings
    def _freeze(self, t: float, kind: str) -> None:
        hero = self.balls[0]
        e = self.emit("freeze", hero.body.position.x, hero.body.position.y, strength=1.0, reason=kind)
        if self.midi and self.final_t is not None and kind == "trapped":
            e.t = self.final_t  # the thump and final chord land on the last note; the pop follows it
        self.finish(kind)
        self.ring_body.angular_velocity = 0.0

    def _escape(self, ball: _Ball, t: float) -> None:
        rng = self.rng
        pos = ball.body.position
        self.escape_ball = ball
        ball.escaped = True
        self.hold_timeout = False
        self.emit("escape", pos.x, pos.y, strength=1.0, ball=ball.id, pt=round(t, 6))
        self.finish("escape")
        # Shatter the ring into flying shards.
        theta = math.degrees(self.ring_body.angle)
        self.space.remove(*self.segments)
        self.ring_body.angular_velocity = 0.0
        rc = self.R + self.w / 2
        n = 30
        sweep = (360 - self.gap) / n
        for k in range(n):
            start = theta + self.gap / 2 + k * sweep
            mid = math.radians(start + sweep / 2)
            px, py = self.cx + rc * math.cos(mid), self.cy + rc * math.sin(mid)
            sp = rng.uniform(350, 950)
            self.shards.append(_Shard(start + 0.6, sweep - 1.2, px, py,
                                      math.cos(mid) * sp + rng.uniform(-120, 120),
                                      math.sin(mid) * sp + rng.uniform(-260, 60),
                                      rng.uniform(-420, 420)))
        # Burst at the exit point plus confetti around the ring.
        dx, dy = pos.x - self.cx, pos.y - self.cy
        d = math.hypot(dx, dy) or 1.0
        ux, uy = dx / d, dy / d
        for _ in range(140):
            a = math.atan2(uy, ux) + rng.gauss(0, 0.9)
            sp = rng.uniform(250, 1500)
            self.sparks.append(_Spark(pos.x, pos.y, math.cos(a) * sp, math.sin(a) * sp, t,
                                      rng.uniform(0.6, 1.9), rng.uniform(3, 9),
                                      self.palette.accent(rng.randrange(12)), drag=1.6, grav=900,
                                      glow=rng.random() < 0.3))
        for _ in range(90):
            a = rng.uniform(0, 2 * math.pi)
            sp = rng.uniform(150, 700)
            x, y = self.cx + rc * math.cos(a), self.cy + rc * math.sin(a)
            self.sparks.append(_Spark(x, y, math.cos(a) * sp, math.sin(a) * sp, t,
                                      rng.uniform(0.5, 1.5), rng.uniform(2, 6),
                                      self.palette.accent(rng.randrange(12)), drag=2.0, grav=700))
        self.ripples.append((t, pos.x, pos.y, (255, 255, 255), 10.0, 520.0, 0.9, 14.0))
        self.ripples.append((t, self.cx, self.cy, ball.color, self.R, self.R + 420.0, 0.8, 10.0))

    # -------------------------------------------------------------- particles
    def _sparks_at(self, x, y, nx, ny, color, t, count):
        rng = self.rng
        tx, ty = -ny, nx
        for _ in range(count):
            along = rng.uniform(-1, 1)
            sp = rng.uniform(150, 420)
            vx = (-nx * 0.8 + tx * along) * sp
            vy = (-ny * 0.8 + ty * along) * sp
            self.sparks.append(_Spark(x, y, vx, vy, t, rng.uniform(0.2, 0.45), rng.uniform(2, 4.5), color,
                                      drag=4.0))

    def _update_particles(self, dt: float, t: float) -> None:
        alive = []
        for s in self.sparks:
            if t - s.t0 > s.life:
                continue
            k = math.exp(-s.drag * dt)
            s.vx *= k
            s.vy = s.vy * k + s.grav * dt
            s.x += s.vx * dt
            s.y += s.vy * dt
            alive.append(s)
        self.sparks = alive
        self.ripples = [r for r in self.ripples if t - r[0] <= r[6]]
        for sh in self.shards:
            sh.vy += self.g * 0.6 * dt
            sh.dx += sh.vx * dt
            sh.dy += sh.vy * dt
            sh.rot += sh.vrot * dt

    # ---------------------------------------------------------------- snapshot
    def snapshot(self) -> Scene:
        """Describe the current state. Must not change any simulation state."""
        p, pal, t = self.p, self.palette, self.t
        items: list = []
        end_age = (t - self.end_t) if self.end_t is not None else -1.0
        ending = self.timeline.ending

        # ring
        rc = self.R + self.w / 2
        if not self.shards:
            theta = math.degrees(self.ring_body.angle)
            flash = math.exp(-(t - self.last_bounce_t) / 0.16) if self.bounces else 0.0
            danger = 0.0
            if p.mode == "grow":
                # builds as the ball's diameter approaches the gap width; full once it can't fit
                danger = _smooth((2 * self.balls[0].r / max(1.0, self._opening()) - 0.55) / 0.45)
            color = mix(pal.ring, self.last_color, 0.75 * flash * (1 - 0.6 * danger))
            color = mix(color, DANGER, 0.75 * danger)
            items.append(Arc(self.cx, self.cy, rc, self.w, theta + self.gap / 2, 360 - self.gap,
                             color, glow=0.35 + 0.65 * flash))
        else:
            fade = max(0.0, 1.0 - end_age / 1.6)
            for sh in self.shards:
                items.append(Arc(self.cx, self.cy, rc, self.w, sh.start, sh.sweep, pal.ring,
                                 alpha=fade, glow=0.4 * fade, rot=sh.rot, px=sh.px, py=sh.py,
                                 dx=sh.dx, dy=sh.dy))

        # ripples
        for (t0, x, y, color, r0, r1, dur, width) in self.ripples:
            k = (t - t0) / dur
            if 0.0 <= k <= 1.0:
                items.append(Ripple(x, y, r0 + (r1 - r0) * _ease_out(k), width * (1 - 0.7 * k), color,
                                    alpha=0.85 * (1 - k) ** 2))

        # sparks
        for s in self.sparks:
            k = (t - s.t0) / s.life
            if 0.0 <= k <= 1.0:
                items.append(Particle(s.x, s.y, s.r * (1 - 0.5 * k), s.color, alpha=(1 - k) ** 1.5, glow=s.glow))

        # balls
        many = len(self.balls) > 12
        for ball in self.balls:
            pos = ball.body.position
            if pos.y > 2400:
                continue
            r = ball.r
            age = t - ball.born
            if not ball.hero and age < 0.15:
                r *= _ease_out(age / 0.15)
            hit_t = ball.hit_t
            if ball.hero and self.loose:
                j = bisect.bisect_right(self.loose, t + 1e-9) - 1
                if j >= 0:
                    hit_t = max(hit_t, self.loose[j])
            hit = math.exp(-(t - hit_t) / 0.12) if hit_t >= 0 else 0.0
            items.append(Circle(pos.x, pos.y, r, ball.color,
                                glow=(0.75 if ball.hero else 0.35) + 0.25 * hit,
                                outline=(255, 255, 255) if hit > 0.05 or (ball.hero and many) else None,
                                outline_width=3.0 + 4.0 * hit if hit > 0.05 else 3.0,
                                id=ball.id, trail=float(p.trail) if (ball.hero or not many) else 0.0))

        fx = Effects(zoom_x=self.cx, zoom_y=self.cy)
        if self.slow_from is not None and t >= self.slow_from - 1e-9:
            # push in on the gap while time slows, ease back out after the burst
            k = min(1.0, (t - self.slow_from) / max(1e-6, self.slow_to - self.slow_from))
            if end_age >= 0:
                k = 1.0 - _ease_out(end_age / 0.35)
            gap_dir = self.ring_body.angle  # the ring stops turning once it shatters
            # zooming about the gap itself keeps the gap (and the ball in it) in frame
            fx.zoom = 1.0 + 0.08 * _smooth(k)
            fx.zoom_x = self.cx + (self.R + self.w / 2) * math.cos(gap_dir)
            fx.zoom_y = self.cy + (self.R + self.w / 2) * math.sin(gap_dir)
            fx.vignette = 0.3 * k
        if end_age >= 0:
            if ending == "escape":
                fx.flash = 0.85 * math.exp(-end_age / 0.12)
                amp = 16.0 * math.exp(-end_age / 0.22)
                fx.shake_x = amp * math.sin(end_age * 97.0)
                fx.shake_y = amp * math.cos(end_age * 83.0)
            else:
                fx.flash = 0.7 * math.exp(-end_age / 0.1)
                k = min(1.0, end_age / 0.45)
                fx.desaturate = 0.8 * k
                fx.tint, fx.tint_amount = (190, 225, 255), 0.18 * k
                fx.zoom = 1.0 + 0.05 * _ease_out(end_age / max(0.1, self.outro))
                fx.vignette = 0.35 * k

        overlay: list = []
        if p.counter:
            value = self.bounces if p.mode == "grow" else len(self.balls)
            caption = "BOUNCES" if p.mode == "grow" else "BALLS"
            y = self.cy + self.R0 + self.w + 56  # just under the ring, above the Shorts caption area
            pulse = math.exp(-(t - self.last_bounce_t) / 0.1) if self.bounces else 0.0
            overlay.append(Label(str(value), self.cx, y, 72 * (1 + 0.08 * pulse), pal.text, weight="black",
                                 stroke=pal.text_stroke, stroke_width=8))
            overlay.append(Label(caption, self.cx, y + 46, 26, pal.text, alpha=0.75, weight="bold"))
        if end_age >= 0:
            text = p.escape_text if ending == "escape" else p.trapped_text
            if text:
                k = min(1.0, end_age / 0.35)
                pop = 1.0 + 0.25 * math.sin(math.pi * k) * (1 - k) if k < 1 else 1.0
                color = self.balls[0].color if ending == "escape" else pal.text
                overlay.append(Label(text, self.cx, self.cy, 120 * _ease_out(k) * pop, color, weight="black",
                                     stroke=pal.text_stroke, stroke_width=14))
        return Scene(items=items, fx=fx, overlay=overlay)
