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
``first_bounce`` seconds. Once the ball can no longer fit through the gap
(sealed) it stops being re-energised: each bounce loses energy, so it settles
like a dropped ball (bounces lower and closer together), its speed is capped
by the free room, and it comes to rest at the bottom while it grows, touching
the ring all round - and freezing - ``seal_fill`` seconds after sealing. An escape plays ``slowmo`` seconds of quarter-speed slow motion as the
ball passes the gap, timed from the rehearsal, then bursts; a camera pan and
zoom, also planned from the rehearsal, keep the escaping ball inside the frame
and out of the Shorts UI.
"""

from __future__ import annotations

import math

import pymunk

from collections import deque

from .. import SAFE_BOTTOM, SAFE_RIGHT
from ..palettes import mix
from ..scene import Arc, Circle, Effects, Label, Particle, Ripple, Scene
from ..timeline import Event
from .base import PHYSICS_HZ, Simulation

BALL, RING = 1, 2
GROW_AUTO = 55.0  # grow = GROW_AUTO / seconds of action (calibrated for the default ring)
SLOW = 4  # video ticks per physics step during slow motion (quarter speed)
TRACE_STEPS = 40  # rehearsal keeps this many recent positions per ball (covers the slow stretch)
CAM_LEAD = 0.35  # seconds the escape camera takes to pan into place before the slow motion
FRAME_MARGIN = 16.0  # px the escaping ball keeps from the frame / Shorts UI edges
HOOK_CLEAR = 440.0  # px from the top: keep the escaping ball below the hook text
# After "sealed" the ball settles like a dropped ball, then grows into the ring.
SETTLE_DROP = 0.015  # restitution lost per bounce while settling (down to the floor in settle_restitution)
SETTLE_TANGENT = 0.8  # share of sideways speed kept per bounce while settling
ROOM_FRAMES = 4  # while settling, never move more than 1/ROOM_FRAMES of the free room per 60 fps frame
REST_SPEED = 90.0  # px/s: a rebound slower than this means the ball has settled
REST_ROOM = 14.0  # px: with less free room than this the ball rests regardless
REST_FOLLOW = 0.06  # s: time constant of the glide onto the resting point
ROLL_DAMP = 0.35  # s: speed decay time constant while the settling ball rolls along the wall
SIDE_DRAG = 0.2  # s: decay time of sideways speed while settling, so it comes to bounce near the bottom
POP = 0.3  # seconds: after the burst the escaped ball brakes and pops (fades out, swelling)
POP_BRAKE = 0.08  # velocity decay time constant (s) while it pops; travel = speed * this
POP_SWELL = 0.25  # how much the ball swells as it pops
DANGER = (255, 60, 80)  # ring tint as the ball outgrows the gap


class _Ball:
    __slots__ = ("id", "body", "shape", "r", "target_r", "color", "hero", "born",
                 "last_wall", "pending", "hit_t", "escaped", "touch_since", "touch_seen", "trace")

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
        self.trace: deque | None = None  # recent positions (rehearsal only, to frame the escape)


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
        "ring_radius": 407.0,  # inner radius at t=0 (px); the ring spans ~78% of the width
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
        "seal_fill": 2.5,  # grow mode: once sealed, the ball touches the ring all round this many seconds later
        "settle_restitution": [0.70, 0.60],  # once sealed: first bounce, floor (each bounce loses a little more)
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
    }

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
        self.sealed_r = 0.0
        self.settle_n = 0  # bounces since sealing
        self.resting = False  # sealed ball has settled at the bottom
        self.escape_vel = (0.0, 0.0)
        self.escape_ball: _Ball | None = None
        self.color_i = 0

        self._add_ball(*self._launch(), float(p.ball_radius), hero=True)

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
        # Camera for the escape: the rehearsal recorded where the ball travels
        # during the slow stretch; pan/zoom so all of it stays in the safe area.
        self.cam: tuple[float, float, float] | None = None
        if self.slow_from is not None:
            box = self.plan.of_kind("escape")[0].data.get("box")
            if box:
                self.cam = self._frame_escape(box)

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
            if self._settling:
                self._settle_grow(t)
            else:
                for ball in self.balls:
                    if ball.r < ball.target_r:
                        ball.r = min(ball.target_r, ball.r + float(p.grow_speed) * dt)
                        ball.shape.unsafe_set_radius(ball.r)

        self.space.step(dt)

        if not self.ended:
            if not self._settling:
                self._unstick(t)
            self._resolve_bounces(t)
            self._hold_energy()
            if self._settling:
                self._settle_motion(t, dt)
            self._check_escape(t)
            if not self.ended:
                self._check_trapped(t)

        self._update_particles(dt, t)
        if self.escape_ball is not None:
            body, (vx, vy) = self.escape_ball.body, self.escape_vel
            body.position = (body.position.x + vx * dt, body.position.y + vy * dt)
            k = math.exp(-dt / POP_BRAKE)
            self.escape_vel = (vx * k, vy * k)
        if self.plan is None and not self.ended:
            for ball in self.balls:  # rehearsal: remember recent positions
                if ball.trace is None:
                    ball.trace = deque(maxlen=TRACE_STEPS)
                ball.trace.append((ball.body.position.x, ball.body.position.y))

    def _resolve_bounces(self, t: float) -> None:
        p = self.p
        spawned_this_step = False
        for ball in list(self.balls):
            if ball.pending <= 0.0:
                continue
            approach, ball.pending = ball.pending, 0.0
            body = ball.body
            if self._settling and ball.hero:
                self._settle_bounce(ball)
            else:
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
            self.emit("bounce", hx, hy, strength=min(1.0, approach / ref),
                      ball=ball.id, n=self.bounces, r=round(ball.r, 2), hero=ball.hero)
            if ball.hero:
                self.ripples.append((t, hx, hy, ball.color, 14.0, 120.0, 0.45, 7.0))
            else:
                self.ripples.append((t, hx, hy, ball.color, 6.0, 42.0, 0.25, 3.0))
            self._sparks_at(hx, hy, dx / d, dy / d, ball.color, t, count=7 if ball.hero else 2)

            if p.mode == "grow":
                if self.sealed_t is None or float(p.seal_fill) <= 0:
                    cap = self.R * float(p.fill) + 2.0
                    ball.target_r = min(cap, ball.target_r + self.grow)
            elif not spawned_this_step and len(self.balls) < int(p.max_balls):
                if (p.spawn_from == "hero" and ball.hero) or (
                    p.spawn_from == "any" and t - self.last_spawn_t >= float(p.spawn_cooldown)
                ):
                    spawned_this_step = self._spawn(t)

    # ------------------------------------------------------------ settling
    @property
    def _settling(self) -> bool:
        """Sealed (grow mode): the ball settles and grows into the ring."""
        return self.sealed_t is not None and float(self.p.seal_fill) > 0 and self.p.mode == "grow"

    def _settle_grow(self, t: float) -> None:
        """Radius schedule from sealing to contact: room = R - r reaches 0
        exactly ``seal_fill`` seconds after sealing, growing a little faster
        towards the end."""
        hero = self.balls[0]
        k = min(1.0, (t - self.sealed_t) / float(self.p.seal_fill))
        r = self.sealed_r + (self.R - self.sealed_r) * k ** 1.5
        hero.r = hero.target_r = max(hero.r, min(r, self.R))
        if not self.resting:
            hero.shape.unsafe_set_radius(hero.r)

    def _settle_bounce(self, ball: _Ball) -> None:
        """Lose energy on a bounce like a dropped ball: restitution on the speed
        into the wall, plus damping of the sideways speed so the ball works its
        way to the bottom. Bounces get lower and closer together."""
        first, floor = (float(v) for v in self.p.settle_restitution)
        e = max(floor, first - SETTLE_DROP * self.settle_n)
        self.settle_n += 1
        body = ball.body
        nx, ny = body.position.x - self.cx, body.position.y - self.cy
        d = math.hypot(nx, ny) or 1.0
        nx, ny = nx / d, ny / d
        v = body.velocity
        vn = v.x * nx + v.y * ny  # normal part (inwards after the bounce)
        tx, ty = v.x - vn * nx, v.y - vn * ny
        body.velocity = (e * vn * nx + SETTLE_TANGENT * tx, e * vn * ny + SETTLE_TANGENT * ty)

    def _start_rest(self) -> None:
        if self.resting:
            return
        self.resting = True
        hero = self.balls[0]
        hero.body.velocity = (0.0, 0.0)
        self.space.remove(hero.body, hero.shape)  # from here on its motion is the resting glide

    def _settle_motion(self, t: float, dt: float) -> None:
        """Cap speed by the free room and damp rolling contact; once the ball
        can't lift off any more, rest it on the bottom of the ring (its center
        rises as it grows) without ever overlapping the ring."""
        hero = self.balls[0]
        body = hero.body
        room = max(0.0, self.R - hero.r)
        x, y = body.position
        if not self.resting:
            v = body.velocity
            v = (v.x * math.exp(-dt / SIDE_DRAG), v.y)  # sideways swing dies out: it drops to the bottom
            if hero.touch_seen == t:  # rolling/sliding along the wall loses energy too
                k = math.exp(-dt / ROLL_DAMP)
                v = (v[0] * k, v[1] * k)
            mag = math.hypot(v[0], v[1])
            cap = room * 60.0 / ROOM_FRAMES
            if mag > cap:
                v = (v[0] * cap / mag, v[1] * cap / mag)
                mag = cap
            # Growth outpaces pymunk's soft overlap correction, so keep the ball
            # touching the ring at most (and drop any speed into it).
            dx, dy = x - self.cx, y - self.cy
            d = math.hypot(dx, dy)
            if d > room and d > 0:
                nx, ny = dx / d, dy / d
                x, y = self.cx + nx * room, self.cy + ny * room
                body.position = (x, y)
                vn = v[0] * nx + v[1] * ny
                if vn > 0:
                    v = (v[0] - vn * nx, v[1] - vn * ny)
                    mag = math.hypot(v[0], v[1])
            body.velocity = v
            lift = 0.5 * mag * mag + self.g * (self.cy + room - y)  # energy above the resting point
            if room >= REST_ROOM and lift > 0.5 * REST_SPEED * REST_SPEED:
                return
            self._start_rest()
        a = 1.0 - math.exp(-dt / REST_FOLLOW)
        nx, ny = x + (self.cx - x) * a, y + (self.cy + room - y) * a  # glide to the bottom of the ring
        step_cap = max(room, 1.0) / ROOM_FRAMES * 60.0 * dt
        dx, dy = nx - x, ny - y
        dd = math.hypot(dx, dy)
        if dd > step_cap:
            nx, ny = x + dx * step_cap / dd, y + dy * step_cap / dd
        dx, dy = nx - self.cx, ny - self.cy
        d = math.hypot(dx, dy)
        if d > room:  # touching the wall at most, never through it
            nx, ny = (self.cx + dx * room / d, self.cy + dy * room / d) if d > 0 else (self.cx, self.cy)
        body.position = (nx, ny)

    def _hold_energy(self) -> None:
        """Keep every ball at the target energy. Ball-ball collisions (and growth
        pushing a ball off the wall) would otherwise move energy around: one ball
        could climb past the reach height, another could end up sliding along
        the floor without ever bouncing again. Collisions still change direction."""
        for ball in self.balls:
            if ball.hero and self._settling:
                continue  # sealed: it loses energy now
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

    def _escape_box(self, ball: _Ball) -> list[float]:
        """Area the escaping ball covers during the slow stretch: its last
        positions (the stretch starts at most this far before the crossing)
        plus a short ballistic extrapolation (the burst lands up to one frame
        grid after the crossing)."""
        dt = 1.0 / PHYSICS_HZ
        steps = round(float(self.p.slowmo) / SLOW * PHYSICS_HZ)
        pos, v = ball.body.position, ball.body.velocity
        pts = list(ball.trace)[-steps:] + [(pos.x, pos.y)]
        x, y = pos.x, pos.y
        for i in range(1, 17):
            tau = i * dt
            pts.append((x + v.x * tau, y + v.y * tau + 0.5 * self.g * tau * tau))
        x, y = pts[-1]  # then it brakes to a stop while popping (and swelling)
        pts.append((x + v.x * POP_BRAKE, y + v.y * POP_BRAKE))
        r = ball.r * (1.0 + POP_SWELL)
        return [round(min(px for px, _ in pts) - r, 1), round(min(py for _, py in pts) - r, 1),
                round(max(px for px, _ in pts) + r, 1), round(max(py for _, py in pts) + r, 1)]

    def _frame_escape(self, box: list[float]) -> tuple[float, float, float]:
        """(zoom, pan_x, pan_y) that keep ``box`` inside the frame, out of the
        Shorts UI (right 12%, bottom 20%) and below the hook, at every zoom from
        1 up to the returned one. Zoom is about the ring center; prefers the
        smallest pan."""
        x0, y0, x1, y1 = box
        limits = ((x0, x1, self.cx, FRAME_MARGIN, SAFE_RIGHT - FRAME_MARGIN),
                  (y0, y1, self.cy, HOOK_CLEAR, SAFE_BOTTOM - FRAME_MARGIN))
        for zoom in (1.06, 1.03, 1.0):
            pans = []
            for a, b, c, lo_lim, hi_lim in limits:
                lo = max(lo_lim - (s * (a - c) + c) for s in (1.0, zoom))
                hi = min(hi_lim - (s * (b - c) + c) for s in (1.0, zoom))
                if lo > hi:
                    break
                pans.append(min(max(0.0, lo), hi))
            else:
                return zoom, pans[0], pans[1]
        # too big to fit (should not happen): center it in the safe area
        return 1.0, (limits[0][3] + limits[0][4] - x0 - x1) / 2, (limits[1][3] + limits[1][4] - y0 - y1) / 2

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
            pos = hero.body.position
            inside = math.hypot(pos.x - self.cx, pos.y - self.cy) + hero.r <= self.R + 2.0  # not mid-gap
            if self.sealed_t is None and inside and 2 * hero.r > self._opening():  # it can't fit any more
                self.sealed_t = t
                self.sealed_r = hero.r
                self.emit("sealed", hero.body.position.x, hero.body.position.y, strength=0.6)
            if self._settling:
                if self.R - hero.r <= 0.5:  # touching the ring all round
                    self._freeze(t, "trapped")
            elif hero.r >= float(p.fill) * self.R:
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
        self.emit("freeze", hero.body.position.x, hero.body.position.y, strength=1.0, reason=kind)
        self.finish(kind)
        self.ring_body.angular_velocity = 0.0

    def _escape(self, ball: _Ball, t: float) -> None:
        rng = self.rng
        pos = ball.body.position
        self.escape_ball = ball
        ball.escaped = True
        # It brakes hard and pops into the confetti instead of flying off-screen.
        self.escape_vel = (ball.body.velocity.x, ball.body.velocity.y)
        self.space.remove(ball.body, ball.shape)
        self.hold_timeout = False
        data = {"ball": ball.id, "pt": round(t, 6), "r": round(ball.r, 2)}
        if ball.trace:
            data["box"] = self._escape_box(ball)
        self.emit("escape", pos.x, pos.y, strength=1.0, **data)
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
            alpha = 1.0
            age = t - ball.born
            if not ball.hero and age < 0.15:
                r *= _ease_out(age / 0.15)
            if ball is self.escape_ball and end_age >= 0:
                k = end_age / POP
                if k >= 1.0:
                    continue
                r *= 1.0 + POP_SWELL * _ease_out(k)
                alpha = (1.0 - k) ** 1.5
            hit = math.exp(-(t - ball.hit_t) / 0.12) if ball.hit_t >= 0 else 0.0
            items.append(Circle(pos.x, pos.y, r, ball.color, alpha=alpha,
                                glow=(0.75 if ball.hero else 0.35) + 0.25 * hit,
                                outline=(255, 255, 255) if hit > 0.05 or (ball.hero and many) else None,
                                outline_width=3.0 + 4.0 * hit if hit > 0.05 else 3.0,
                                id=ball.id, trail=float(p.trail) if (ball.hero or not many) else 0.0))

        fx = Effects(zoom_x=self.cx, zoom_y=self.cy)
        if self.cam is not None and t >= self.slow_from - CAM_LEAD - 1e-9:
            # pan into position just before the slow stretch, push in during it,
            # hold through the burst, then ease back
            zoom, pan_x, pan_y = self.cam
            if end_age >= 0:  # hold until the escaped ball has popped, then ease back
                pan = push = 1.0 - _ease_out((end_age - POP) / 0.45) if end_age > POP else 1.0
            else:
                pan = _smooth((t - (self.slow_from - CAM_LEAD)) / CAM_LEAD)
                push = _smooth((t - self.slow_from) / max(1e-6, self.slow_to - self.slow_from))
            fx.pan_x, fx.pan_y = pan_x * pan, pan_y * pan
            fx.zoom = 1.0 + (zoom - 1.0) * push
            fx.vignette = 0.3 * push
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
