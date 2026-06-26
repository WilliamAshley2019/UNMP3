#!/usr/bin/env python3
"""
remeta.py — UNMP3 Metadata Sidecar Format
==========================================
Reads, writes, and extracts the .remeta companion file for the UNMP3 ecosystem.

A .remeta file is a JSON sidecar that preserves everything an MP3 cannot hold:
  - Original WAV structural properties (sample rate, bit depth, channels, size)
  - Broadcast Wave Format (BWF / bext chunk) professional production metadata
  - Encoder telemetry (detected delay, padding, scaling factors)
  - Optional user-supplied descriptive metadata

The three-file ecosystem:
    song.mp3      — lossy consumer/streaming audio
    song.unmp3    — FLAC-compressed residual (lossless correction layer)
    song.remeta   — JSON metadata sidecar (this file's domain)

Together they reconstruct a bit-perfect WAV with all original metadata intact.

Usage (standalone):
    python remeta.py extract input.wav output.remeta
    python remeta.py show    input.remeta
    python remeta.py apply   input.remeta output.wav

Dependencies: numpy (for encoder delay detection), ffmpeg in PATH
"""

import json
import os
import struct
import subprocess
import datetime
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Schema — default field structure with descriptions
# ─────────────────────────────────────────────────────────────────────────────

REMETA_VERSION = "1.0"

# Human-readable field descriptions used by the GUI
FIELD_DESCRIPTIONS = {
    # File properties (auto-extracted, not user-editable)
    "source_filename":       "Original WAV filename",
    "file_size_bytes":       "Original WAV file size in bytes",
    "sample_rate":           "Sample rate in Hz (e.g. 44100, 48000, 96000)",
    "channels":              "Number of audio channels (1=mono, 2=stereo)",
    "bit_depth":             "Bit depth of original WAV (16, 24, 32)",
    "duration_seconds":      "Duration of audio in seconds",
    "total_samples":         "Total sample frames in original file",
    "audio_format":          "PCM encoding type (PCM_16, PCM_24, PCM_32F)",

    # Encoder telemetry (auto-computed during encode)
    "encoder_delay_samples": "Samples of silence prepended by MP3 encoder (libmp3lame typically 1105)",
    "encoder_padding_samples":"Samples of silence appended by MP3 encoder for frame alignment",
    "unmp3_bitrate":         "MP3 bitrate used for the companion .mp3 file",
    "encode_timestamp":      "UTC timestamp when this ecosystem was created",

    # BWF / Broadcast Wave Format fields (user-editable)
    "bwf_description":       "Free-text description of the audio content (max 256 chars)",
    "bwf_originator":        "Name of the originating organisation or DAW",
    "bwf_originator_reference": "Unique reference string from the originator (max 32 chars)",
    "bwf_origination_date":  "Date of origination (YYYY-MM-DD)",
    "bwf_origination_time":  "Time of origination (HH:MM:SS)",
    "bwf_time_reference":    "Sample count from midnight for timecode sync (BWF TimeReference)",
    "bwf_coding_history":    "History of processing applied to this audio",

    # User metadata (freely editable)
    "title":                 "Track or project title",
    "artist":                "Artist or performer name",
    "album":                 "Album or project name",
    "track_number":          "Track number within album/project",
    "year":                  "Year of recording or release",
    "genre":                 "Genre classification",
    "composer":              "Composer or songwriter",
    "publisher":             "Publisher or label",
    "isrc":                  "ISRC code (International Standard Recording Code, XX-XXX-YY-NNNNN)",
    "copyright":             "Copyright notice",
    "comment":               "General notes or comments",
    "project":               "Project or session name",
    "engineer":              "Recording or mix engineer name",
    "studio":                "Recording studio or location",
    "bpm":                   "Tempo in beats per minute",
    "key":                   "Musical key (e.g. C major, F# minor)",
    "tags":                  "Comma-separated custom tags",
}

# Which fields are auto-extracted (read-only in GUI)
AUTO_FIELDS = {
    "source_filename", "file_size_bytes", "sample_rate", "channels",
    "bit_depth", "duration_seconds", "total_samples", "audio_format",
    "encoder_delay_samples", "encoder_padding_samples",
    "unmp3_bitrate", "encode_timestamp",
    "bwf_origination_date", "bwf_origination_time",  # can be overridden
}

# GUI tab groupings
FIELD_GROUPS = {
    "File Properties": [
        "source_filename", "file_size_bytes", "sample_rate", "channels",
        "bit_depth", "duration_seconds", "total_samples", "audio_format",
    ],
    "Encoder Telemetry": [
        "encoder_delay_samples", "encoder_padding_samples",
        "unmp3_bitrate", "encode_timestamp",
    ],
    "Broadcast (BWF)": [
        "bwf_description", "bwf_originator", "bwf_originator_reference",
        "bwf_origination_date", "bwf_origination_time",
        "bwf_time_reference", "bwf_coding_history",
    ],
    "Track Info": [
        "title", "artist", "album", "track_number", "year",
        "genre", "composer", "publisher", "isrc", "copyright",
    ],
    "Production Notes": [
        "comment", "project", "engineer", "studio",
        "bpm", "key", "tags",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# WAV / BWF header parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_wav_header(wav_path):
    """
    Parse the binary RIFF/WAV header to extract:
      - fmt  chunk: sample_rate, channels, bit_depth, audio_format
      - bext chunk: BWF broadcast extension metadata
      - data chunk: size → total_samples, duration
    Returns a dict; missing fields are left as empty strings.
    """
    result = {
        "file_size_bytes":    os.path.getsize(wav_path),
        "sample_rate":        "",
        "channels":           "",
        "bit_depth":          "",
        "audio_format":       "",
        "total_samples":      "",
        "duration_seconds":   "",
        "bwf_description":    "",
        "bwf_originator":     "",
        "bwf_originator_reference": "",
        "bwf_origination_date":     "",
        "bwf_origination_time":     "",
        "bwf_time_reference":       "",
        "bwf_coding_history":       "",
    }

    try:
        with open(wav_path, "rb") as f:
            # RIFF header
            riff = f.read(12)
            if len(riff) < 12 or riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
                return result  # not a WAV

            data_size = 0
            fmt_parsed = False

            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                chunk_id, chunk_size = struct.unpack("<4sI", hdr)
                chunk_start = f.tell()

                if chunk_id == b"fmt ":
                    fmt = f.read(min(chunk_size, 40))
                    audio_fmt_code = struct.unpack_from("<H", fmt, 0)[0]
                    num_ch         = struct.unpack_from("<H", fmt, 2)[0]
                    sr             = struct.unpack_from("<I", fmt, 4)[0]
                    bps            = struct.unpack_from("<H", fmt, 14)[0] if len(fmt) > 14 else 0

                    result["channels"]    = num_ch
                    result["sample_rate"] = sr
                    result["bit_depth"]   = bps

                    fmt_map = {1: "PCM_16", 3: "PCM_32F", 65534: "PCM_EXT"}
                    if audio_fmt_code in fmt_map:
                        label = fmt_map[audio_fmt_code]
                        if audio_fmt_code == 1 and bps == 24:
                            label = "PCM_24"
                        elif audio_fmt_code == 1 and bps == 32:
                            label = "PCM_32"
                    else:
                        label = f"FMT_{audio_fmt_code}"
                    result["audio_format"] = label
                    fmt_parsed = True

                elif chunk_id == b"data":
                    data_size = chunk_size
                    if fmt_parsed and result["channels"] and result["sample_rate"] and result["bit_depth"]:
                        bytes_per_sample = result["bit_depth"] // 8
                        total = data_size // (bytes_per_sample * result["channels"])
                        result["total_samples"]    = total
                        result["duration_seconds"] = round(total / result["sample_rate"], 6)

                elif chunk_id == b"bext":
                    bext = f.read(chunk_size)
                    def _str(b, start, length):
                        return b[start:start+length].rstrip(b"\x00").decode("ascii", errors="replace").strip()

                    result["bwf_description"]          = _str(bext,   0, 256)
                    result["bwf_originator"]           = _str(bext, 256,  32)
                    result["bwf_originator_reference"] = _str(bext, 288,  32)
                    result["bwf_origination_date"]     = _str(bext, 320,  10)
                    result["bwf_origination_time"]     = _str(bext, 330,   8)

                    if len(bext) >= 346:
                        tref_lo, tref_hi = struct.unpack_from("<II", bext, 338)
                        tref = (tref_hi << 32) | tref_lo
                        result["bwf_time_reference"] = tref

                    if len(bext) > 602:
                        result["bwf_coding_history"] = _str(bext, 602, len(bext) - 602)

                # Seek to next chunk (chunks are word-aligned)
                next_pos = chunk_start + chunk_size + (chunk_size % 2)
                f.seek(next_pos)

    except Exception as e:
        result["_parse_error"] = str(e)

    return result


def _detect_encoder_delay(wav_path, mp3_path):
    """
    Use cross-correlation on a 1-second window to detect the sample delay
    introduced by the MP3 encoder (libmp3lame typically prepends 1105 samples).
    Returns (delay_samples, padding_samples).
    Falls back gracefully if numpy is unavailable.
    """
    try:
        import numpy as np
        import tempfile

        tmp = Path(tempfile.gettempdir())
        pid = os.getpid()

        def read_raw(path, sr, ch):
            out = tmp / f"dly_{pid}_{id(path)}.f64"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(path),
                "-f", "f64le", "-acodec", "pcm_f64le",
                "-ar", str(sr), "-ac", str(ch),
                str(out)
            ], check=True)
            data = np.fromfile(str(out), dtype=np.float64)
            out.unlink(missing_ok=True)
            return data.reshape(-1, ch)

        # Get WAV properties
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channels",
            "-of", "json", str(wav_path)
        ], capture_output=True, text=True)
        info = json.loads(probe.stdout)["streams"][0]
        sr = int(info["sample_rate"])
        ch = int(info["channels"])

        orig = read_raw(wav_path, sr, ch)
        dec  = read_raw(mp3_path, sr, ch)

        # Use mono mix of first second for correlation
        window = min(sr, len(orig), len(dec))
        orig_w = orig[:window, 0]
        dec_w  = dec[:window, 0]

        corr  = np.correlate(dec_w, orig_w, mode="full")
        delay = int(np.argmax(corr)) - (window - 1)
        delay = max(0, delay)  # negative delay is physically impossible here

        # Padding = extra samples at end of decoded vs aligned original
        aligned_len = len(orig)
        decoded_len = len(dec)
        padding = max(0, decoded_len - delay - aligned_len)

        return delay, padding

    except Exception:
        # If anything fails (numpy missing, ffmpeg issue) return defaults
        return 1105, 576


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def create_remeta(wav_path, mp3_path=None, bitrate=None, user_fields=None):
    """
    Build and return a remeta dict from a WAV file.
    Optionally cross-correlates with mp3_path to detect encoder delay.
    user_fields: dict of any FIELD_DESCRIPTIONS keys to overlay.
    """
    wav_path = Path(wav_path)

    payload = {
        "remeta_version":    REMETA_VERSION,
        "source_filename":   wav_path.name,
        "encode_timestamp":  datetime.datetime.utcnow().isoformat() + "Z",
        "unmp3_bitrate":     bitrate or "",
    }

    # Extract WAV header fields
    header = _parse_wav_header(wav_path)
    payload.update(header)

    # Auto-fill BWF date/time if not in file
    now = datetime.datetime.utcnow()
    if not payload.get("bwf_origination_date"):
        payload["bwf_origination_date"] = now.strftime("%Y-%m-%d")
    if not payload.get("bwf_origination_time"):
        payload["bwf_origination_time"] = now.strftime("%H:%M:%S")

    # Encoder delay detection
    if mp3_path and Path(mp3_path).exists():
        delay, padding = _detect_encoder_delay(wav_path, mp3_path)
    else:
        delay, padding = 1105, 576  # libmp3lame defaults
    payload["encoder_delay_samples"]   = delay
    payload["encoder_padding_samples"] = padding

    # Empty slots for all user fields not yet populated
    for field in FIELD_DESCRIPTIONS:
        if field not in payload:
            payload[field] = ""

    # Overlay user-supplied fields
    if user_fields:
        for k, v in user_fields.items():
            if k in FIELD_DESCRIPTIONS:
                payload[k] = v

    return payload


def save_remeta(payload, remeta_path):
    """Write a remeta dict to a .remeta JSON file."""
    with open(remeta_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def load_remeta(remeta_path):
    """Load and return a remeta dict from a .remeta file."""
    with open(remeta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_remeta_to_wav(remeta_path, wav_path, output_wav_path=None):
    """
    Bake the metadata from a .remeta file into a WAV file's ID3/INFO tags
    via ffmpeg metadata mapping.  BWF fields are written where ffmpeg supports them.
    If output_wav_path is None, overwrites wav_path in-place (via temp file).
    """
    import tempfile, shutil

    meta = load_remeta(remeta_path)
    output_wav_path = output_wav_path or wav_path

    # Map remeta fields → ffmpeg metadata key=value pairs
    ff_meta = {}
    field_map = {
        "title":       "title",
        "artist":      "artist",
        "album":       "album",
        "track_number":"track",
        "year":        "date",
        "genre":       "genre",
        "composer":    "composer",
        "publisher":   "publisher",
        "isrc":        "isrc",
        "copyright":   "copyright",
        "comment":     "comment",
        "bwf_description":   "description",
        "bwf_originator":    "originator",
        "bwf_originator_reference": "originator_reference",
        "bwf_origination_date":     "origination_date",
        "bwf_origination_time":     "origination_time",
        "bwf_coding_history":       "coding_history",
        "engineer":    "engineer",
        "project":     "album_artist",  # closest ffmpeg equivalent
    }
    for remeta_key, ff_key in field_map.items():
        val = meta.get(remeta_key, "")
        if val:
            ff_meta[ff_key] = str(val)

    tmp = Path(tempfile.gettempdir()) / f"remeta_apply_{os.getpid()}.wav"

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(wav_path)]
    for k, v in ff_meta.items():
        cmd += ["-metadata", f"{k}={v}"]
    cmd += ["-c:a", "copy", str(tmp)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg metadata write failed: {result.stderr}")

    shutil.move(str(tmp), str(output_wav_path))
    return output_wav_path


def print_remeta(meta):
    """Pretty-print a remeta dict to stdout."""
    print(f"\n{'='*60}")
    print(f"REMETA  v{meta.get('remeta_version','?')}  —  {meta.get('source_filename','')}")
    print(f"{'='*60}")
    for group, fields in FIELD_GROUPS.items():
        header_printed = False
        for field in fields:
            val = meta.get(field, "")
            if val == "" or val is None:
                continue
            if not header_printed:
                print(f"\n  [{group}]")
                header_printed = True
            label = FIELD_DESCRIPTIONS.get(field, field)
            print(f"    {field:<32} {val}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="UNMP3 .remeta sidecar tool")
    sub = parser.add_subparsers(dest="cmd")

    p_ex = sub.add_parser("extract", help="Extract metadata from WAV → .remeta")
    p_ex.add_argument("wav",    help="Input WAV file")
    p_ex.add_argument("output", help="Output .remeta file")
    p_ex.add_argument("--mp3",  help="Companion MP3 for encoder delay detection")
    p_ex.add_argument("--bitrate", default="", help="MP3 bitrate used")

    p_sh = sub.add_parser("show", help="Display .remeta contents")
    p_sh.add_argument("remeta", help=".remeta file to display")

    p_ap = sub.add_parser("apply", help="Apply .remeta metadata to a WAV file")
    p_ap.add_argument("remeta", help="Source .remeta file")
    p_ap.add_argument("wav",    help="Target WAV file")
    p_ap.add_argument("--output", help="Output WAV (default: overwrite input)")

    args = parser.parse_args()

    if args.cmd == "extract":
        meta = create_remeta(args.wav, mp3_path=args.mp3, bitrate=args.bitrate)
        save_remeta(meta, args.output)
        print(f"✅ Saved {args.output}")
        print_remeta(meta)
    elif args.cmd == "show":
        meta = load_remeta(args.remeta)
        print_remeta(meta)
    elif args.cmd == "apply":
        out = apply_remeta_to_wav(args.remeta, args.wav, args.output)
        print(f"✅ Metadata applied → {out}")
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
