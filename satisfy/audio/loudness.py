"""Loudness: ITU-R BS.1770 integrated loudness (LUFS) and normalisation.

``normalize`` brings a mix to a target integrated loudness (YouTube plays
everything at -14 LUFS) and keeps the peaks under a ceiling with a look-ahead
limiter. Everything is vectorised numpy: the K-weighting filter is applied as
its magnitude response in the frequency domain (loudness only depends on the
energy per 400 ms block, so the filter's phase does not matter), and the
limiter's gain curve is a moving minimum followed by a moving average.
"""

from __future__ import annotations

import numpy as np

from .synth import SR

# BS.1770-4 K-weighting at 48 kHz: high shelf (head) then high pass (RLB)
_SHELF = ([1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585])
_HIGHPASS = ([1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621])


def _biquad_mag(coeffs, f: np.ndarray) -> np.ndarray:
    b, a = coeffs
    z = np.exp(-2j * np.pi * f / SR)
    num = b[0] + b[1] * z + b[2] * z * z
    den = a[0] + a[1] * z + a[2] * z * z
    return np.abs(num / den)


def k_weight(x: np.ndarray) -> np.ndarray:
    """K-weighted copy of stereo ``x`` (2, n) (zero-phase, same magnitude response)."""
    n = x.shape[1]
    size = 1 << int(np.ceil(np.log2(n + SR)))  # zero padding: no wrap-around between end and start
    f = np.fft.rfftfreq(size, 1 / SR)
    h = _biquad_mag(_SHELF, f) * _biquad_mag(_HIGHPASS, f)
    return np.fft.irfft(np.fft.rfft(x, size, axis=1) * h, size, axis=1)[:, :n]


def block_power(x: np.ndarray, block: float = 0.4, overlap: float = 0.75) -> np.ndarray:
    """Mean-square K-weighted power of each gating block, summed over channels."""
    y = k_weight(x)
    p = np.sum(y * y, axis=0)
    size = int(round(block * SR))
    hop = int(round(size * (1 - overlap)))
    if len(p) < size:
        return np.array([p.mean()]) if len(p) else np.zeros(0)
    c = np.concatenate([[0.0], np.cumsum(p)])
    starts = np.arange(0, len(p) - size + 1, hop)
    return (c[starts + size] - c[starts]) / size


def _lufs(power):
    return -0.691 + 10 * np.log10(np.maximum(power, 1e-20))


def integrated_lufs(x: np.ndarray) -> float:
    """Integrated loudness of stereo ``x`` (2, n) in LUFS (absolute and relative gating)."""
    z = block_power(x)
    z = z[_lufs(z) > -70.0]
    if len(z) == 0:
        return -70.0
    rel = _lufs(z.mean()) - 10.0
    z = z[_lufs(z) > rel]
    return float(_lufs(z.mean())) if len(z) else -70.0


def _moving_min(x: np.ndarray, w: int) -> np.ndarray:
    """``out[i] = min(x[i : i + w])`` (van Herk / Gil-Werman, O(n)); ``x`` padded with +inf."""
    n = len(x)
    m = -(-(n + w) // w) * w
    xp = np.full(m, np.inf)
    xp[:n] = x
    blocks = xp.reshape(-1, w)
    pre = np.minimum.accumulate(blocks, axis=1).ravel()
    suf = np.minimum.accumulate(blocks[:, ::-1], axis=1)[:, ::-1].ravel()
    i = np.arange(n)
    return np.minimum(suf[i], pre[np.minimum(i + w - 1, m - 1)])


def _moving_mean(x: np.ndarray, w: int) -> np.ndarray:
    """``out[i] = mean(x[i : i + w])``; ``x`` must already be padded by ``w - 1``."""
    c = np.concatenate([[0.0], np.cumsum(x)])
    return (c[w:] - c[:-w]) / w


def limit(x: np.ndarray, ceiling_db: float = -1.0, window: float = 0.02, circular: bool = False) -> np.ndarray:
    """Look-ahead peak limiter (stereo linked). The gain dips smoothly over
    ``window`` seconds either side of a peak. A moving minimum over twice the
    window followed by a moving average over the window can never exceed the
    gain any sample in reach needs, so no sample ends up above the ceiling.
    ``circular``: the signal is one cycle of a loop, so the limiter looks
    across the seam (the end anticipates the start's peaks)."""
    ceiling = 10 ** (ceiling_db / 20)
    peak = np.abs(x).max(axis=0)
    need = np.minimum(1.0, ceiling / np.maximum(peak, 1e-12))
    if need.min() >= 1.0:
        return x
    h = max(1, int(window * SR))
    n = len(need)
    if circular:
        reps = -(-2 * h // n) + 1
        pad = np.tile(need, 2 * reps + 1)
        lo = reps * n - 2 * h
        ext = pad[lo: lo + n + 4 * h]
    else:
        ext = np.concatenate([np.ones(2 * h), need, np.ones(2 * h)])
    g = _moving_min(ext, 2 * h + 1)  # g[j] = min over ext[j .. j + 2h]
    g = _moving_mean(g, 2 * h + 1)  # mean over g[j .. j + 2h]
    # out[i] corresponds to ext index i + 2h: centred min (±h) then centred mean (±h)
    gain = g[:n]
    return x * gain


def normalize(x: np.ndarray, target_lufs: float = -14.0, ceiling_db: float = -1.0,
              circular: bool = False) -> np.ndarray:
    """Remove DC, bring ``x`` to ``target_lufs`` integrated loudness, and limit
    the peaks to ``ceiling_db``. Limiting lowers the loudness a little, so the
    gain is refined a few times (at most +4 dB on top of the plain gain)."""
    x = x - x.mean(axis=1, keepdims=True)
    level = integrated_lufs(x)
    if level <= -69.0:
        return x.astype(np.float32)
    gain = 10 ** ((target_lufs - level) / 20)
    top = gain * 10 ** (4 / 20)
    y = limit(x * gain, ceiling_db, circular=circular)
    for _ in range(6):
        err = target_lufs - integrated_lufs(y)
        if abs(err) < 0.03 or gain >= top:
            break
        gain = min(top, gain * 10 ** (err / 20))
        y = limit(x * gain, ceiling_db, circular=circular)
    return y.astype(np.float32)
