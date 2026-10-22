// Motion of the type: the slam-in, the beat punch and the shake. Pure
// functions of time, shared by the composition and the safe-zone check, so
// the layout can reserve exactly the room the motion needs.

export const WIDTH = 1080;
export const HEIGHT = 1920;
export const FPS = 60;

/** Where text may go, as fractions of the frame: never in the bottom 20% or the right 12% (Shorts UI). */
export const SAFE = { left: 0.04, right: 0.88, top: 0.06, bottom: 0.8 };
/** The text block's centre: midway between the safe edges (a touch left of the frame's centre). */
export const CENTER = { x: (SAFE.left + SAFE.right) / 2, y: 0.42 };

export const SLAM = {
  from: 1.22, // start scale
  rotate: 6, // start rotation (deg), alternating sides; settles to `settle`
  settle: 1.5,
  emphasisBoost: 1.12, // emphasised chunks slam from further out and turn more
  stiffness: 220,
  damping: 13,
  mass: 0.6,
};
export const PUNCH = { downbeat: 0.05, beat: 0.025, decay: 0.12 };
export const SHAKE = { downbeat: 8, beat: 4.5, decay: 0.09, hz: 21 };
export const EXIT_FRAMES = 6;

/** Largest scale and rotation the type can reach, and how far the shake can push it. */
export const motionEnvelope = (emphasis: boolean) => {
  const boost = emphasis ? SLAM.emphasisBoost : 1;
  return {
    scale: (1 + (SLAM.from - 1) * boost) * (1 + PUNCH.downbeat),
    rotateDeg: SLAM.rotate * boost,
    shakePx: SHAKE.downbeat,
  };
};

/** Damped spring 0 -> 1 (same model as Remotion's spring()). */
export const springAt = (tSec: number, stiffness = SLAM.stiffness, damping = SLAM.damping, mass = SLAM.mass) => {
  if (tSec <= 0) return 0;
  const w0 = Math.sqrt(stiffness / mass);
  const zeta = damping / (2 * Math.sqrt(stiffness * mass));
  if (zeta < 1) {
    const wd = w0 * Math.sqrt(1 - zeta * zeta);
    return 1 - Math.exp(-zeta * w0 * tSec) * (Math.cos(wd * tSec) + ((zeta * w0) / wd) * Math.sin(wd * tSec));
  }
  return 1 - Math.exp(-w0 * tSec) * (1 + w0 * tSec);
};

/** Slam-in of a chunk `tSec` after its start: scale and rotation (deg). */
export const slam = (tSec: number, index: number, emphasis: boolean) => {
  const boost = emphasis ? SLAM.emphasisBoost : 1;
  const s = springAt(tSec);
  const side = index % 2 === 0 ? -1 : 1;
  return {
    scale: 1 + (SLAM.from - 1) * boost * (1 - s),
    rotate: side * (SLAM.settle + (SLAM.rotate * boost - SLAM.settle) * (1 - s)),
    opacity: Math.min(1, tSec / 0.05), // 3 frames at 60 fps
  };
};

const lastAtOrBefore = (sorted: number[], t: number) => {
  let lo = 0;
  let hi = sorted.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] <= t + 1e-9) {
      ans = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return ans;
};

/** Hash-based pseudo random in [0, 1) (deterministic per beat). */
const rand = (n: number) => {
  const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
};

/** Zoom punch and shake from the most recent beat at time `t` (seconds). */
export const beatKick = (t: number, beats: number[], downbeats: Set<number>) => {
  const i = lastAtOrBefore(beats, t);
  if (i < 0) return { zoom: 1, x: 0, y: 0, glow: 0 };
  const age = t - beats[i];
  const down = downbeats.has(i);
  const fall = Math.exp(-age / PUNCH.decay);
  const shake = (down ? SHAKE.downbeat : SHAKE.beat) * Math.exp(-age / SHAKE.decay) * Math.cos(2 * Math.PI * SHAKE.hz * age);
  const angle = rand(i) * 2 * Math.PI;
  return {
    zoom: 1 + (down ? PUNCH.downbeat : PUNCH.beat) * fall,
    x: shake * Math.cos(angle),
    y: shake * Math.sin(angle),
    glow: (down ? 1 : 0.5) * Math.exp(-age / 0.25),
  };
};
