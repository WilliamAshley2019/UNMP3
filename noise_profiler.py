#!/usr/bin/env python3
"""
noise_profiler.py — UNMP3 v4 Acoustic Analysis Extension
==========================================================
Offline noise characterisation, dynamic envelope profiling,
transient analysis, harmonic distortion fingerprinting,
and multidimensional profile delta computation.

Designed to extend the .remeta sidecar with deep analysis data
that can auto-configure EIN Lab VST3 and drive WaveLab batch workflows.

Stages
------
Stage 1 — Segment Detection
    Scan the file for the quietest N seconds using short-time RMS.
    Flag if no quiet segment exists (live recording with no gaps).

Stage 2 — Noise Characterisation
    On the reference segment:
      · Power spectral density (Welch method)
      · White noise density (dB/√Hz)
      · 1/f slope via log-log least-squares fit
      · Corner frequency (white → 1/f transition)
      · Hum detection at 50/60 Hz and harmonics up to 12th
      · RMS noise floor and 6.6σ peak-to-peak ceiling
      · Noise floor shape stored as 1/3-octave band profile

Stage 3 — Spectral Subtraction Export (optional)
    Overlap-add Wiener soft-mask denoising.
    Outputs _denoised.wav and records parameters in .remeta.

Dynamic Envelope Profiling
    Short-time RMS envelope, attack/release detection,
    macro loudness arc (8 equal segments), crest factor per segment.

Transient Character
    Onset detection via spectral flux, transient density (per second),
    average attack time (ms), transient-to-noise ratio (TNR).

Harmonic Distortion Signature
    THD-style measurement: detect fundamental, measure 2nd–8th harmonic
    levels relative to fundamental, flag hum harmonics separately.

Profile Delta
    compute_profile_delta(ref_meta, src_meta) → delta dict containing:
      · EQ correction curve (per 1/3-octave bin, dB)
      · Noise floor offset and 1/f slope correction
      · Dynamic range delta (ratio + threshold shift)
      · Hum signature difference
      · Transient character delta
      · Harmonic correction deltas
    Exportable as: parametric EQ curve, EIN Lab preset JSON, or
    WaveLab JavaScript preset stub.

Dependencies: numpy, ffmpeg in PATH
"""

import json
import math
import os
import struct
import subprocess
import tempfile
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

ANALYSIS_SR       = 44100    # internal analysis sample rate
FFT_SIZE          = 8192     # main analysis window
HOP_SIZE          = 2048     # hop for overlap-add
NOISE_SEARCH_SECS = 0.5      # length of quiet segment to search for
SIGMA_CEILING     = 6.6      # σ multiplier for p-p noise ceiling (covers 99.9999998%)
HUM_FREQS_50      = [50  * i for i in range(1, 13)]   # 50 Hz mains + harmonics
HUM_FREQS_60      = [60  * i for i in range(1, 13)]   # 60 Hz mains + harmonics
THIRD_OCTAVE_CENTRES = [                               # ISO 1/3-octave band centres
    20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160,
    200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
    2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000
]


# ─────────────────────────────────────────────────────────────────────────────
# Audio I/O helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_audio(path, sr=ANALYSIS_SR, mono=True, max_seconds=None):
    """
    Decode any audio file to numpy float32 array via ffmpeg.
    Returns (data, actual_sr, channels_in_source).
    data is always float32; if mono=True, mixed to mono.
    """
    import numpy as np
    path = Path(path)
    tmp  = Path(tempfile.gettempdir()) / f"np_{os.getpid()}_{id(path)}.f32"

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(path),
    ]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    ch_out = 1 if mono else 2
    cmd += [
        "-ar", str(sr),
        "-ac", str(ch_out),
        "-f",  "f32le",
        str(tmp),
    ]
    subprocess.run(cmd, check=True)
    data = np.fromfile(str(tmp), dtype="float32")
    tmp.unlink(missing_ok=True)

    if not mono:
        data = data.reshape(-1, 2)
    return data, sr


def _write_audio(path, data, sr):
    """Write float32 numpy array to WAV via ffmpeg."""
    import numpy as np
    tmp = Path(tempfile.gettempdir()) / f"wr_{os.getpid()}_{id(path)}.f32"
    data.astype(np.float32).tofile(str(tmp))
    ch = 1 if data.ndim == 1 else data.shape[1]
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "f32le", "-ar", str(sr), "-ac", str(ch),
        "-i", str(tmp),
        "-c:a", "pcm_s24le",
        str(path),
    ], check=True)
    tmp.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Quiet segment detection
# ─────────────────────────────────────────────────────────────────────────────

def find_noise_floor_segment(data, sr, window_secs=NOISE_SEARCH_SECS):
    """
    Scan data for the quietest contiguous segment of `window_secs` length.

    Returns
    -------
    dict with keys:
        start_sample   int
        end_sample     int
        start_sec      float
        end_sec        float
        rms_db         float   RMS of the segment in dB
        has_quiet_seg  bool    False if the whole file is loud (live recording)
        warning        str     Human-readable flag if no usable segment found
    """
    import numpy as np

    frame = int(window_secs * sr)
    hop   = frame // 4

    best_rms  = float("inf")
    best_start = 0

    for i in range(0, max(1, len(data) - frame), hop):
        seg = data[i : i + frame]
        rms = float(np.sqrt(np.mean(seg ** 2)))
        if rms < best_rms:
            best_rms   = rms
            best_start = i

    rms_db = 20 * math.log10(best_rms + 1e-12)

    # Threshold: if the quietest window is louder than –40 dBFS we warn
    has_quiet = rms_db < -40.0
    warning   = "" if has_quiet else (
        f"No quiet segment found below –40 dBFS "
        f"(quietest: {rms_db:.1f} dBFS). "
        "Noise profile may reflect signal content rather than pure noise floor."
    )

    return {
        "start_sample":  best_start,
        "end_sample":    best_start + frame,
        "start_sec":     round(best_start / sr, 4),
        "end_sec":       round((best_start + frame) / sr, 4),
        "rms_db":        round(rms_db, 3),
        "has_quiet_seg": has_quiet,
        "warning":       warning,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Noise characterisation
# ─────────────────────────────────────────────────────────────────────────────

def characterise_noise(segment, sr, fft_size=FFT_SIZE):
    """
    Full noise characterisation on a numpy array segment.

    Returns a flat dict suitable for embedding in .remeta under
    the key 'noise_profile'.
    """
    import numpy as np

    result = {}
    n = len(segment)
    if n < fft_size:
        fft_size = max(256, 2 ** int(math.log2(n)))

    # ── Welch PSD ────────────────────────────────────────────────────────────
    hop    = fft_size // 2
    window = np.hanning(fft_size)
    psds   = []
    for start in range(0, n - fft_size, hop):
        frame = segment[start : start + fft_size] * window
        spec  = np.abs(np.fft.rfft(frame)) ** 2
        psds.append(spec)

    if not psds:
        return {"error": "segment too short for PSD"}

    psd      = np.mean(psds, axis=0)                    # averaged power spectrum
    freqs    = np.fft.rfftfreq(fft_size, 1.0 / sr)     # frequency axis

    # ── White noise density (dB/√Hz) ─────────────────────────────────────────
    # Average PSD over 2–8 kHz band (usually flat / white dominated)
    mask_white = (freqs >= 2000) & (freqs <= 8000)
    if mask_white.any():
        white_power = float(np.mean(psd[mask_white]))
        df          = freqs[1] - freqs[0]               # bin width Hz
        white_density_db = 10 * math.log10(white_power / df + 1e-30)
    else:
        white_density_db = -120.0
    result["white_noise_density_db_per_sqrt_hz"] = round(white_density_db, 2)

    # ── 1/f slope via log-log least-squares (20 Hz – 500 Hz) ────────────────
    mask_pink = (freqs >= 20) & (freqs <= 500)
    if mask_pink.sum() > 4:
        log_f   = np.log10(freqs[mask_pink])
        log_psd = np.log10(psd[mask_pink] + 1e-30)
        slope, intercept = np.polyfit(log_f, log_psd, 1)
        result["pink_noise_slope"]      = round(float(slope), 4)
        result["pink_noise_intercept"]  = round(float(intercept), 4)
        # Ideal 1/f gives slope ≈ –2 (power, so –10 dB/decade for amplitude)
        result["noise_character"] = (
            "white"  if slope > -0.5 else
            "pink"   if slope > -1.5 else
            "brown"  if slope > -2.5 else
            "dark"
        )
    else:
        result["pink_noise_slope"]     = 0.0
        result["noise_character"]      = "unknown"

    # ── Corner frequency (white→1/f transition) ──────────────────────────────
    # Find where PSD crosses the fitted white level from below
    mask_search = (freqs >= 20) & (freqs <= 4000)
    if mask_search.any() and mask_white.any():
        psd_search    = psd[mask_search]
        freqs_search  = freqs[mask_search]
        above_white   = psd_search > white_power
        crossings     = np.where(np.diff(above_white.astype(int)))[0]
        if len(crossings):
            corner_hz = float(freqs_search[crossings[-1]])
        else:
            corner_hz = 1000.0
    else:
        corner_hz = 1000.0
    result["corner_frequency_hz"] = round(corner_hz, 1)

    # ── RMS noise floor and 6.6σ peak ceiling ────────────────────────────────
    rms = float(np.sqrt(np.mean(segment ** 2)))
    std = float(np.std(segment))
    result["noise_floor_rms"]         = round(rms, 8)
    result["noise_floor_rms_db"]      = round(20 * math.log10(rms + 1e-12), 3)
    result["noise_floor_std"]         = round(std, 8)
    result["noise_floor_pp_ceiling"]  = round(std * SIGMA_CEILING, 8)
    result["noise_floor_pp_db"]       = round(
        20 * math.log10(std * SIGMA_CEILING + 1e-12), 3)
    result["sigma_multiplier"]        = SIGMA_CEILING

    # ── 1/3-octave noise floor shape ─────────────────────────────────────────
    third_oct = {}
    for fc in THIRD_OCTAVE_CENTRES:
        f_lo = fc / (2 ** (1/6))
        f_hi = fc * (2 ** (1/6))
        band = (freqs >= f_lo) & (freqs < f_hi)
        if band.any():
            band_power = float(np.mean(psd[band]))
            band_db    = round(10 * math.log10(band_power + 1e-30), 2)
        else:
            band_db = -120.0
        third_oct[str(int(fc))] = band_db
    result["third_octave_profile_db"] = third_oct

    # ── Hum detection ─────────────────────────────────────────────────────────
    def _hum_level(freq_hz):
        idx = int(round(freq_hz * fft_size / sr))
        idx = max(0, min(idx, len(psd) - 1))
        # average ±2 bins around the harmonic
        lo = max(0, idx - 2);  hi = min(len(psd), idx + 3)
        return float(np.mean(psd[lo:hi]))

    def _detect_hum(hum_freqs, label):
        levels = {}
        for f in hum_freqs:
            if f > sr / 2:
                break
            db = 10 * math.log10(_hum_level(f) + 1e-30)
            levels[str(int(f))] = round(db, 2)
        # Is this mains frequency present? Fundamental louder than –80 dBFS
        present = levels.get(str(int(hum_freqs[0])), -120) > -80
        return {"present": present, "harmonic_levels_db": levels}

    result["hum_50hz"] = _detect_hum(HUM_FREQS_50, "50Hz")
    result["hum_60hz"] = _detect_hum(HUM_FREQS_60, "60Hz")
    result["hum_detected"] = (
        result["hum_50hz"]["present"] or result["hum_60hz"]["present"]
    )
    if result["hum_50hz"]["present"] and result["hum_60hz"]["present"]:
        result["hum_mains"] = "ambiguous"
    elif result["hum_50hz"]["present"]:
        result["hum_mains"] = "50Hz"
    elif result["hum_60hz"]["present"]:
        result["hum_mains"] = "60Hz"
    else:
        result["hum_mains"] = "none"

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic envelope profiling
# ─────────────────────────────────────────────────────────────────────────────

def profile_dynamic_envelope(data, sr, frame_ms=50, num_macro_segments=8):
    """
    Compute the short-time RMS envelope and macro dynamic arc.

    Returns dict with:
        envelope_rms_db        list[float]  per-frame RMS in dB
        envelope_times_sec     list[float]  centre time of each frame
        macro_rms_db           list[float]  8-segment loudness arc
        macro_crest_db         list[float]  8-segment crest factor
        attack_time_ms         float        median rise time (10→90% RMS)
        release_time_ms        float        median fall time (90→10% RMS)
        dynamic_range_db       float        difference between loudest and quietest macro segment
        compression_estimate   str          "uncompressed" / "light" / "moderate" / "heavy"
    """
    import numpy as np

    frame = max(64, int(sr * frame_ms / 1000))
    hop   = frame // 2

    rms_vals  = []
    time_vals = []

    for i in range(0, len(data) - frame, hop):
        seg = data[i : i + frame]
        rms = float(np.sqrt(np.mean(seg ** 2)))
        rms_vals.append(20 * math.log10(rms + 1e-12))
        time_vals.append((i + frame / 2) / sr)

    rms_arr = np.array(rms_vals)

    # ── Macro arc (8 equal time segments) ────────────────────────────────────
    seg_size = len(rms_arr) // num_macro_segments or 1
    macro_rms   = []
    macro_crest = []

    for s in range(num_macro_segments):
        sl = rms_arr[s * seg_size : (s + 1) * seg_size]
        if len(sl):
            macro_rms.append(round(float(np.mean(sl)), 2))
            # crest: difference between peak and RMS in linear then dB
            seg_lin = 10 ** (sl / 20)
            peak_db = 20 * math.log10(float(np.max(np.abs(seg_lin))) + 1e-12)
            macro_crest.append(round(peak_db - float(np.mean(sl)), 2))

    dr = round(float(np.max(macro_rms)) - float(np.min(macro_rms)), 2) if macro_rms else 0.0

    # ── Attack / release estimation ───────────────────────────────────────────
    # Detect rising and falling edges in the RMS envelope
    attacks  = []
    releases = []
    threshold_low  = float(np.percentile(rms_arr, 20))
    threshold_high = float(np.percentile(rms_arr, 80))

    i = 0
    while i < len(rms_arr) - 1:
        if rms_arr[i] < threshold_low and rms_arr[i + 1] > threshold_low:
            # Rising edge — find how long to reach threshold_high
            j = i + 1
            while j < len(rms_arr) and rms_arr[j] < threshold_high:
                j += 1
            if j < len(rms_arr):
                attacks.append((j - i) * frame_ms / 2)
            i = j
        elif rms_arr[i] > threshold_high and rms_arr[i + 1] < threshold_high:
            j = i + 1
            while j < len(rms_arr) and rms_arr[j] > threshold_low:
                j += 1
            if j < len(rms_arr):
                releases.append((j - i) * frame_ms / 2)
            i = j
        else:
            i += 1

    attack_ms  = round(float(np.median(attacks)),  2) if attacks  else 0.0
    release_ms = round(float(np.median(releases)), 2) if releases else 0.0

    # ── Compression estimate ──────────────────────────────────────────────────
    compression = (
        "uncompressed" if dr > 20 else
        "light"        if dr > 12 else
        "moderate"     if dr > 6  else
        "heavy"
    )

    return {
        "envelope_rms_db":       [round(v, 2) for v in rms_vals],
        "envelope_times_sec":    [round(v, 4) for v in time_vals],
        "macro_rms_db":          macro_rms,
        "macro_crest_db":        macro_crest,
        "attack_time_ms":        attack_ms,
        "release_time_ms":       release_ms,
        "dynamic_range_db":      dr,
        "compression_estimate":  compression,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Transient character
# ─────────────────────────────────────────────────────────────────────────────

def profile_transients(data, sr, fft_size=2048, hop=512, threshold_percentile=95):
    """
    Detect transients via spectral flux and characterise attack behaviour.

    Returns dict with:
        transient_count        int
        transient_density_per_sec  float
        transient_times_sec    list[float]
        avg_attack_ms          float   average onset sharpness
        transient_to_noise_ratio_db  float
        transient_character    str     "percussive" / "melodic" / "speech" / "noise"
    """
    import numpy as np

    window   = np.hanning(fft_size)
    prev_spec = None
    flux      = []
    times     = []

    for i in range(0, len(data) - fft_size, hop):
        frame = data[i : i + fft_size] * window
        spec  = np.abs(np.fft.rfft(frame))
        if prev_spec is not None:
            diff = np.maximum(spec - prev_spec, 0)
            flux.append(float(np.sum(diff)))
            times.append((i + fft_size / 2) / sr)
        prev_spec = spec

    if not flux:
        return {"transient_count": 0, "transient_density_per_sec": 0.0}

    flux_arr  = np.array(flux)
    threshold = float(np.percentile(flux_arr, threshold_percentile))

    # Peak-pick: local maxima above threshold
    transient_times = []
    for i in range(1, len(flux_arr) - 1):
        if flux_arr[i] > threshold and flux_arr[i] >= flux_arr[i-1] and flux_arr[i] >= flux_arr[i+1]:
            transient_times.append(times[i])

    duration     = len(data) / sr
    density      = len(transient_times) / duration if duration > 0 else 0.0

    # Average attack time: for each transient, measure how fast flux rises
    attack_times = []
    for t in transient_times:
        frame_idx = int(t * sr / hop)
        # Find the onset: walk back to 10% of peak
        peak_val = flux_arr[min(frame_idx, len(flux_arr)-1)]
        j = frame_idx
        while j > 0 and flux_arr[j] > peak_val * 0.1:
            j -= 1
        attack_frames = frame_idx - j
        attack_times.append(attack_frames * hop / sr * 1000)

    avg_attack = round(float(np.mean(attack_times)), 2) if attack_times else 0.0

    # TNR: ratio of transient energy to background flux
    if len(flux_arr) > 0:
        background = float(np.median(flux_arr))
        peak_flux  = float(np.mean([flux_arr[min(int(t * sr / hop), len(flux_arr)-1)]
                                    for t in transient_times])) if transient_times else background
        tnr = 20 * math.log10(peak_flux / (background + 1e-12))
    else:
        tnr = 0.0

    # Character classification
    if density > 8:
        character = "percussive"
    elif density > 2:
        character = "melodic" if avg_attack > 30 else "percussive"
    elif density > 0.5:
        character = "speech"
    else:
        character = "noise" if tnr < 6 else "melodic"

    return {
        "transient_count":              len(transient_times),
        "transient_density_per_sec":    round(density, 3),
        "transient_times_sec":          [round(t, 4) for t in transient_times[:200]],
        "avg_attack_ms":                avg_attack,
        "transient_to_noise_ratio_db":  round(tnr, 2),
        "transient_character":          character,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Harmonic distortion signature
# ─────────────────────────────────────────────────────────────────────────────

def profile_harmonics(data, sr, fft_size=FFT_SIZE, num_harmonics=8):
    """
    Measure harmonic content relative to the dominant fundamental.
    Designed to fingerprint the colouration of the recording chain.

    Returns dict with:
        fundamental_hz         float
        fundamental_db         float
        harmonic_levels_db     dict  H2..H8 levels in dB relative to fundamental
        thd_percent            float total harmonic distortion %
        harmonic_character     str   "clean" / "warm" / "bright" / "saturated"
        spectral_flatness      float Wiener entropy (0=tonal, 1=noise-like)
    """
    import numpy as np

    # Use a large FFT on the first 10s for frequency resolution
    segment = data[:sr * 10]
    if len(segment) < fft_size:
        return {"error": "too short for harmonic analysis"}

    window = np.hanning(fft_size)

    # Average spectrum over multiple frames
    specs = []
    for i in range(0, len(segment) - fft_size, fft_size // 2):
        frame = segment[i : i + fft_size] * window
        specs.append(np.abs(np.fft.rfft(frame)))
    if not specs:
        return {"error": "no frames"}

    avg_spec = np.mean(specs, axis=0)
    freqs    = np.fft.rfftfreq(fft_size, 1.0 / sr)
    df       = freqs[1]

    # ── Find dominant fundamental (50 Hz – 2000 Hz) ──────────────────────────
    mask = (freqs >= 50) & (freqs <= 2000)
    fund_local_idx = int(np.argmax(avg_spec[mask]))
    fund_idx  = np.where(mask)[0][fund_local_idx]
    fund_hz   = float(freqs[fund_idx])
    fund_mag  = float(avg_spec[fund_idx])
    fund_db   = 20 * math.log10(fund_mag + 1e-12)

    # ── Harmonic levels ───────────────────────────────────────────────────────
    harmonic_db = {}
    harmonic_lin = []
    for h in range(2, num_harmonics + 1):
        h_freq = fund_hz * h
        if h_freq > sr / 2:
            break
        h_idx = int(round(h_freq / df))
        h_idx = max(0, min(h_idx, len(avg_spec) - 1))
        # Average ±3 bins
        lo = max(0, h_idx - 3); hi = min(len(avg_spec), h_idx + 4)
        h_mag = float(np.mean(avg_spec[lo:hi]))
        h_db_rel = 20 * math.log10(h_mag / (fund_mag + 1e-12))
        harmonic_db[f"H{h}"] = round(h_db_rel, 2)
        harmonic_lin.append(h_mag)

    # ── THD % ─────────────────────────────────────────────────────────────────
    if harmonic_lin:
        thd = 100.0 * math.sqrt(sum(m**2 for m in harmonic_lin)) / (fund_mag + 1e-12)
    else:
        thd = 0.0

    # ── Harmonic character ────────────────────────────────────────────────────
    h2 = harmonic_db.get("H2", -120)
    h3 = harmonic_db.get("H3", -120)
    h4 = harmonic_db.get("H4", -120)
    if thd > 5:
        character = "saturated"
    elif h2 > -20 and h3 < h2 - 6:
        character = "warm"    # even-order dominant → transformer/tube warmth
    elif h3 > h2 and h3 > -30:
        character = "bright"  # odd-order dominant → transistor / tape saturation
    else:
        character = "clean"

    # ── Spectral flatness (Wiener entropy) ───────────────────────────────────
    geom_mean = math.exp(float(np.mean(np.log(avg_spec + 1e-12))))
    arith_mean = float(np.mean(avg_spec))
    flatness = geom_mean / (arith_mean + 1e-12)

    return {
        "fundamental_hz":      round(fund_hz, 2),
        "fundamental_db":      round(fund_db, 2),
        "harmonic_levels_db":  harmonic_db,
        "thd_percent":         round(thd, 4),
        "harmonic_character":  character,
        "spectral_flatness":   round(flatness, 6),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — Wiener soft-mask spectral subtraction
# ─────────────────────────────────────────────────────────────────────────────

def spectral_subtraction(data, sr, noise_psd, fft_size=FFT_SIZE,
                          over_subtraction=1.5, floor_db=-80.0):
    """
    Overlap-add Wiener soft-mask denoising.

    Parameters
    ----------
    data             : float32 mono numpy array
    sr               : sample rate
    noise_psd        : numpy array, power spectrum of the noise estimate
                       (length = fft_size//2+1)
    over_subtraction : α — over-subtraction factor (1.0–2.0, default 1.5)
                       Higher = more aggressive noise removal, more musical noise risk
    floor_db         : spectral floor below which bins are zeroed (default –80 dBFS)

    Returns
    -------
    cleaned : float32 mono numpy array, same length as data
    """
    import numpy as np

    hop    = fft_size // 4     # 75% overlap for smooth reconstruction
    window = np.hanning(fft_size).astype(np.float32)
    # Normalisation factor for OLA
    ola_norm = np.zeros(len(data) + fft_size, dtype=np.float32)
    output   = np.zeros(len(data) + fft_size, dtype=np.float32)
    floor_lin = 10 ** (floor_db / 20)

    for i in range(0, len(data) - fft_size, hop):
        frame  = data[i : i + fft_size] * window
        spec   = np.fft.rfft(frame)
        mag    = np.abs(spec)
        phase  = np.angle(spec)
        power  = mag ** 2

        # Wiener gain: G = max(1 - α·N/S, floor)
        gain   = np.maximum(1.0 - over_subtraction * noise_psd / (power + 1e-30), floor_lin)

        cleaned_mag  = mag * gain
        cleaned_spec = cleaned_mag * np.exp(1j * phase)
        frame_out    = np.fft.irfft(cleaned_spec).real * window

        output[i : i + fft_size]   += frame_out
        ola_norm[i : i + fft_size] += window ** 2

    # Normalise by OLA window sum
    ola_norm = np.maximum(ola_norm, 1e-8)
    output  /= ola_norm
    return output[:len(data)]


def denoise_file(input_path, output_path, noise_segment_info=None,
                 over_subtraction=1.5, progress_cb=None):
    """
    High-level API: detect noise floor, run spectral subtraction, write output.

    Returns a dict of parameters used (for embedding in .remeta).
    """
    import numpy as np

    if progress_cb: progress_cb("Loading audio…")
    data, sr = _read_audio(input_path, sr=ANALYSIS_SR, mono=True)

    if progress_cb: progress_cb("Detecting noise floor segment…")
    seg_info = noise_segment_info or find_noise_floor_segment(data, sr)
    seg = data[seg_info["start_sample"] : seg_info["end_sample"]]

    if progress_cb: progress_cb("Estimating noise PSD…")
    fft_size = FFT_SIZE
    hop      = fft_size // 2
    window   = np.hanning(fft_size)
    psds     = []
    for i in range(0, len(seg) - fft_size, hop):
        frame = seg[i : i + fft_size] * window
        psds.append(np.abs(np.fft.rfft(frame)) ** 2)
    noise_psd = np.mean(psds, axis=0).astype(np.float32) if psds else np.zeros(fft_size//2+1)

    if progress_cb: progress_cb("Running Wiener spectral subtraction…")
    cleaned = spectral_subtraction(data, sr, noise_psd,
                                    over_subtraction=over_subtraction)

    if progress_cb: progress_cb("Writing denoised file…")
    _write_audio(output_path, cleaned, sr)

    params = {
        "denoised_output":        str(output_path),
        "noise_segment_start_sec": seg_info["start_sec"],
        "noise_segment_end_sec":   seg_info["end_sec"],
        "noise_segment_rms_db":    seg_info["rms_db"],
        "over_subtraction_alpha":  over_subtraction,
        "fft_size":                fft_size,
        "method":                  "wiener_soft_mask",
    }
    return params


# ─────────────────────────────────────────────────────────────────────────────
# Master analysis entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_full_analysis(wav_path, progress_cb=None):
    """
    Run all four profiling stages on a WAV file.

    Returns a dict with keys:
        noise_segment_info     from Stage 1
        noise_profile          from Stage 2
        dynamic_envelope       dynamic envelope profile
        transient_profile      transient character
        harmonic_profile       harmonic distortion signature
        analysis_version       "4.0"

    This dict is designed to be embedded in the .remeta sidecar under
    the top-level key "deep_analysis".
    """
    import numpy as np

    wav_path = Path(wav_path)
    result   = {"analysis_version": "4.0", "source": wav_path.name}

    def _cb(msg):
        if progress_cb:
            progress_cb(msg)
        else:
            print(f"    [analysis] {msg}")

    _cb("Loading audio for deep analysis…")
    data, sr = _read_audio(wav_path, sr=ANALYSIS_SR, mono=True, max_seconds=300)

    _cb("Stage 1 — Quiet segment detection…")
    seg_info = find_noise_floor_segment(data, sr)
    result["noise_segment_info"] = seg_info
    if seg_info.get("warning"):
        _cb(f"  ⚠  {seg_info['warning']}")

    _cb("Stage 2 — Noise characterisation…")
    segment = data[seg_info["start_sample"] : seg_info["end_sample"]]
    result["noise_profile"] = characterise_noise(segment, sr)

    _cb("Dynamic envelope profiling…")
    result["dynamic_envelope"] = profile_dynamic_envelope(data, sr)

    _cb("Transient character profiling…")
    result["transient_profile"] = profile_transients(data, sr)

    _cb("Harmonic distortion signature…")
    result["harmonic_profile"] = profile_harmonics(data, sr)

    _cb("Deep analysis complete.")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Profile delta — multidimensional comparison between two .remeta files
# ─────────────────────────────────────────────────────────────────────────────

def compute_profile_delta(reference_meta, source_meta):
    """
    Compute the multidimensional transform needed to bring source → reference.

    Parameters
    ----------
    reference_meta : dict   remeta dict of the reference (target) audio
    source_meta    : dict   remeta dict of the source (to be corrected) audio

    Returns
    -------
    delta : dict with keys:

        eq_correction_db          dict  {freq_hz_str: dB_correction}
                                        Per 1/3-octave: positive = boost source
        noise_floor_offset_db     float Add this to source noise floor to match ref
        pink_slope_correction     float Add to source 1/f slope to match ref
        corner_freq_correction_hz float Hz shift needed on corner frequency
        dynamic_range_delta_db    float Ref DR – Source DR (positive = source too compressed)
        compression_ratio_hint    float Approx expansion ratio needed (1.0 = none)
        attack_delta_ms           float Ref attack – Source attack
        release_delta_ms          float Ref release – Source release
        hum_delta                 dict  Per-harmonic level differences (ref – source) dB
        harmonic_deltas_db        dict  H2..H8 level differences (ref – source) dB
        thd_delta_percent         float Ref THD – Source THD
        transient_density_delta   float Ref density – Source density (per sec)
        tnr_delta_db              float Ref TNR – Source TNR
        summary                   dict  Human-readable description of each correction

        ein_lab_preset            dict  Parameter hints for EIN Lab VST3
        wavlab_stub               str   WaveLab JavaScript preset stub
        eq_curve_points           list  [(freq_hz, gain_db), ...] for generic EQ
    """
    def _deep(meta, *keys):
        """Safe nested get."""
        v = meta
        for k in keys:
            if not isinstance(v, dict):
                return None
            v = v.get(k)
        return v

    delta   = {}
    summary = {}

    ref_da  = _deep(reference_meta, "deep_analysis") or {}
    src_da  = _deep(source_meta,    "deep_analysis") or {}
    ref_np  = ref_da.get("noise_profile",   {})
    src_np  = src_da.get("noise_profile",   {})
    ref_de  = ref_da.get("dynamic_envelope", {})
    src_de  = src_da.get("dynamic_envelope", {})
    ref_tp  = ref_da.get("transient_profile", {})
    src_tp  = src_da.get("transient_profile", {})
    ref_hp  = ref_da.get("harmonic_profile",  {})
    src_hp  = src_da.get("harmonic_profile",  {})

    # ── EQ correction: 1/3-octave noise floor shape ───────────────────────────
    ref_3oct = ref_np.get("third_octave_profile_db", {})
    src_3oct = src_np.get("third_octave_profile_db", {})
    eq_corr  = {}
    for fc_str in ref_3oct:
        ref_db = ref_3oct.get(fc_str, -120.0)
        src_db = src_3oct.get(fc_str, -120.0)
        if ref_db > -119 and src_db > -119:
            eq_corr[fc_str] = round(ref_db - src_db, 2)
    delta["eq_correction_db"] = eq_corr
    if eq_corr:
        max_boost = max(eq_corr.values())
        max_cut   = min(eq_corr.values())
        summary["eq"] = (
            f"EQ: boost up to +{max_boost:.1f} dB / cut {max_cut:.1f} dB "
            f"to match reference noise floor shape."
        )

    # ── Noise floor offset ────────────────────────────────────────────────────
    ref_nf = ref_np.get("noise_floor_rms_db")
    src_nf = src_np.get("noise_floor_rms_db")
    if ref_nf is not None and src_nf is not None:
        offset = round(float(ref_nf) - float(src_nf), 2)
        delta["noise_floor_offset_db"] = offset
        summary["noise_floor"] = (
            f"Noise floor: source is {abs(offset):.1f} dB "
            f"{'louder' if offset < 0 else 'quieter'} than reference."
        )
    else:
        delta["noise_floor_offset_db"] = 0.0

    # ── 1/f slope correction ──────────────────────────────────────────────────
    ref_sl = ref_np.get("pink_noise_slope")
    src_sl = src_np.get("pink_noise_slope")
    if ref_sl is not None and src_sl is not None:
        slope_corr = round(float(ref_sl) - float(src_sl), 4)
        delta["pink_slope_correction"] = slope_corr
        summary["pink_slope"] = (
            f"1/f slope: source slope {src_sl:.2f}, reference {ref_sl:.2f}. "
            f"Correction: {slope_corr:+.2f} (tilt filter hint)."
        )
    else:
        delta["pink_slope_correction"] = 0.0

    # ── Corner frequency ──────────────────────────────────────────────────────
    ref_cf = ref_np.get("corner_frequency_hz")
    src_cf = src_np.get("corner_frequency_hz")
    if ref_cf is not None and src_cf is not None:
        delta["corner_freq_correction_hz"] = round(float(ref_cf) - float(src_cf), 1)
    else:
        delta["corner_freq_correction_hz"] = 0.0

    # ── Dynamic range ─────────────────────────────────────────────────────────
    ref_dr = ref_de.get("dynamic_range_db")
    src_dr = src_de.get("dynamic_range_db")
    if ref_dr is not None and src_dr is not None:
        dr_delta = round(float(ref_dr) - float(src_dr), 2)
        delta["dynamic_range_delta_db"] = dr_delta
        if dr_delta > 2:
            ratio = round(1.0 + dr_delta / 20.0, 3)
            summary["dynamics"] = (
                f"Source is {dr_delta:.1f} dB more compressed than reference. "
                f"Suggested expansion ratio ~{ratio:.2f}:1."
            )
        elif dr_delta < -2:
            summary["dynamics"] = (
                f"Source has {abs(dr_delta):.1f} dB more dynamic range than reference. "
                "Light compression may be needed."
            )
        else:
            summary["dynamics"] = "Dynamic range closely matched."
        # Expansion ratio hint: 1 dB DR difference ≈ 0.05 ratio
        delta["compression_ratio_hint"] = round(1.0 + max(dr_delta, 0) / 20.0, 3)
    else:
        delta["dynamic_range_delta_db"]  = 0.0
        delta["compression_ratio_hint"]  = 1.0

    # ── Attack / release ──────────────────────────────────────────────────────
    ref_att = ref_de.get("attack_time_ms",  0)
    src_att = src_de.get("attack_time_ms",  0)
    ref_rel = ref_de.get("release_time_ms", 0)
    src_rel = src_de.get("release_time_ms", 0)
    delta["attack_delta_ms"]  = round(float(ref_att) - float(src_att),  2)
    delta["release_delta_ms"] = round(float(ref_rel) - float(src_rel), 2)

    # ── Hum delta ─────────────────────────────────────────────────────────────
    ref_hum = ref_np.get("hum_50hz", {}).get("harmonic_levels_db", {})
    src_hum = src_np.get("hum_50hz", {}).get("harmonic_levels_db", {})
    hum_delta = {}
    for f_str in ref_hum:
        rd = ref_hum.get(f_str, -120)
        sd = src_hum.get(f_str, -120)
        hum_delta[f_str] = round(float(rd) - float(sd), 2)
    delta["hum_delta_db"] = hum_delta
    if any(v > 3 for v in hum_delta.values()):
        summary["hum"] = "Reference has higher hum content than source — unusual. Check signal chain."
    elif any(v < -3 for v in hum_delta.values()):
        summary["hum"] = "Source has elevated hum vs reference. Hum removal recommended."

    # ── Harmonic distortion deltas ────────────────────────────────────────────
    ref_harm = ref_hp.get("harmonic_levels_db", {})
    src_harm = src_hp.get("harmonic_levels_db", {})
    harm_delta = {}
    for h in [f"H{i}" for i in range(2, 9)]:
        rd = ref_harm.get(h, -120)
        sd = src_harm.get(h, -120)
        harm_delta[h] = round(float(rd) - float(sd), 2)
    delta["harmonic_deltas_db"] = harm_delta

    ref_thd = ref_hp.get("thd_percent", 0)
    src_thd = src_hp.get("thd_percent", 0)
    delta["thd_delta_percent"] = round(float(ref_thd) - float(src_thd), 4)

    ref_char = ref_hp.get("harmonic_character", "")
    src_char = src_hp.get("harmonic_character", "")
    if ref_char != src_char:
        summary["harmonics"] = (
            f"Harmonic character mismatch: source is '{src_char}', "
            f"reference is '{ref_char}'."
        )

    # ── Transient delta ───────────────────────────────────────────────────────
    ref_td = ref_tp.get("transient_density_per_sec", 0)
    src_td = src_tp.get("transient_density_per_sec", 0)
    delta["transient_density_delta"] = round(float(ref_td) - float(src_td), 3)

    ref_tnr = ref_tp.get("transient_to_noise_ratio_db", 0)
    src_tnr = src_tp.get("transient_to_noise_ratio_db", 0)
    delta["tnr_delta_db"] = round(float(ref_tnr) - float(src_tnr), 2)

    # ── EIN Lab VST3 preset hints ─────────────────────────────────────────────
    delta["ein_lab_preset"] = {
        "gate_threshold_db":   round(float(src_np.get("noise_floor_pp_db", -60)), 2),
        "noise_figure_db":     round(float(src_np.get("noise_floor_rms_db", -80)), 2),
        "corner_freq_hz":      float(src_np.get("corner_frequency_hz", 1000)),
        "pink_slope":          float(src_np.get("pink_noise_slope", -2)),
        "hum_freq_hz":         50.0 if src_np.get("hum_mains") == "50Hz" else
                               60.0 if src_np.get("hum_mains") == "60Hz" else 0.0,
        "hum_detected":        bool(src_np.get("hum_detected", False)),
        "suggested_eq_tilt_db_per_octave": round(delta.get("pink_slope_correction", 0) * 3, 2),
        "noise_floor_target_db": float(ref_np.get("noise_floor_rms_db", -80)) if ref_np else -80.0,
    }

    # ── WaveLab JavaScript preset stub ───────────────────────────────────────
    eq_pts = [(int(f), db) for f, db in eq_corr.items() if abs(db) > 0.5]
    eq_pts.sort(key=lambda x: x[0])
    wl_eq_str = "\n".join(
        f'    eqBand({i}, {f}, {db:.2f}, 0.7);  // {f} Hz'
        for i, (f, db) in enumerate(eq_pts[:12])
    )
    delta["wavlab_stub"] = f"""// WaveLab Preset Stub — generated by UNMP3 remeta delta
// Apply to source file to match reference characteristics
// Gate threshold: {delta['ein_lab_preset']['gate_threshold_db']:.1f} dBFS
// Noise floor offset: {delta.get('noise_floor_offset_db', 0):.1f} dB
// Dynamic range delta: {delta.get('dynamic_range_delta_db', 0):.1f} dB

function applyCorrection(clip) {{
{wl_eq_str}
    // Noise reduction: set gate at {delta['ein_lab_preset']['gate_threshold_db']:.1f} dBFS
    // Expansion ratio: {delta.get('compression_ratio_hint', 1.0):.2f}:1
    // Hum filter: {delta['ein_lab_preset']['hum_freq_hz']} Hz ({'detected' if delta['ein_lab_preset']['hum_detected'] else 'not detected'})
}}
"""

    # ── Generic EQ curve points ───────────────────────────────────────────────
    delta["eq_curve_points"] = eq_pts
    delta["summary"]         = summary

    return delta


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _print_analysis(result):
    print(f"\n{'═'*64}")
    print(f"  DEEP ANALYSIS v{result.get('analysis_version','?')}  ·  {result.get('source','')}")
    print(f"{'═'*64}")

    seg = result.get("noise_segment_info", {})
    print(f"\n  ┌─ Noise Segment ──────────────────────────────────────────┐")
    print(f"  │  Location      {seg.get('start_sec', 0):.3f}s – {seg.get('end_sec', 0):.3f}s")
    print(f"  │  RMS           {seg.get('rms_db', 0):.1f} dBFS")
    print(f"  │  Quiet segment {'YES' if seg.get('has_quiet_seg') else 'NO ⚠'}")
    if seg.get("warning"):
        print(f"  │  ⚠  {seg['warning'][:56]}")
    print(f"  └{'─'*60}┘")

    np_ = result.get("noise_profile", {})
    print(f"\n  ┌─ Noise Profile ──────────────────────────────────────────┐")
    print(f"  │  Floor RMS     {np_.get('noise_floor_rms_db', 0):.2f} dBFS")
    print(f"  │  6.6σ ceiling  {np_.get('noise_floor_pp_db', 0):.2f} dBFS")
    print(f"  │  Character     {np_.get('noise_character', '?')}")
    print(f"  │  1/f slope     {np_.get('pink_noise_slope', 0):.3f}")
    print(f"  │  Corner freq   {np_.get('corner_frequency_hz', 0):.0f} Hz")
    print(f"  │  White density {np_.get('white_noise_density_db_per_sqrt_hz', 0):.1f} dB/√Hz")
    print(f"  │  Hum           {np_.get('hum_mains', 'none')}")
    print(f"  └{'─'*60}┘")

    de = result.get("dynamic_envelope", {})
    print(f"\n  ┌─ Dynamic Envelope ───────────────────────────────────────┐")
    print(f"  │  Dynamic range  {de.get('dynamic_range_db', 0):.1f} dB")
    print(f"  │  Compression    {de.get('compression_estimate', '?')}")
    print(f"  │  Attack         {de.get('attack_time_ms', 0):.1f} ms")
    print(f"  │  Release        {de.get('release_time_ms', 0):.1f} ms")
    print(f"  └{'─'*60}┘")

    tr = result.get("transient_profile", {})
    print(f"\n  ┌─ Transient Character ────────────────────────────────────┐")
    print(f"  │  Count          {tr.get('transient_count', 0)}")
    print(f"  │  Density        {tr.get('transient_density_per_sec', 0):.2f}/sec")
    print(f"  │  Avg attack     {tr.get('avg_attack_ms', 0):.1f} ms")
    print(f"  │  TNR            {tr.get('transient_to_noise_ratio_db', 0):.1f} dB")
    print(f"  │  Character      {tr.get('transient_character', '?')}")
    print(f"  └{'─'*60}┘")

    hp = result.get("harmonic_profile", {})
    print(f"\n  ┌─ Harmonic Signature ─────────────────────────────────────┐")
    print(f"  │  Fundamental    {hp.get('fundamental_hz', 0):.1f} Hz  ({hp.get('fundamental_db', 0):.1f} dBFS)")
    print(f"  │  THD            {hp.get('thd_percent', 0):.3f}%")
    print(f"  │  Character      {hp.get('harmonic_character', '?')}")
    print(f"  │  Flatness       {hp.get('spectral_flatness', 0):.4f}")
    for h, db in (hp.get("harmonic_levels_db") or {}).items():
        print(f"  │  {h:<8}       {db:+.1f} dB (rel. fundamental)")
    print(f"  └{'─'*60}┘")
    print()


def _cli():
    import argparse
    parser = argparse.ArgumentParser(
        description="UNMP3 v4 — Deep audio analysis and profile delta")
    sub = parser.add_subparsers(dest="cmd")

    p_an = sub.add_parser("analyze",  help="Full deep analysis of a WAV file")
    p_an.add_argument("wav",    help="Input WAV")
    p_an.add_argument("output", help="Output .remeta file (merges into existing if present)")

    p_dn = sub.add_parser("denoise",  help="Spectral subtraction denoising")
    p_dn.add_argument("input",  help="Input WAV")
    p_dn.add_argument("output", help="Output denoised WAV")
    p_dn.add_argument("--alpha", type=float, default=1.5,
                      help="Over-subtraction factor (1.0–2.0, default 1.5)")

    p_dl = sub.add_parser("delta",    help="Compute profile delta between two .remeta files")
    p_dl.add_argument("reference",   help="Reference .remeta")
    p_dl.add_argument("source",      help="Source .remeta")
    p_dl.add_argument("--output",    help="Save delta JSON to file")
    p_dl.add_argument("--wavlab",    help="Save WaveLab JS stub to file")

    args = parser.parse_args()

    if args.cmd == "analyze":
        # Load existing remeta if present
        existing = {}
        if Path(args.output).exists():
            with open(args.output) as f:
                existing = json.load(f)
        analysis = run_full_analysis(args.wav)
        _print_analysis(analysis)
        existing["deep_analysis"] = analysis
        with open(args.output, "w") as f:
            json.dump(existing, f, indent=2)
        print(f"✅ Deep analysis saved → {args.output}")

    elif args.cmd == "denoise":
        params = denoise_file(args.input, args.output,
                               over_subtraction=args.alpha)
        print(f"✅ Denoised → {args.output}")
        for k, v in params.items():
            print(f"   {k}: {v}")

    elif args.cmd == "delta":
        with open(args.reference) as f: ref = json.load(f)
        with open(args.source)    as f: src = json.load(f)
        delta = compute_profile_delta(ref, src)
        print(f"\n{'═'*64}")
        print("  PROFILE DELTA  (reference ← source)")
        print(f"{'═'*64}")
        for k, v in delta.get("summary", {}).items():
            print(f"  {v}")
        print(f"\n  EQ curve: {len(delta.get('eq_curve_points',[]))} correction points")
        print(f"  Noise floor offset:   {delta.get('noise_floor_offset_db', 0):+.1f} dB")
        print(f"  Dynamic range delta:  {delta.get('dynamic_range_delta_db', 0):+.1f} dB")
        print(f"  Expansion ratio hint: {delta.get('compression_ratio_hint', 1.0):.2f}:1")
        if args.output:
            with open(args.output, "w") as f:
                json.dump(delta, f, indent=2, default=str)
            print(f"\n✅ Delta saved → {args.output}")
        if args.wavlab:
            with open(args.wavlab, "w") as f:
                f.write(delta.get("wavlab_stub", ""))
            print(f"✅ WaveLab stub → {args.wavlab}")

    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
