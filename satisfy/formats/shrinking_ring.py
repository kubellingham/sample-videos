"""shrinking_ring: a ball bounces inside a slowly rotating, shrinking ring with a gap.

Each bounce plays the next note and either grows the ball (``mode: grow``) or
spawns a new ball (``mode: spawn``). The video ends with

* an escape burst - a ball slips out through the gap and the ring shatters, or
* a trapped freeze - the ball fills the ring (grow) or the ring packs full
  (spawn), or time runs out.

Balls keep a constant mechanical energy (they are re-energised on every wall
bounce), so ``energy`` sets how high up the ring they can reach. With the gap
starting near the top, the ring's rotation and shrink decide when the gap comes
within reach; ball growth decides whether the ball still fits through it.
"""

from __future__ import annotations

import math

import pymunk

from ..palettes import mix
from ..scene import Arc, Circle, Effects, Label, Particle, Ripple, Scene
from .base import Simulation

BALL, RING = 1, 2


class _Ball:
    __slots__ = ("id", "body", "shape", "r", "target_r", "color", "hero", "born",
                 "last_wall", "pending", "hit_t", "escaped", "touch_since", "touch_seen")

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


class ShrinkingRing(Simulation):
    name = "shrinking_ring"
    description = "Ball bounces in a rotating, shrinking ring with a gap; grows or multiplies per bounce."
    defaults = {
        "mode": "grow",  # grow | spawn
        "center": [540, 1010],
        "ring_radius": 380.0,  # inner radius at t=0 (px)
        "ring_min_radius": 240.0,
        "ring_shrink": 3.0,  # px per second
        "ring_width": 14.0,
        "gap": 62.0,  # gap size (degrees)
        "spin": [1.0, 4.0],  # rotation speed range (deg/s); direction is random
        "gap_start": [-125.0, -55.0],  # initial gap angle range (deg; -90 is straight up)
        "gravity": 4000.0,
        "energy": [90.0, 150.0],  # how far above the ring center balls can reach (px)
        "ball_radius": 22.0,
        "grow": "auto",  # px added to the radius per bounce (grow mode); auto scales with duration
        "grow_speed": 120.0,  # max visual growth rate (px/s)
        "fill": 0.93,  # grow mode: trapped once ball radius >= fill * ring radius
        "spawn_radius": 15.0,  # spawn mode: radius of new balls
        "spawn_from": "hero",  # hero: only the first ball's bounces spawn | any
        "spawn_cooldown": 0.15,  # spawn mode with spawn_from=any (s)
        "max_balls": 160,
        "pack": 0.42,  # spawn mode: trapped once balls cover this share of the ring
        "trail": 0.16,  # trail length (s)
        "counter": True,  # bounce / ball counter under the ring
        "escape_text": "ESCAPED!",  # shown after an escape ("" to hide)
        "trapped_text": "TRAPPED!",  # shown after a trapped freeze ("" to hide)
        "outro": 2.2,  # seconds after the ending
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
        angle0 = rng.uniform(*p.gap_start)
        if p.grow == "auto":
            # Calibrated so a ball that never escapes fills the ring at ~85% of the duration.
            self.grow = 2.2 * 30.0 / max(5.0, 0.85 * (self.cfg.duration - self.outro))
        else:
            self.grow = float(p.grow)

        space = pymunk.Space()
        space.gravity = (0.0, self.g)
        space.iterations = 20
        self.space = space

        body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        body.position = (self.cx, self.cy)
        body.angle = math.radians(angle0)
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

        # First ball starts at the center already moving (motion from frame 1),
        # aimed downwards so the first bounce lands quickly.
        ang = math.radians(rng.uniform(25, 155))
        speed = self._speed_at(self.cy)
        r = float(p.ball_radius)
        self._add_ball(self.cx, self.cy, speed * math.cos(ang), speed * math.sin(ang), r, hero=True)

        space.on_collision(BALL, RING, begin=self._on_wall, pre_solve=self._on_wall_touch,
                           separate=self._on_wall_separate)
        space.on_collision(BALL, BALL, begin=self._on_ball)

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
            self.R = max(float(p.ring_min_radius), self.R0 - float(p.ring_shrink) * t)
            self._set_ring_radius(self.R)

        # grow mode: ease the radius towards its target
        if p.mode == "grow" and not self.ended:
            for ball in self.balls:
                if ball.r < ball.target_r:
                    ball.r = min(ball.target_r, ball.r + float(p.grow_speed) * dt)
                    ball.shape.unsafe_set_radius(ball.r)

        self.space.step(dt)

        if not self.ended:
            self._unstick(t)
            self._resolve_bounces(t)
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
                cap = self.R * float(p.fill) + 2.0
                ball.target_r = min(cap, ball.target_r + self.grow)
            elif not spawned_this_step and len(self.balls) < int(p.max_balls):
                if (p.spawn_from == "hero" and ball.hero) or (
                    p.spawn_from == "any" and t - self.last_spawn_t >= float(p.spawn_cooldown)
                ):
                    spawned_this_step = self._spawn(t)

    def _hold_energy(self) -> None:
        """Keep every ball at the target energy. Ball-ball collisions (and growth
        pushing a ball off the wall) would otherwise move energy around: one ball
        could climb past the reach height, another could end up sliding along
        the floor without ever bouncing again. Collisions still change direction."""
        for ball in self.balls:
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
        limit = self.R + self.w + 1.0
        for ball in self.balls:
            pos = ball.body.position
            if math.hypot(pos.x - self.cx, pos.y - self.cy) - ball.r > limit:
                self._escape(ball, t)
                return

    def _check_trapped(self, t: float) -> None:
        p = self.p
        if p.mode == "grow":
            hero = self.balls[0]
            if self.sealed_t is None and 2 * hero.target_r > self._opening():
                self.sealed_t = t
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
        self.emit("freeze", hero.body.position.x, hero.body.position.y, strength=1.0, reason=kind)
        self.finish(kind)
        self.ring_body.angular_velocity = 0.0

    def _escape(self, ball: _Ball, t: float) -> None:
        rng = self.rng
        pos = ball.body.position
        self.escape_ball = ball
        ball.escaped = True
        self.emit("escape", pos.x, pos.y, strength=1.0, ball=ball.id)
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
            color = mix(pal.ring, self.last_color, 0.75 * flash)
            if self.sealed_t is not None and p.mode == "grow":
                color = mix(color, (255, 70, 90), 0.35 * min(1.0, (t - self.sealed_t) / 0.6))
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
            hit = math.exp(-(t - ball.hit_t) / 0.12) if ball.hit_t >= 0 else 0.0
            items.append(Circle(pos.x, pos.y, r, ball.color,
                                glow=(0.75 if ball.hero else 0.35) + 0.25 * hit,
                                outline=(255, 255, 255) if hit > 0.05 or (ball.hero and many) else None,
                                outline_width=3.0 + 4.0 * hit if hit > 0.05 else 3.0,
                                id=ball.id, trail=float(p.trail) if (ball.hero or not many) else 0.0))

        fx = Effects(zoom_x=self.cx, zoom_y=self.cy)
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
            y = self.cy + self.R0 + 135
            pulse = math.exp(-(t - self.last_bounce_t) / 0.1) if self.bounces else 0.0
            overlay.append(Label(str(value), self.cx, y, 86 * (1 + 0.08 * pulse), pal.text, weight="black",
                                 stroke=pal.text_stroke, stroke_width=8))
            overlay.append(Label(caption, self.cx, y + 58, 30, pal.text, alpha=0.75, weight="bold"))
        if end_age >= 0:
            text = p.escape_text if ending == "escape" else p.trapped_text
            if text:
                k = min(1.0, end_age / 0.35)
                pop = 1.0 + 0.25 * math.sin(math.pi * k) * (1 - k) if k < 1 else 1.0
                color = self.balls[0].color if ending == "escape" else pal.text
                overlay.append(Label(text, self.cx, self.cy, 120 * _ease_out(k) * pop, color, weight="black",
                                     stroke=pal.text_stroke, stroke_width=14))
        return Scene(items=items, fx=fx, overlay=overlay)
