"""Numpy synthesis primitives: instrument voices, mixer, reverb, limiter.

Everything is generated at 48 kHz. Voices start exactly at their first sample
(no pre-delay), so an event placed on a frame boundary is heard on that frame.
"""

from __future__ import annotations

import numpy as np

SR = 48_000


def _t(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR)) / SR


def _attack(t: np.ndarray, seconds: float) -> np.ndarray:
    if seconds <= 0:
        return np.ones_like(t)
    return np.minimum(1.0, t / seconds)


def _bar(freq: float, vel: float, partials, dur: float, attack: float = 0.0015,
         decay_scale: float = 1.0) -> np.ndarray:
    """Sum of exponentially decaying sine partials: (ratio, amplitude, decay seconds)."""
    t = _t(dur)
    out = np.zeros_like(t)
    # higher notes ring shorter, like real bars and tines
    pitch_k = (440.0 / max(freq, 20.0)) ** 0.35 * decay_scale
    nyquist = SR / 2 * 0.9
    for ratio, amp, decay in partials:
        f = freq * ratio
        if f >= nyquist:
            continue
        out += amp * np.sin(2 * np.pi * f * t) * np.exp(-t / (decay * pitch_k))
    out *= _attack(t, attack)
    return out * vel


def marimba(freq: float, vel: float = 1.0) -> np.ndarray:
    # tuned bar: partials at 1 : 4 : 10, the upper ones die quickly
    sig = _bar(freq, vel, [(1.0, 1.0, 0.55), (4.0, 0.32, 0.09), (9.9, 0.1, 0.03), (2.0, 0.06, 0.25)], 1.6)
    return _with_click(sig, vel * 0.2)


def glock(freq: float, vel: float = 1.0) -> np.ndarray:
    # free bar (glockenspiel / music box): 1 : 2.76 : 5.40 : 8.93
    sig = _bar(freq, vel, [(1.0, 1.0, 1.1), (2.76, 0.42, 0.35), (5.40, 0.22, 0.12), (8.93, 0.1, 0.05)], 2.6)
    return _with_click(sig, vel * 0.12)


def kalimba(freq: float, vel: float = 1.0) -> np.ndarray:
    sig = _bar(freq, vel, [(1.0, 1.0, 0.9), (5.95, 0.22, 0.08), (2.0, 0.12, 0.3), (3.0, 0.05, 0.2)], 2.0)
    return _with_click(sig, vel * 0.25)


def pluck(freq: float, vel: float = 1.0) -> np.ndarray:
    parts = [(n, 1.0 / n ** 1.2, 0.9 / n ** 0.7) for n in range(1, 9)]
    return _bar(freq, vel, parts, 2.0, attack=0.001)


def chime(freq: float, vel: float = 1.0) -> np.ndarray:
    """Soft, slow-blooming bell for ambient sets."""
    sig = _bar(freq, vel, [(1.0, 1.0, 2.2), (2.0, 0.3, 1.2), (3.01, 0.12, 0.6), (4.2, 0.05, 0.3)], 4.0,
               attack=0.006, decay_scale=1.2)
    return sig


INSTRUMENTS = {"marimba": marimba, "glock": glock, "kalimba": kalimba, "pluck": pluck, "chime": chime}


def click(vel: float = 1.0, bright: float = 1.0, seed: int = 0) -> np.ndarray:
    """Short tactile tick: a few fast-decaying high partials plus a noise spike."""
    rng = np.random.default_rng(seed)
    t = _t(0.05)
    out = np.zeros_like(t)
    for _ in range(4):
        f = rng.uniform(1500, 2600 + 2400 * bright)
        out += np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi)) * np.exp(-t / rng.uniform(0.002, 0.006))
    out += rng.normal(0, 0.6, len(t)) * np.exp(-t / 0.0012)
    return out * 0.35 * vel


def _with_click(sig: np.ndarray, vel: float) -> np.ndarray:
    c = click(vel, bright=0.5)
    n = min(len(c), len(sig))
    sig[:n] += c[:n]
    return sig


def pop(freq: float, vel: float = 1.0, seed: int = 0) -> np.ndarray:
    """Bubble 'bloop': a sine whose pitch rises quickly, plus a tiny click."""
    t = _t(0.16)
    f = freq * (1.0 + 0.9 * (1 - np.exp(-t / 0.018)))
    phase = 2 * np.pi * np.cumsum(f) / SR
    env = _attack(t, 0.0015) * np.exp(-t / 0.045)
    out = np.sin(phase) * env
    c = click(0.35, bright=0.8, seed=seed)
    out[: len(c)] += c[: len(out)]
    return out * vel


def thump(freq: float = 70.0, vel: float = 1.0, dur: float = 0.6) -> np.ndarray:
    """Low boom with a falling pitch."""
    t = _t(dur)
    f = freq * (0.65 + 0.35 * np.exp(-t / 0.06)) + freq * 0.8 * np.exp(-t / 0.008)
    phase = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(phase) * _attack(t, 0.002) * np.exp(-t / (dur * 0.35)) * vel


def noise_swell(dur: float, center: float, width: float, rise: float, fall: float, vel: float = 1.0,
                seed: int = 0, sweep: float = 0.0) -> np.ndarray:
    """Band-limited noise with a rise/fall envelope (whoosh). ``sweep`` > 0 makes
    the band brighten over time by mixing two bands."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR

    def band(c):
        spec = np.fft.rfft(rng.normal(0, 1, n))
        f = np.fft.rfftfreq(n, 1 / SR)
        spec *= np.exp(-0.5 * ((np.log(f + 1) - np.log(c)) / width) ** 2)
        b = np.fft.irfft(spec, n)
        return b / (np.abs(b).max() + 1e-9)

    sig = band(center)
    if sweep > 0:
        k = np.minimum(1.0, t / max(1e-3, dur * 0.6))
        sig = sig * (1 - k) + band(center * (1 + sweep)) * k
    env = np.where(t < rise, (t / rise) ** 2, np.exp(-(t - rise) / fall))
    return sig * env * vel


def pad_chord(freqs: list[float], dur: float, attack: float = 1.2, release: float = 1.6, vel: float = 1.0,
              seed: int = 0, brightness: float = 1.0) -> np.ndarray:
    """Warm detuned additive pad. Returns stereo (2, n)."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros((2, n))
    for f0 in freqs:
        for ch in range(2):
            for cents in (-7.0, 0.0, 7.0):
                f = f0 * 2 ** ((cents + rng.uniform(-2, 2)) / 1200)
                for h in range(1, 7):
                    if f * h > 9000:
                        break
                    amp = 1.0 / h ** (2.2 - 0.6 * brightness)
                    out[ch] += amp * np.sin(2 * np.pi * f * h * t + rng.uniform(0, 2 * np.pi))
    env = np.minimum(1.0, t / max(attack, 1e-3))
    env *= np.clip((dur - t) / max(release, 1e-3), 0.0, 1.0)
    trem = 1.0 + 0.06 * np.sin(2 * np.pi * 0.23 * t + rng.uniform(0, 6.28))
    out *= env * trem
    peak = np.abs(out).max() + 1e-9
    return out / peak * vel


class Mixer:
    """Stereo bus. ``add`` places a voice at an exact sample index."""

    def __init__(self, n_samples: int):
        self.n = n_samples
        self.dry = np.zeros((2, n_samples))
        self.wet_send = np.zeros((2, n_samples))

    def add(self, sig: np.ndarray, start: int, gain: float = 1.0, pan: float = 0.0, send: float = 1.0) -> None:
        if start >= self.n or gain == 0:
            return
        if sig.ndim == 1:
            # equal-power pan, pan in -1..1
            a = (pan + 1) * np.pi / 4
            stereo = np.stack([sig * np.cos(a), sig * np.sin(a)]) * np.sqrt(2)
        else:
            stereo = sig
        start = max(0, start)
        end = min(self.n, start + stereo.shape[1])
        chunk = stereo[:, : end - start] * gain
        self.dry[:, start:end] += chunk
        if send:
            self.wet_send[:, start:end] += chunk * send

    def finish(self, reverb_mix: float = 0.2, reverb_time: float = 1.8, seed: int = 0) -> np.ndarray:
        out = self.dry.copy()
        if reverb_mix > 0 and np.any(self.wet_send):
            out += reverb(self.wet_send, reverb_time, seed) * reverb_mix
        return master(out)


def reverb(x: np.ndarray, rt60: float = 1.8, seed: int = 0) -> np.ndarray:
    """Stereo FFT convolution with a synthetic decaying-noise impulse response."""
    rng = np.random.default_rng(seed + 7)
    n_ir = int(SR * min(4.0, rt60 * 1.2))
    t = np.arange(n_ir) / SR
    env = np.exp(-6.9 * t / rt60) * np.minimum(1.0, t / 0.012)
    ir = rng.normal(0, 1, (2, n_ir)) * env
    # darken: high frequencies decay faster
    spec = np.fft.rfft(ir, axis=1)
    f = np.fft.rfftfreq(n_ir, 1 / SR)
    spec *= 1.0 / (1.0 + (f / 5000.0) ** 2)
    ir = np.fft.irfft(spec, n_ir, axis=1)
    ir /= np.sqrt(np.sum(ir ** 2, axis=1, keepdims=True)) + 1e-9
    n = x.shape[1]
    size = 1 << int(np.ceil(np.log2(n + n_ir)))
    y = np.fft.irfft(np.fft.rfft(x, size, axis=1) * np.fft.rfft(ir, size, axis=1), size, axis=1)
    return y[:, :n]


def master(x: np.ndarray, target_rms_db: float = -16.0, ceiling: float = 0.93) -> np.ndarray:
    """Remove DC, bring to a consistent loudness, then soft-limit the peaks."""
    x = x - x.mean(axis=1, keepdims=True)
    active = np.abs(x).max(axis=0) > 1e-4
    if not active.any():
        return x.astype(np.float32)
    rms = np.sqrt(np.mean(x[:, active] ** 2))
    x = x * (10 ** (target_rms_db / 20) / (rms + 1e-12))
    thresh = 0.75
    mag = np.abs(x)
    over = mag > thresh
    x[over] = np.sign(x[over]) * (thresh + (1 - thresh) * np.tanh((mag[over] - thresh) / (1 - thresh)))
    return (x * ceiling).astype(np.float32)


def write_wav(path, audio: np.ndarray) -> None:
    """Write stereo float audio (2, n) as 16-bit PCM."""
    import wave

    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").T.copy()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
