#!/usr/bin/env python3
"""
remeta.py — UNMP3 Metadata Sidecar Format  v2.0
=================================================
Comprehensive metadata extraction, acoustic analysis, and cross-schema writing
for the UNMP3 three-file ecosystem.

Extracts and maps metadata across:
  WAV/RIFF INFO chunk   → standard Windows DAW metadata
  BWF bext chunk        → EBU broadcast production fields
  iXML chunk            → XML production metadata (Pro Tools, Nuendo, Sequoia)
  aXML chunk            → extended XML (timecode, project)
  CART chunk            → broadcast automation (RadioTraffic, RCS, Selector)
  ID3 tags (from MP3)   → ID3v2.3/2.4 + iTunes atoms
  Acoustic analysis     → BPM, musical key, LUFS, peak, dynamic range, pitch

All extracted data is stored in one .remeta JSON file and can be written back
to a reconstructed WAV using ffmpeg metadata injection.

Usage (standalone):
    python remeta.py extract input.wav output.remeta [--mp3 input.mp3]
    python remeta.py show    input.remeta
    python remeta.py apply   input.remeta output.wav [--output fixed.wav]
    python remeta.py analyze input.wav output.remeta   # acoustic analysis only

Dependencies: numpy, ffmpeg/ffprobe in PATH
Optional:     mutagen (pip install mutagen)  — richer ID3/iTunes tag reading
"""

import json
import os
import struct
import subprocess
import datetime
import math
import tempfile
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Version & Schema
# ─────────────────────────────────────────────────────────────────────────────

REMETA_VERSION = "2.0"

# ── Field descriptions (shown in GUI) ────────────────────────────────────────
FIELD_DESCRIPTIONS = {
    # ── File / Technical ──────────────────────────────────────────────────
    "source_filename":          "Original WAV filename",
    "file_size_bytes":          "Original WAV file size in bytes",
    "sample_rate":              "Sample rate in Hz (44100, 48000, 96000…)",
    "channels":                 "Number of audio channels (1=mono 2=stereo)",
    "bit_depth":                "Bit depth of original WAV (16, 24, 32)",
    "duration_seconds":         "Duration in seconds",
    "total_samples":            "Total sample frames in original file",
    "audio_format":             "PCM encoding type (PCM_16, PCM_24, PCM_32F)",

    # ── Encoder Telemetry ────────────────────────────────────────────────
    "encoder_delay_samples":    "Samples prepended by MP3 encoder (libmp3lame ~1105)",
    "encoder_padding_samples":  "Samples appended by MP3 encoder for frame alignment",
    "unmp3_bitrate":            "MP3 bitrate used for companion .mp3",
    "encode_timestamp":         "UTC timestamp of UNMP3 ecosystem creation",

    # ── Acoustic Analysis (auto) ─────────────────────────────────────────
    "bpm":                      "Tempo in BPM (auto-detected via beat tracking)",
    "bpm_confidence":           "Beat detection confidence 0–1",
    "musical_key":              "Detected musical key (e.g. C major, F# minor)",
    "key_confidence":           "Key detection confidence 0–1",
    "root_note":                "Root note (C, C#, D … B)",
    "scale":                    "Scale type (major / minor)",
    "loudness_lufs":            "Integrated loudness LUFS (EBU R128)",
    "loudness_range_lu":        "Loudness range LU (EBU R128 LRA)",
    "true_peak_dbtp":           "True peak level dBTP",
    "rms_db":                   "RMS level dB",
    "dynamic_range_db":         "Crest factor / dynamic range dB",
    "zero_crossings_per_sec":   "Zero-crossing rate (brightness/noisiness indicator)",
    "spectral_centroid_hz":     "Spectral centroid Hz (timbral brightness)",
    "pitch_hz":                 "Dominant fundamental pitch Hz (mono/lead content)",
    "pitch_note":               "Dominant pitch as musical note (e.g. A4 = 440 Hz)",

    # ── BWF / Broadcast Wave Format ──────────────────────────────────────
    "bwf_description":          "Audio content description (max 256 chars)",
    "bwf_originator":           "Originating organisation or DAW name",
    "bwf_originator_reference": "Originator unique reference (max 32 chars)",
    "bwf_origination_date":     "Date of origination YYYY-MM-DD",
    "bwf_origination_time":     "Time of origination HH:MM:SS",
    "bwf_time_reference":       "Sample count from midnight (timecode sync)",
    "bwf_version":              "BWF bext chunk version (0 or 1)",
    "bwf_umid":                 "SMPTE UMID (64 hex chars) — unique material ID",
    "bwf_loudness_value":       "BWF v2 integrated loudness × 100 (hundredths LUFS)",
    "bwf_loudness_range":       "BWF v2 loudness range × 100",
    "bwf_max_true_peak":        "BWF v2 max true peak × 100 (hundredths dBTP)",
    "bwf_max_momentary_loudness":"BWF v2 max momentary loudness × 100",
    "bwf_max_short_term_loudness":"BWF v2 max short-term loudness × 100",
    "bwf_coding_history":       "Processing history applied to the audio",

    # ── RIFF INFO chunk ──────────────────────────────────────────────────
    "riff_inam":                "INAM — Track/clip name",
    "riff_iart":                "IART — Artist",
    "riff_iprd":                "IPRD — Album / product",
    "riff_icmt":                "ICMT — Comment",
    "riff_ignr":                "IGNR — Genre",
    "riff_icrd":                "ICRD — Creation date",
    "riff_ieng":                "IENG — Engineer",
    "riff_isft":                "ISFT — Software / DAW",
    "riff_icop":                "ICOP — Copyright",
    "riff_isbj":                "ISBJ — Subject",
    "riff_ikey":                "IKEY — Keywords",
    "riff_imed":                "IMED — Medium (e.g. Digital)",
    "riff_isrc":                "ISRC — Source (origin description, not ISRC code)",
    "riff_itch":                "ITCH — Technician",
    "riff_torg":                "IORG — Organisation",

    # ── iXML chunk ───────────────────────────────────────────────────────
    "ixml_project":             "iXML PROJECT",
    "ixml_scene":               "iXML SCENE",
    "ixml_take":                "iXML TAKE",
    "ixml_tape":                "iXML TAPE",
    "ixml_circle":              "iXML CIRCLED (best take flag)",
    "ixml_no_good":             "iXML NO_GOOD flag",
    "ixml_false_start":         "iXML FALSE_START flag",
    "ixml_wild_track":          "iXML WILD_TRACK flag",
    "ixml_note":                "iXML NOTE (free text)",
    "ixml_user":                "iXML USER (custom XML blob)",

    # ── CART chunk (broadcast automation) ───────────────────────────────
    "cart_title":               "CART Title (broadcast automation)",
    "cart_artist":              "CART Artist",
    "cart_cut_id":              "CART CutID (automation system ID)",
    "cart_client_id":           "CART ClientID",
    "cart_category":            "CART Category",
    "cart_classification":      "CART Classification",
    "cart_out_cue":             "CART OutCue text",
    "cart_start_date":          "CART StartDate YYYY/MM/DD",
    "cart_start_time":          "CART StartTime HH:MM:SS",
    "cart_end_date":            "CART EndDate YYYY/MM/DD",
    "cart_end_time":            "CART EndTime HH:MM:SS",
    "cart_producer_app_id":     "CART ProducerAppID",
    "cart_level_reference":     "CART LevelReference (0dBFS ref point)",
    "cart_post_timer":          "CART PostTimer (ms to next event)",
    "cart_user_def":            "CART UserDef (free string)",

    # ── Track / Library metadata ─────────────────────────────────────────
    "title":                    "Track or project title",
    "artist":                   "Artist or performer",
    "album":                    "Album or project name",
    "album_artist":             "Album artist (compilation field)",
    "track_number":             "Track number (e.g. 3 or 3/12)",
    "disc_number":              "Disc number (e.g. 1/2)",
    "year":                     "Year of recording or release",
    "genre":                    "Genre",
    "composer":                 "Composer / songwriter",
    "lyricist":                 "Lyricist",
    "publisher":                "Publisher / label",
    "isrc":                     "ISRC code XX-XXX-YY-NNNNN",
    "iswc":                     "ISWC code T-NNN.NNN.NNN-C",
    "catalog_number":           "Catalogue number",
    "barcode":                  "EAN/UPC barcode",
    "label":                    "Record label",
    "copyright":                "Copyright notice",
    "license":                  "License (e.g. CC BY 4.0)",
    "comment":                  "General comment",
    "lyrics":                   "Lyrics (plain text)",
    "language":                 "Language (ISO 639-2, e.g. eng)",
    "mood":                     "Mood / energy descriptor",
    "occasion":                 "Occasion / use-case tag",
    "tags":                     "Comma-separated custom tags",

    # ── Production / Session ─────────────────────────────────────────────
    "project":                  "Project / session name",
    "engineer":                 "Recording or mix engineer",
    "mixer":                    "Mix engineer (if different)",
    "mastering_engineer":       "Mastering engineer",
    "producer":                 "Producer",
    "studio":                   "Recording studio or location",
    "daw":                      "DAW used for recording/mixing",
    "daw_version":              "DAW version string",
    "sample_library":           "Sample library name (for one-shot/loop files)",
    "loop_type":                "Loop type (one-shot / loop / stem)",
    "loop_start_samples":       "Loop start point in samples",
    "loop_end_samples":         "Loop end point in samples",

    # ── FL Studio / DAW hint fields ──────────────────────────────────────
    "fl_tempo":                 "FL Studio tempo hint (mirrors bpm)",
    "fl_pitch":                 "FL Studio pitch semitones offset",
    "fl_color":                 "FL Studio mixer track colour (hex)",
    "timestretch_ratio":        "Timestretch ratio applied (1.0 = none)",
    "pitch_semitones":          "Pitch shift in semitones applied",
    "sample_start_ms":          "Cue: sample start offset in ms",
    "sample_end_ms":            "Cue: sample end offset in ms",
}

# ── Which fields are auto-populated (read-only in GUI unless overridden) ─────
AUTO_FIELDS = {
    "source_filename", "file_size_bytes", "sample_rate", "channels",
    "bit_depth", "duration_seconds", "total_samples", "audio_format",
    "encoder_delay_samples", "encoder_padding_samples",
    "unmp3_bitrate", "encode_timestamp",
    "bpm", "bpm_confidence", "musical_key", "key_confidence",
    "root_note", "scale",
    "loudness_lufs", "loudness_range_lu", "true_peak_dbtp",
    "rms_db", "dynamic_range_db", "zero_crossings_per_sec",
    "spectral_centroid_hz", "pitch_hz", "pitch_note",
    "bwf_origination_date", "bwf_origination_time",
    "bwf_version", "bwf_umid",
    "bwf_loudness_value", "bwf_loudness_range", "bwf_max_true_peak",
    "bwf_max_momentary_loudness", "bwf_max_short_term_loudness",
    "riff_inam", "riff_iart", "riff_iprd", "riff_icmt", "riff_ignr",
    "riff_icrd", "riff_ieng", "riff_isft", "riff_icop", "riff_isbj",
    "riff_ikey", "riff_imed", "riff_isrc", "riff_itch", "riff_torg",
    "ixml_project", "ixml_scene", "ixml_take", "ixml_tape",
    "ixml_circle", "ixml_no_good", "ixml_false_start",
    "ixml_wild_track", "ixml_note", "ixml_user",
    "cart_title", "cart_artist", "cart_cut_id", "cart_client_id",
    "cart_category", "cart_classification", "cart_out_cue",
    "cart_start_date", "cart_start_time", "cart_end_date", "cart_end_time",
    "cart_producer_app_id", "cart_level_reference",
    "cart_post_timer", "cart_user_def",
}

# ── GUI sub-tab groupings ─────────────────────────────────────────────────────
FIELD_GROUPS = {
    "File / Tech": [
        "source_filename", "file_size_bytes", "sample_rate", "channels",
        "bit_depth", "duration_seconds", "total_samples", "audio_format",
        "encoder_delay_samples", "encoder_padding_samples",
        "unmp3_bitrate", "encode_timestamp",
    ],
    "Acoustic": [
        "bpm", "bpm_confidence", "musical_key", "key_confidence",
        "root_note", "scale",
        "loudness_lufs", "loudness_range_lu", "true_peak_dbtp",
        "rms_db", "dynamic_range_db",
        "zero_crossings_per_sec", "spectral_centroid_hz",
        "pitch_hz", "pitch_note",
    ],
    "BWF / Broadcast": [
        "bwf_description", "bwf_originator", "bwf_originator_reference",
        "bwf_origination_date", "bwf_origination_time",
        "bwf_time_reference", "bwf_version", "bwf_umid",
        "bwf_loudness_value", "bwf_loudness_range", "bwf_max_true_peak",
        "bwf_max_momentary_loudness", "bwf_max_short_term_loudness",
        "bwf_coding_history",
    ],
    "RIFF INFO": [
        "riff_inam", "riff_iart", "riff_iprd", "riff_icmt", "riff_ignr",
        "riff_icrd", "riff_ieng", "riff_isft", "riff_icop", "riff_isbj",
        "riff_ikey", "riff_imed", "riff_isrc", "riff_itch", "riff_torg",
    ],
    "iXML / CART": [
        "ixml_project", "ixml_scene", "ixml_take", "ixml_tape",
        "ixml_circle", "ixml_no_good", "ixml_false_start",
        "ixml_wild_track", "ixml_note", "ixml_user",
        "cart_title", "cart_artist", "cart_cut_id", "cart_client_id",
        "cart_category", "cart_classification", "cart_out_cue",
        "cart_start_date", "cart_start_time", "cart_end_date", "cart_end_time",
        "cart_producer_app_id", "cart_level_reference",
        "cart_post_timer", "cart_user_def",
    ],
    "Track Info": [
        "title", "artist", "album", "album_artist", "track_number",
        "disc_number", "year", "genre", "composer", "lyricist",
        "publisher", "isrc", "iswc", "catalog_number", "barcode",
        "label", "copyright", "license", "comment", "lyrics",
        "language", "mood", "occasion", "tags",
    ],
    "Production": [
        "project", "engineer", "mixer", "mastering_engineer",
        "producer", "studio", "daw", "daw_version",
        "sample_library", "loop_type",
        "loop_start_samples", "loop_end_samples",
        "fl_tempo", "fl_pitch", "fl_color",
        "timestretch_ratio", "pitch_semitones",
        "sample_start_ms", "sample_end_ms",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# WAV binary chunk parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_wav_header(wav_path):
    """Parse all known RIFF chunks from a WAV file."""
    result = {k: "" for k in FIELD_DESCRIPTIONS}
    result["file_size_bytes"] = os.path.getsize(wav_path)

    # RIFF INFO four-cc → field name mapping
    INFO_MAP = {
        b"INAM": "riff_inam", b"IART": "riff_iart", b"IPRD": "riff_iprd",
        b"ICMT": "riff_icmt", b"IGNR": "riff_ignr", b"ICRD": "riff_icrd",
        b"IENG": "riff_ieng", b"ISFT": "riff_isft", b"ICOP": "riff_icop",
        b"ISBJ": "riff_isbj", b"IKEY": "riff_ikey", b"IMED": "riff_imed",
        b"ISRC": "riff_isrc", b"ITCH": "riff_itch", b"IORG": "riff_torg",
    }

    def _str(b, start=0, length=None):
        end = (start + length) if length else len(b)
        return b[start:end].rstrip(b"\x00").decode("utf-8", errors="replace").strip()

    try:
        with open(wav_path, "rb") as f:
            riff = f.read(12)
            if len(riff) < 12 or riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
                return result

            fmt_parsed = False

            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                chunk_id, chunk_size = struct.unpack("<4sI", hdr)
                chunk_start = f.tell()

                # ── fmt ──────────────────────────────────────────────────
                if chunk_id == b"fmt ":
                    fmt = f.read(min(chunk_size, 40))
                    afmt   = struct.unpack_from("<H", fmt, 0)[0]
                    nch    = struct.unpack_from("<H", fmt, 2)[0]
                    sr     = struct.unpack_from("<I", fmt, 4)[0]
                    bps    = struct.unpack_from("<H", fmt, 14)[0] if len(fmt) > 14 else 0
                    result["channels"]    = nch
                    result["sample_rate"] = sr
                    result["bit_depth"]   = bps
                    fmap = {1: "PCM", 3: "PCM_32F", 65534: "PCM_EXT"}
                    base = fmap.get(afmt, f"FMT_{afmt}")
                    if base == "PCM":
                        base = {8:"PCM_8",16:"PCM_16",24:"PCM_24",32:"PCM_32"}.get(bps, "PCM")
                    result["audio_format"] = base
                    fmt_parsed = True

                # ── data ─────────────────────────────────────────────────
                elif chunk_id == b"data":
                    ds = chunk_size
                    if fmt_parsed and result["channels"] and result["sample_rate"] and result["bit_depth"]:
                        bps_bytes = result["bit_depth"] // 8
                        total = ds // (bps_bytes * result["channels"])
                        result["total_samples"]    = total
                        result["duration_seconds"] = round(total / result["sample_rate"], 6)

                # ── bext (BWF) ────────────────────────────────────────────
                elif chunk_id == b"bext":
                    bext = f.read(chunk_size)
                    result["bwf_description"]          = _str(bext, 0, 256)
                    result["bwf_originator"]           = _str(bext, 256, 32)
                    result["bwf_originator_reference"] = _str(bext, 288, 32)
                    result["bwf_origination_date"]     = _str(bext, 320, 10)
                    result["bwf_origination_time"]     = _str(bext, 330, 8)
                    if len(bext) >= 346:
                        lo, hi = struct.unpack_from("<II", bext, 338)
                        result["bwf_time_reference"] = (hi << 32) | lo
                    if len(bext) >= 348:
                        result["bwf_version"] = struct.unpack_from("<H", bext, 346)[0]
                    if len(bext) >= 412:
                        result["bwf_umid"] = bext[348:412].hex().upper()
                    # BWF v2 loudness fields (412–422)
                    if len(bext) >= 422:
                        fields_v2 = struct.unpack_from("<hhhhh", bext, 412)
                        result["bwf_loudness_value"]           = fields_v2[0]
                        result["bwf_loudness_range"]           = fields_v2[1]
                        result["bwf_max_true_peak"]            = fields_v2[2]
                        result["bwf_max_momentary_loudness"]   = fields_v2[3]
                        result["bwf_max_short_term_loudness"]  = fields_v2[4]
                    if len(bext) > 602:
                        result["bwf_coding_history"] = _str(bext, 602)

                # ── LIST INFO ────────────────────────────────────────────
                elif chunk_id == b"LIST":
                    list_type = f.read(4)
                    if list_type == b"INFO":
                        end = chunk_start + chunk_size
                        while f.tell() < end - 8:
                            ih = f.read(8)
                            if len(ih) < 8:
                                break
                            iid, isz = struct.unpack("<4sI", ih)
                            raw = f.read(isz)
                            if iid in INFO_MAP:
                                result[INFO_MAP[iid]] = _str(raw)
                            if isz % 2:
                                f.read(1)
                        f.seek(chunk_start + chunk_size)
                        continue

                # ── iXML ─────────────────────────────────────────────────
                elif chunk_id == b"iXML":
                    raw = f.read(chunk_size)
                    xml = raw.decode("utf-8", errors="replace")
                    def _xtag(tag):
                        import re
                        m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
                        return m.group(1).strip() if m else ""
                    result["ixml_project"]     = _xtag("PROJECT")
                    result["ixml_scene"]       = _xtag("SCENE")
                    result["ixml_take"]        = _xtag("TAKE")
                    result["ixml_tape"]        = _xtag("TAPE")
                    result["ixml_circle"]      = _xtag("CIRCLED")
                    result["ixml_no_good"]     = _xtag("NO_GOOD")
                    result["ixml_false_start"] = _xtag("FALSE_START")
                    result["ixml_wild_track"]  = _xtag("WILD_TRACK")
                    result["ixml_note"]        = _xtag("NOTE")
                    result["ixml_user"]        = _xtag("USER")

                # ── aXML ─────────────────────────────────────────────────
                elif chunk_id == b"axml":
                    raw = f.read(chunk_size)
                    xml = raw.decode("utf-8", errors="replace")
                    import re
                    m = re.search(r"<tc:frameRate[^>]*>(\d+)</tc:frameRate>", xml)
                    if m and not result.get("ixml_project"):
                        result["ixml_user"] = xml[:512]

                # ── CART ─────────────────────────────────────────────────
                elif chunk_id == b"cart":
                    cart = f.read(chunk_size)
                    if len(cart) >= 2048:
                        result["cart_title"]        = _str(cart, 4, 64)
                        result["cart_artist"]       = _str(cart, 68, 64)
                        result["cart_cut_id"]       = _str(cart, 132, 64)
                        result["cart_client_id"]    = _str(cart, 196, 64)
                        result["cart_category"]     = _str(cart, 260, 64)
                        result["cart_classification"]= _str(cart, 324, 64)
                        result["cart_out_cue"]      = _str(cart, 388, 64)
                        result["cart_start_date"]   = _str(cart, 452, 10)
                        result["cart_start_time"]   = _str(cart, 462, 8)
                        result["cart_end_date"]     = _str(cart, 470, 10)
                        result["cart_end_time"]     = _str(cart, 480, 8)
                        result["cart_producer_app_id"] = _str(cart, 488, 64)
                        if len(cart) >= 2052:
                            result["cart_level_reference"] = struct.unpack_from("<i", cart, 1820)[0]
                        result["cart_user_def"]     = _str(cart, 2048, min(64, len(cart)-2048)) if len(cart) > 2048 else ""

                # seek to next chunk
                next_pos = chunk_start + chunk_size + (chunk_size % 2)
                f.seek(next_pos)

    except Exception as e:
        result["_wav_parse_error"] = str(e)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# ID3 / iTunes tag extraction from MP3
# ─────────────────────────────────────────────────────────────────────────────

def _extract_id3(mp3_path):
    """
    Extract ID3v2/iTunes tags from an MP3 file.
    Uses mutagen if available, otherwise falls back to ffprobe JSON.
    Returns a flat dict of remeta field names → values.
    """
    out = {}

    # ── mutagen path (richest data) ───────────────────────────────────────
    try:
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, ID3NoHeaderError

        audio = MP3(str(mp3_path))

        def _get(tag_id):
            v = audio.tags.get(tag_id)
            if v is None:
                return ""
            text = str(v)
            # strip ID3 frame wrapper like "TIT2(encoding=..., text=['My Song'])"
            if text.startswith(tag_id) and "[" in text:
                text = text.split("[")[-1].rstrip("]'\"")
            return text.strip()

        # Standard ID3v2 frame → remeta field
        ID3_MAP = {
            "TIT2": "title",          "TPE1": "artist",
            "TALB": "album",          "TPE2": "album_artist",
            "TRCK": "track_number",   "TPOS": "disc_number",
            "TDRC": "year",           "TCON": "genre",
            "TCOM": "composer",       "TEXT": "lyricist",
            "TPUB": "publisher",      "TSRC": "isrc",
            "TCOP": "copyright",      "COMM": "comment",
            "TBPM": "bpm",            "TKEY": "musical_key",
            "TLAN": "language",       "TMOO": "mood",
            "TMED": "riff_imed",      "TENC": "engineer",
            "TOWN": "label",          "TOPE": "composer",
        }
        for frame_id, field in ID3_MAP.items():
            val = _get(frame_id)
            if val:
                out[field] = val

        # iTunes-style TXXX frames
        for key in audio.tags.keys():
            if key.startswith("TXXX:"):
                label_part = key[5:].lower()
                val = str(audio.tags[key]).strip()
                if "bpm" in label_part:       out.setdefault("bpm", val)
                if "key" in label_part:       out.setdefault("musical_key", val)
                if "energy" in label_part:    out.setdefault("mood", val)
                if "catalog" in label_part:   out.setdefault("catalog_number", val)
                if "barcode" in label_part:   out.setdefault("barcode", val)
                if "isrc" in label_part:      out.setdefault("isrc", val)
                if "iswc" in label_part:      out.setdefault("iswc", val)
                if "label" in label_part:     out.setdefault("label", val)
                if "mood" in label_part:      out.setdefault("mood", val)
                if "occasion" in label_part:  out.setdefault("occasion", val)
                if "tags" in label_part:      out.setdefault("tags", val)

        # USLT (lyrics)
        for key in audio.tags.keys():
            if key.startswith("USLT"):
                uslt = audio.tags[key]
                out["lyrics"] = str(uslt.text)[:2000]
                break

        return out

    except ImportError:
        pass  # fall through to ffprobe
    except Exception:
        pass

    # ── ffprobe fallback ──────────────────────────────────────────────────
    try:
        probe = subprocess.run([
            "ffprobe", "-v", "error",
            "-print_format", "json",
            "-show_format",
            str(mp3_path)
        ], capture_output=True, text=True)
        info = json.loads(probe.stdout)
        tags = info.get("format", {}).get("tags", {})

        FFPROBE_MAP = {
            "title":        "title",       "artist":       "artist",
            "album":        "album",       "album_artist": "album_artist",
            "track":        "track_number","disc":         "disc_number",
            "date":         "year",        "genre":        "genre",
            "composer":     "composer",    "lyricist":     "lyricist",
            "publisher":    "publisher",   "isrc":         "isrc",
            "copyright":    "copyright",   "comment":      "comment",
            "bpm":          "bpm",         "tkey":         "musical_key",
            "language":     "language",    "mood":         "mood",
            "label":        "label",       "catalog":      "catalog_number",
            "lyrics":       "lyrics",      "engineer":     "engineer",
        }
        for raw_key, field in FFPROBE_MAP.items():
            val = tags.get(raw_key, tags.get(raw_key.upper(), ""))
            if val:
                out[field] = str(val)

    except Exception:
        pass

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Acoustic analysis (pure numpy + ffmpeg)
# ─────────────────────────────────────────────────────────────────────────────

def _read_mono_f32(wav_path, target_sr=22050, max_seconds=120):
    """Decode WAV to mono float32 numpy array via ffmpeg."""
    import numpy as np
    tmp = Path(tempfile.gettempdir()) / f"ana_{os.getpid()}_{id(wav_path)}.f32"
    try:
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(wav_path),
            "-t", str(max_seconds),
            "-ar", str(target_sr),
            "-ac", "1",
            "-f", "f32le",
            str(tmp)
        ], check=True)
        data = __import__("numpy").fromfile(str(tmp), dtype="float32")
        return data, target_sr
    finally:
        tmp.unlink(missing_ok=True)


def _analyze_loudness_ffmpeg(wav_path):
    """Use ffmpeg's ebur128 filter for integrated LUFS, LRA, true peak."""
    result = {"loudness_lufs": "", "loudness_range_lu": "", "true_peak_dbtp": ""}
    try:
        proc = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "info",
            "-i", str(wav_path),
            "-af", "ebur128=peak=true:framelog=quiet",
            "-f", "null", "-"
        ], capture_output=True, text=True, timeout=300)
        combined = proc.stderr + proc.stdout
        import re
        m = re.search(r"I:\s*([-\d.]+)\s*LUFS", combined)
        if m: result["loudness_lufs"] = float(m.group(1))
        m = re.search(r"LRA:\s*([\d.]+)\s*LU", combined)
        if m: result["loudness_range_lu"] = float(m.group(1))
        m = re.search(r"True peak:\s*Peak:\s*([-\d.]+)\s*dBFS", combined)
        if not m:
            m = re.search(r"Peak:\s*([-\d.]+)\s*dBFS", combined)
        if m: result["true_peak_dbtp"] = float(m.group(1))
    except Exception:
        pass
    return result


def _analyze_acoustic(wav_path):
    """
    Perform acoustic feature analysis returning a dict of remeta fields.
    All done in numpy — no librosa dependency.
    """
    out = {}
    try:
        import numpy as np

        data, sr = _read_mono_f32(wav_path, target_sr=22050, max_seconds=120)
        if len(data) == 0:
            return out

        # ── RMS & dynamic range ───────────────────────────────────────────
        rms = float(np.sqrt(np.mean(data ** 2)))
        peak = float(np.max(np.abs(data)))
        if rms > 0:
            rms_db = 20 * math.log10(rms)
            out["rms_db"] = round(rms_db, 2)
        if peak > 0:
            crest = 20 * math.log10(peak / rms) if rms > 0 else 0
            out["dynamic_range_db"] = round(crest, 2)

        # ── Zero-crossing rate ────────────────────────────────────────────
        zcr = float(np.mean(np.abs(np.diff(np.sign(data)))) / 2)
        out["zero_crossings_per_sec"] = round(zcr * sr, 1)

        # ── Spectral centroid ─────────────────────────────────────────────
        frame_size = 2048
        hop = 512
        frames = [data[i:i+frame_size] for i in range(0, len(data)-frame_size, hop)]
        if frames:
            window = np.hanning(frame_size)
            centroids = []
            for frame in frames[:500]:  # cap at 500 frames
                spec = np.abs(np.fft.rfft(frame * window))
                freqs = np.fft.rfftfreq(frame_size, 1/sr)
                if spec.sum() > 0:
                    centroids.append(float(np.sum(freqs * spec) / spec.sum()))
            if centroids:
                out["spectral_centroid_hz"] = round(float(np.mean(centroids)), 1)

        # ── Dominant pitch (autocorrelation on first 5s) ─────────────────
        pitch_data = data[:sr * 5]
        if len(pitch_data) > 2048:
            frame = pitch_data[:4096]
            ac = np.correlate(frame, frame, mode="full")
            ac = ac[len(ac)//2:]
            min_lag = int(sr / 1200)   # max 1200 Hz
            max_lag = int(sr / 60)     # min 60 Hz
            if max_lag < len(ac):
                ac_slice = ac[min_lag:max_lag]
                lag = int(np.argmax(ac_slice)) + min_lag
                if lag > 0:
                    f0 = sr / lag
                    out["pitch_hz"] = round(f0, 2)
                    note, octave = _hz_to_note(f0)
                    out["pitch_note"] = f"{note}{octave}"

        # ── BPM via onset strength + autocorrelation ──────────────────────
        # Compute onset envelope from spectral flux
        hop = 512
        frames2 = []
        prev_spec = None
        for i in range(0, min(len(data), sr * 60) - frame_size, hop):
            frame = data[i:i+frame_size] * np.hanning(frame_size)
            spec = np.abs(np.fft.rfft(frame))
            if prev_spec is not None:
                flux = float(np.sum(np.maximum(spec - prev_spec, 0)))
                frames2.append(flux)
            prev_spec = spec

        if len(frames2) > 64:
            onset_env = np.array(frames2, dtype=np.float32)
            # Normalize
            onset_env -= onset_env.mean()
            if onset_env.std() > 0:
                onset_env /= onset_env.std()

            # Autocorrelation of onset envelope → tempo
            onset_sr = sr / hop  # frames per second
            min_period = int(onset_sr * 60 / 200)  # 200 BPM
            max_period = int(onset_sr * 60 / 40)   # 40 BPM
            ac_onset = np.correlate(onset_env, onset_env, mode="full")
            ac_onset = ac_onset[len(ac_onset)//2:]
            if max_period < len(ac_onset):
                ac_slice = ac_onset[min_period:max_period]
                period = int(np.argmax(ac_slice)) + min_period
                bpm_val = onset_sr * 60.0 / period
                # Snap to musically sensible range via doubling/halving
                while bpm_val < 60:   bpm_val *= 2
                while bpm_val > 200:  bpm_val /= 2
                out["bpm"] = round(bpm_val, 1)
                # Confidence: normalised peak value
                conf = float(ac_onset[period]) / (float(ac_onset[0]) + 1e-9)
                out["bpm_confidence"] = round(min(max(conf, 0.0), 1.0), 3)

        # ── Musical key via Krumhansl-Schmuckler ──────────────────────────
        chroma = _compute_chroma(data, sr)
        if chroma is not None:
            key_idx, scale, confidence = _key_from_chroma(chroma)
            note_names = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
            root = note_names[key_idx]
            out["root_note"]      = root
            out["scale"]          = scale
            out["musical_key"]    = f"{root} {scale}"
            out["key_confidence"] = round(confidence, 3)

    except ImportError:
        out["_analysis_note"] = "numpy not available — install with: pip install numpy"
    except Exception as e:
        out["_analysis_error"] = str(e)

    return out


def _hz_to_note(hz):
    """Convert frequency in Hz to note name and octave."""
    if hz <= 0:
        return "?", 0
    A4 = 440.0
    semitones = 12 * math.log2(hz / A4)
    midi = round(semitones) + 69
    names = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
    octave = (midi // 12) - 1
    note = names[midi % 12]
    return note, octave


def _compute_chroma(data, sr, frame_size=8192, hop=2048):
    """Compute 12-bin chroma vector from audio data."""
    try:
        import numpy as np
        chroma = np.zeros(12)
        note_names = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
        count = 0
        for i in range(0, min(len(data), sr * 60) - frame_size, hop):
            frame = data[i:i+frame_size] * np.hanning(frame_size)
            spec = np.abs(np.fft.rfft(frame)) ** 2
            freqs = np.fft.rfftfreq(frame_size, 1/sr)
            for bin_i, freq in enumerate(freqs):
                if freq < 60 or freq > 4200:
                    continue
                semitone = 12 * math.log2(freq / 16.35) if freq > 0 else 0
                chroma_bin = int(semitone) % 12
                chroma[chroma_bin] += spec[bin_i]
            count += 1
        if count > 0:
            chroma /= count
            chroma /= (chroma.max() + 1e-9)
        return chroma
    except Exception:
        return None


def _key_from_chroma(chroma):
    """
    Krumhansl-Schmuckler key profiles.
    Returns (key_index 0-11, 'major'|'minor', confidence).
    """
    import numpy as np
    # Major and minor profiles (Krumhansl 1990)
    major = np.array([6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88])
    minor = np.array([6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])

    def correlate_key(profile):
        scores = []
        for shift in range(12):
            rolled = np.roll(chroma, -shift)
            scores.append(float(np.corrcoef(rolled, profile)[0, 1]))
        return scores

    maj_scores = correlate_key(major)
    min_scores = correlate_key(minor)
    all_scores = maj_scores + min_scores
    best_idx = int(np.argmax(all_scores))
    confidence = float(np.max(all_scores))
    if best_idx < 12:
        return best_idx, "major", confidence
    else:
        return best_idx - 12, "minor", confidence


# ─────────────────────────────────────────────────────────────────────────────
# Encoder delay detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_encoder_delay(wav_path, mp3_path):
    """Cross-correlate WAV vs decoded MP3 to find encoder delay in samples."""
    try:
        import numpy as np
        tmp = Path(tempfile.gettempdir())
        pid = os.getpid()

        def read_raw(path, sr, ch):
            out = tmp / f"dly_{pid}_{id(path)}.f32"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(path), "-f", "f32le", "-acodec", "pcm_f32le",
                "-ar", str(sr), "-ac", str(ch), "-t", "5", str(out)
            ], check=True)
            d = np.fromfile(str(out), dtype="float32")
            out.unlink(missing_ok=True)
            return d.reshape(-1, ch)

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
        window = min(sr, len(orig), len(dec))
        corr = np.correlate(dec[:window, 0], orig[:window, 0], mode="full")
        delay = max(0, int(np.argmax(corr)) - (window - 1))
        padding = max(0, len(dec) - delay - len(orig))
        return delay, padding
    except Exception:
        return 1105, 576


# ─────────────────────────────────────────────────────────────────────────────
# Cross-schema consolidation
# ─────────────────────────────────────────────────────────────────────────────

def _consolidate(meta):
    """
    After all extraction passes, fill common fields from their schema equivalents.
    e.g. RIFF INAM → title, CART title → title, ID3 TIT2 → title, etc.
    Priority: user/ID3 > RIFF INFO > CART > BWF description
    """
    def _fill(target, *sources):
        if not meta.get(target):
            for src in sources:
                if meta.get(src):
                    meta[target] = meta[src]
                    break

    _fill("title",    "riff_inam", "cart_title")
    _fill("artist",   "riff_iart", "cart_artist")
    _fill("album",    "riff_iprd")
    _fill("comment",  "riff_icmt")
    _fill("genre",    "riff_ignr")
    _fill("engineer", "riff_ieng")
    _fill("copyright","riff_icop")
    _fill("tags",     "riff_ikey")

    # BWF → track title fallback
    if not meta.get("title") and meta.get("bwf_description"):
        meta["title"] = meta["bwf_description"][:128]

    # FL Studio hints
    if meta.get("bpm") and not meta.get("fl_tempo"):
        meta["fl_tempo"] = meta["bpm"]
    if meta.get("musical_key") and not meta.get("fl_pitch"):
        meta["fl_pitch"] = "0"  # default no pitch shift

    # BWF v2 loudness → human-readable
    if meta.get("bwf_loudness_value") and not meta.get("loudness_lufs"):
        try:
            meta["loudness_lufs"] = round(int(meta["bwf_loudness_value"]) / 100.0, 2)
        except Exception:
            pass

    return meta


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def create_remeta(wav_path, mp3_path=None, bitrate=None, user_fields=None,
                  do_acoustic=True):
    """
    Build a complete remeta dict from a WAV file.
    Runs: WAV chunk parsing, ID3 extraction (if mp3_path given),
          acoustic analysis, encoder delay detection, cross-schema fill.
    """
    wav_path = Path(wav_path)
    now = datetime.datetime.utcnow()

    # Base payload
    meta = {k: "" for k in FIELD_DESCRIPTIONS}
    meta["remeta_version"]   = REMETA_VERSION
    meta["source_filename"]  = wav_path.name
    meta["encode_timestamp"] = now.isoformat() + "Z"
    meta["unmp3_bitrate"]    = bitrate or ""

    # 1 — WAV header / RIFF chunks
    print("    [remeta] Parsing WAV chunks…")
    header = _parse_wav_header(wav_path)
    meta.update({k: v for k, v in header.items() if v != ""})

    # Fill default BWF date/time if not in file
    if not meta.get("bwf_origination_date"):
        meta["bwf_origination_date"] = now.strftime("%Y-%m-%d")
    if not meta.get("bwf_origination_time"):
        meta["bwf_origination_time"] = now.strftime("%H:%M:%S")

    # 2 — ID3 tags from MP3 companion
    if mp3_path and Path(mp3_path).exists():
        print("    [remeta] Extracting ID3/iTunes tags from MP3…")
        id3_data = _extract_id3(mp3_path)
        for k, v in id3_data.items():
            if v and not meta.get(k):
                meta[k] = v

    # 3 — Acoustic analysis
    if do_acoustic:
        print("    [remeta] Running acoustic analysis (BPM / key / loudness)…")
        loudness = _analyze_loudness_ffmpeg(wav_path)
        meta.update({k: v for k, v in loudness.items() if v != ""})
        acoustic = _analyze_acoustic(wav_path)
        for k, v in acoustic.items():
            if v != "" and v is not None:
                if not meta.get(k):
                    meta[k] = v

    # 4 — Encoder delay
    if mp3_path and Path(mp3_path).exists():
        print("    [remeta] Detecting encoder delay…")
        delay, padding = _detect_encoder_delay(wav_path, mp3_path)
    else:
        delay, padding = 1105, 576
    meta["encoder_delay_samples"]   = delay
    meta["encoder_padding_samples"] = padding

    # 5 — Cross-schema consolidation
    meta = _consolidate(meta)

    # 6 — User overrides (always win)
    if user_fields:
        for k, v in user_fields.items():
            if k in FIELD_DESCRIPTIONS:
                meta[k] = v

    return meta


def save_remeta(payload, remeta_path):
    """Write remeta dict to a .remeta JSON file."""
    with open(remeta_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def load_remeta(remeta_path):
    """Load a .remeta JSON file and return the dict."""
    with open(remeta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_remeta_to_wav(remeta_path, wav_path, output_wav_path=None):
    """
    Write metadata from a .remeta file into a WAV via ffmpeg.
    Maps all populated fields to their correct ffmpeg metadata keys.
    If output_wav_path is None, overwrites wav_path in-place.
    """
    import shutil
    meta = load_remeta(remeta_path) if isinstance(remeta_path, (str, Path)) else remeta_path

    output_wav_path = output_wav_path or wav_path
    tmp = Path(tempfile.gettempdir()) / f"remeta_apply_{os.getpid()}.wav"

    # ffmpeg metadata key → remeta field (write highest-priority value)
    FF_MAP = {
        "title":                "title",
        "artist":               "artist",
        "album":                "album",
        "album_artist":         "album_artist",
        "track":                "track_number",
        "disc":                 "disc_number",
        "date":                 "year",
        "genre":                "genre",
        "composer":             "composer",
        "lyricist":             "lyricist",
        "publisher":            "publisher",
        "copyright":            "copyright",
        "comment":              "comment",
        "isrc":                 "isrc",
        "language":             "language",
        "engineer":             "engineer",
        "producer":             "producer",
        "description":          "bwf_description",
        "originator":           "bwf_originator",
        "originator_reference": "bwf_originator_reference",
        "origination_date":     "bwf_origination_date",
        "origination_time":     "bwf_origination_time",
        "coding_history":       "bwf_coding_history",
        "bpm":                  "bpm",
        "tbpm":                 "bpm",
        "tkey":                 "musical_key",
        "mood":                 "mood",
        "label":                "label",
        "catalog_number":       "catalog_number",
        "barcode":              "barcode",
        "lyrics":               "lyrics",
        "project":              "project",
        "studio":               "studio",
        "mixer":                "mixer",
        "INAM":                 "title",
        "IART":                 "artist",
        "IPRD":                 "album",
        "ICMT":                 "comment",
        "IGNR":                 "genre",
        "IENG":                 "engineer",
        "ISFT":                 "daw",
        "ICOP":                 "copyright",
    }

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(wav_path)]
    for ff_key, remeta_key in FF_MAP.items():
        val = meta.get(remeta_key, "")
        if val:
            cmd += ["-metadata", f"{ff_key}={val}"]

    cmd += ["-c:a", "copy", str(tmp)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg metadata write failed: {result.stderr}")

    import shutil as _sh
    _sh.move(str(tmp), str(output_wav_path))
    return output_wav_path


def print_remeta(meta):
    """Pretty-print a remeta dict grouped by schema section."""
    print(f"\n{'═'*64}")
    print(f"  REMETA v{meta.get('remeta_version','?')}  ·  {meta.get('source_filename','')}")
    print(f"{'═'*64}")
    for group, fields in FIELD_GROUPS.items():
        rows = [(f, meta.get(f, "")) for f in fields if meta.get(f, "") not in ("", None)]
        if not rows:
            continue
        print(f"\n  ┌─ {group} {'─'*(54-len(group))}┐")
        for field, val in rows:
            val_str = str(val)[:50]
            print(f"  │  {field:<32} {val_str}")
        print(f"  └{'─'*60}┘")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _cli():
    import argparse
    parser = argparse.ArgumentParser(
        description="UNMP3 .remeta sidecar — metadata extraction & writing")
    sub = parser.add_subparsers(dest="cmd")

    p_ex = sub.add_parser("extract",
        help="Extract full metadata + acoustic analysis from WAV")
    p_ex.add_argument("wav",    help="Input WAV file")
    p_ex.add_argument("output", help="Output .remeta file")
    p_ex.add_argument("--mp3",     help="Companion MP3 for ID3 tags + encoder delay")
    p_ex.add_argument("--bitrate", default="", help="MP3 bitrate label")
    p_ex.add_argument("--no-acoustic", action="store_true",
                      help="Skip acoustic analysis (faster)")

    p_sh = sub.add_parser("show", help="Display .remeta contents")
    p_sh.add_argument("remeta", help=".remeta file to display")

    p_ap = sub.add_parser("apply", help="Apply .remeta metadata to a WAV file")
    p_ap.add_argument("remeta", help="Source .remeta file")
    p_ap.add_argument("wav",    help="Target WAV file")
    p_ap.add_argument("--output", help="Output WAV path (default: overwrite)")

    p_an = sub.add_parser("analyze", help="Acoustic analysis only → .remeta")
    p_an.add_argument("wav",    help="Input WAV")
    p_an.add_argument("output", help="Output .remeta")

    args = parser.parse_args()

    if args.cmd == "extract":
        meta = create_remeta(args.wav, mp3_path=args.mp3, bitrate=args.bitrate,
                             do_acoustic=not args.no_acoustic)
        save_remeta(meta, args.output)
        print(f"\n✅ Saved {args.output}")
        print_remeta(meta)

    elif args.cmd == "show":
        print_remeta(load_remeta(args.remeta))

    elif args.cmd == "apply":
        out = apply_remeta_to_wav(args.remeta, args.wav, args.output)
        print(f"✅ Metadata applied → {out}")

    elif args.cmd == "analyze":
        meta = {"remeta_version": REMETA_VERSION, "source_filename": Path(args.wav).name}
        meta.update(_analyze_loudness_ffmpeg(args.wav))
        meta.update(_analyze_acoustic(args.wav))
        save_remeta(meta, args.output)
        print(f"✅ Saved {args.output}")
        print_remeta(meta)

    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
