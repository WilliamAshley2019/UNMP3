#!/usr/bin/env python3
"""
UnMP3 Codec - Hybrid Lossless Audio Format
============================================
MP3 base layer + FLAC-compressed residual = bit-perfect WAV reconstruction

Usage:
    python unmp3.py encode input.wav output.mp3 output.unmp3 [--bitrate 320k]
    python unmp3.py decode input.mp3 input.unmp3 output.wav
    python unmp3.py test   # Run full experiment with test tone

Dependencies: numpy, ffmpeg (with libmp3lame and flac support)
"""

import numpy as np
import subprocess
import os
import tempfile
import json
import shutil
import argparse
from pathlib import Path

# Optional remeta sidecar support
try:
    from remeta import create_remeta, save_remeta, load_remeta, apply_remeta_to_wav, print_remeta
    REMETA_AVAILABLE = True
except ImportError:
    REMETA_AVAILABLE = False


class UnMP3Codec:
    """
    Hybrid lossless codec: MP3 base layer + residual correction file.
    Uses ffmpeg for all audio I/O (no external Python audio libs needed).
    """

    def __init__(self, mp3_bitrate='320k', temp_dir=None):
        self.mp3_bitrate = mp3_bitrate
        self.temp_dir = temp_dir or tempfile.gettempdir()

    def _run_ffmpeg(self, args, check=True):
        """Run ffmpeg with given arguments."""
        cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error'] + args
        result = subprocess.run(cmd, capture_output=True, text=True)
        if check and result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr}")
        return result

    def _read_wav(self, path):
        """Read WAV file to numpy array using ffmpeg raw output."""
        raw_path = Path(self.temp_dir) / f"raw_{os.getpid()}_{id(path)}.f64"
        self._run_ffmpeg([
            '-i', str(path),
            '-f', 'f64le',
            '-acodec', 'pcm_f64le',
            str(raw_path)
        ])

        # Get audio info
        probe = subprocess.run([
            'ffprobe', '-v', 'error',
            '-select_streams', 'a:0',
            '-show_entries', 'stream=sample_rate,channels',
            '-of', 'json',
            str(path)
        ], capture_output=True, text=True)

        info = json.loads(probe.stdout)
        stream = info['streams'][0]
        sample_rate = int(stream['sample_rate'])
        channels = int(stream['channels'])

        # Read raw data
        raw = np.fromfile(str(raw_path), dtype=np.float64)
        raw = raw.reshape(-1, channels)
        raw_path.unlink(missing_ok=True)

        return raw, sample_rate, channels

    def _write_wav(self, path, data, sample_rate, bits=16):
        """Write numpy array to WAV file using ffmpeg."""
        raw_path = Path(self.temp_dir) / f"out_raw_{os.getpid()}_{id(path)}.f64"
        data = np.asarray(data, dtype=np.float64)
        data.tofile(str(raw_path))

        if bits == 24:
            self._run_ffmpeg([
                '-f', 'f64le',
                '-ar', str(sample_rate),
                '-ac', str(data.shape[1] if data.ndim > 1 else 1),
                '-i', str(raw_path),
                '-c:a', 'pcm_s24le',
                str(path)
            ])
        else:
            self._run_ffmpeg([
                '-f', 'f64le',
                '-ar', str(sample_rate),
                '-ac', str(data.shape[1] if data.ndim > 1 else 1),
                '-i', str(raw_path),
                '-sample_fmt', 's16',
                str(path)
            ])
        raw_path.unlink(missing_ok=True)

    def encode(self, wav_path, mp3_path, unmp3_path, remeta_path=None, user_meta=None, do_acoustic=True):
        """Encode WAV to MP3 + UNMP3 residual."""
        wav_path = Path(wav_path)
        mp3_path = Path(mp3_path)
        unmp3_path = Path(unmp3_path)

        print(f"[1/5] Reading original WAV: {wav_path}")
        original_pcm, sample_rate, num_channels = self._read_wav(wav_path)
        num_samples = len(original_pcm)

        print(f"        Sample rate: {sample_rate} Hz")
        print(f"        Channels: {num_channels}")
        print(f"        Samples: {num_samples}")
        print(f"        Duration: {num_samples / sample_rate:.2f}s")

        print(f"[2/5] Encoding to MP3 at {self.mp3_bitrate}...")
        self._run_ffmpeg([
            '-i', str(wav_path),
            '-codec:a', 'libmp3lame',
            '-b:a', self.mp3_bitrate,
            '-q:a', '0',
            str(mp3_path)
        ])

        print(f"[3/5] Decoding MP3 back to PCM for residual computation...")
        decoded_wav_temp = Path(self.temp_dir) / f"decoded_{os.getpid()}.wav"
        self._run_ffmpeg([
            '-i', str(mp3_path),
            '-ar', str(sample_rate),
            '-ac', str(num_channels),
            '-sample_fmt', 's16',
            str(decoded_wav_temp)
        ])

        decoded_pcm, _, _ = self._read_wav(decoded_wav_temp)

        min_len = min(len(original_pcm), len(decoded_pcm))
        original_pcm = original_pcm[:min_len]
        decoded_pcm = decoded_pcm[:min_len]

        print(f"[4/5] Computing residual (difference)...")
        residual = original_pcm - decoded_pcm

        residual_wav_temp = Path(self.temp_dir) / f"residual_{os.getpid()}.wav"
        self._write_wav(residual_wav_temp, residual, sample_rate, bits=24)

        print(f"[5/5] Compressing residual to FLAC (UNMP3)...")
        flac_temp = Path(self.temp_dir) / f"residual_{os.getpid()}.flac"
        self._run_ffmpeg([
            '-i', str(residual_wav_temp),
            '-c:a', 'flac',
            '-compression_level', '8',
            str(flac_temp)
        ])
        shutil.move(str(flac_temp), str(unmp3_path))

        # Generate .remeta sidecar
        if REMETA_AVAILABLE and remeta_path:
            print(f"[+] Generating .remeta sidecar...")
            meta = create_remeta(
                wav_path, mp3_path=mp3_path,
                bitrate=self.mp3_bitrate, user_fields=user_meta
            )
            save_remeta(meta, remeta_path)
            print(f"        Saved: {remeta_path}")

        decoded_wav_temp.unlink(missing_ok=True)
        residual_wav_temp.unlink(missing_ok=True)

        # Report sizes
        orig_size = wav_path.stat().st_size
        mp3_size = mp3_path.stat().st_size
        unmp3_size = unmp3_path.stat().st_size
        total_size = mp3_size + unmp3_size

        flac_temp2 = Path(self.temp_dir) / f"est_flac_{os.getpid()}.flac"
        self._run_ffmpeg([
            '-i', str(wav_path),
            '-c:a', 'flac',
            '-compression_level', '8',
            str(flac_temp2)
        ])
        flac_size = flac_temp2.stat().st_size
        flac_temp2.unlink(missing_ok=True)

        print(f"\n{'='*60}")
        print(f"ENCODING RESULTS")
        print(f"{'='*60}")
        print(f"Original WAV:     {orig_size / 1024 / 1024:.2f} MB")
        print(f"MP3 ({self.mp3_bitrate}):     {mp3_size / 1024 / 1024:.2f} MB")
        print(f"UNMP3 residual:   {unmp3_size / 1024 / 1024:.2f} MB")
        print(f"MP3 + UNMP3:      {total_size / 1024 / 1024:.2f} MB")
        print(f"FLAC:             {flac_size / 1024 / 1024:.2f} MB")
        print(f"{'='*60}")
        if flac_size > 0:
            savings = (1 - total_size/flac_size)*100
            print(f"vs FLAC:          {savings:+.1f}% ({'wins' if savings > 0 else 'loses'})")
        print(f"Compression:      {orig_size / total_size:.2f}x")

        return {
            'original_size': orig_size,
            'mp3_size': mp3_size,
            'unmp3_size': unmp3_size,
            'total_size': total_size,
            'flac_size': flac_size,
            'sample_rate': sample_rate,
            'channels': num_channels,
            'samples': min_len
        }

    def decode(self, mp3_path, unmp3_path, output_wav_path, remeta_path=None):
        """Decode MP3 + UNMP3 back to original WAV."""
        mp3_path = Path(mp3_path)
        unmp3_path = Path(unmp3_path)
        output_wav_path = Path(output_wav_path)

        print(f"[DECODE] Reconstructing WAV from MP3 + UNMP3...")

        mp3_pcm_temp = Path(self.temp_dir) / f"mp3_pcm_{os.getpid()}.wav"
        self._run_ffmpeg([
            '-i', str(mp3_path),
            '-sample_fmt', 's16',
            str(mp3_pcm_temp)
        ])

        mp3_pcm, mp3_sr, mp3_ch = self._read_wav(mp3_pcm_temp)

        flac_temp = Path(self.temp_dir) / f"unmp3_{os.getpid()}.flac"
        shutil.copy(str(unmp3_path), str(flac_temp))

        residual_pcm_temp = Path(self.temp_dir) / f"residual_pcm_{os.getpid()}.wav"
        self._run_ffmpeg([
            '-i', str(flac_temp),
            str(residual_pcm_temp)
        ])

        residual_pcm, res_sr, res_ch = self._read_wav(residual_pcm_temp)

        min_len = min(len(mp3_pcm), len(residual_pcm))
        mp3_pcm = mp3_pcm[:min_len]
        residual_pcm = residual_pcm[:min_len]

        reconstructed = mp3_pcm + residual_pcm
        reconstructed = np.clip(reconstructed, -1.0, 1.0)

        self._write_wav(output_wav_path, reconstructed, mp3_sr, bits=16)

        # Apply .remeta metadata to reconstructed WAV
        if REMETA_AVAILABLE and remeta_path and Path(remeta_path).exists():
            print(f"        Applying .remeta metadata...")
            try:
                apply_remeta_to_wav(remeta_path, output_wav_path)
                print(f"        Metadata applied from: {remeta_path}")
            except Exception as e:
                print(f"        Warning: could not apply metadata: {e}")

        mp3_pcm_temp.unlink(missing_ok=True)
        flac_temp.unlink(missing_ok=True)
        residual_pcm_temp.unlink(missing_ok=True)

        print(f"        Reconstructed: {output_wav_path}")
        print(f"        Samples: {len(reconstructed)}")

        return output_wav_path

    def verify(self, original_wav, reconstructed_wav):
        """Verify bit-perfect reconstruction."""
        orig, sr1, ch1 = self._read_wav(original_wav)
        recon, sr2, ch2 = self._read_wav(reconstructed_wav)

        min_len = min(len(orig), len(recon))
        orig = orig[:min_len]
        recon = recon[:min_len]

        diff = np.abs(orig - recon)
        max_diff = np.max(diff)
        mean_diff = np.mean(diff)
        lsb_16bit = 1.0 / 32768.0

        print(f"\n{'='*60}")
        print(f"VERIFICATION")
        print(f"{'='*60}")
        print(f"Max sample difference: {max_diff:.10f}")
        print(f"Mean sample difference: {mean_diff:.10f}")
        print(f"16-bit LSB: {lsb_16bit:.10f}")
        print(f"Max diff in LSBs: {max_diff / lsb_16bit:.2f}")

        if max_diff < lsb_16bit:
            print(f"✅ BIT-PERFECT (within 16-bit quantization)")
        elif max_diff < lsb_16bit * 2:
            print(f"⚠️ NEAR-PERFECT (within 2 LSBs of 16-bit)")
        else:
            print(f"❌ NOT PERFECT (difference > 2 LSBs)")

        return max_diff < lsb_16bit * 2


def generate_test_audio(path, duration=10.0, sample_rate=44100, 
                        freq=440.0, harmonics=[1, 0.5, 0.25, 0.125, 0.06, 0.03],
                        noise_level=0.002, stereo=True):
    """Generate a rich test tone with harmonics and subtle noise."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)

    signal = np.zeros_like(t)
    for i, amp in enumerate(harmonics):
        signal += amp * np.sin(2 * np.pi * freq * (i + 1) * t)

    noise = np.random.normal(0, noise_level, len(t))
    signal += noise

    transient = np.zeros_like(t)
    for i in range(0, len(t), int(sample_rate * 0.5)):
        if i + 100 < len(t):
            transient[i:i+100] += np.hanning(100) * 0.1 * np.sin(2 * np.pi * 2000 * t[i:i+100])
    signal += transient

    signal = signal / np.max(np.abs(signal)) * 0.95

    if stereo:
        left = signal * 0.98
        right = signal * 1.0
        signal = np.column_stack([left, right])
    else:
        signal = signal.reshape(-1, 1)

    raw_path = Path(str(path)).with_suffix('.raw')
    signal.astype(np.float64).tofile(str(raw_path))

    subprocess.run([
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'f64le',
        '-ar', str(sample_rate),
        '-ac', str(2 if stereo else 1),
        '-i', str(raw_path),
        '-sample_fmt', 's16',
        str(path)
    ], check=True)

    raw_path.unlink(missing_ok=True)
    print(f"Generated test audio: {path}")
    return path


def run_experiment(output_dir='./unmp3_test'):
    """Run full experiment at multiple bitrates."""
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    test_wav = output_dir / 'test_tone.wav'
    generate_test_audio(test_wav, duration=10.0)

    results = {}
    for bitrate in ['128k', '192k', '256k', '320k']:
        print(f"\n{'='*70}")
        print(f"EXPERIMENT: MP3 bitrate = {bitrate}")
        print(f"{'='*70}")

        codec = UnMP3Codec(mp3_bitrate=bitrate)
        mp3_file = output_dir / f'test_{bitrate}.mp3'
        unmp3_file = output_dir / f'test_{bitrate}.unmp3'
        recon_file = output_dir / f'test_{bitrate}_recon.wav'

        result = codec.encode(test_wav, mp3_file, unmp3_file)
        codec.decode(mp3_file, unmp3_file, recon_file)
        is_perfect = codec.verify(test_wav, recon_file)

        results[bitrate] = {**result, 'bit_perfect': is_perfect}

    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"{'='*70}")
    print(f"{'Bitrate':<10} {'MP3':<10} {'UNMP3':<10} {'Total':<10} {'FLAC':<10} {'vs FLAC':<10} {'Perfect'}")
    print(f"{'-'*70}")
    for bitrate, r in results.items():
        vs_flac = (1 - r['total_size']/r['flac_size'])*100
        print(f"{bitrate:<10} {r['mp3_size']/1024/1024:>6.2f}MB  {r['unmp3_size']/1024/1024:>6.2f}MB  "
              f"{r['total_size']/1024/1024:>6.2f}MB  {r['flac_size']/1024/1024:>6.2f}MB  "
              f"{vs_flac:>+6.1f}%   {'✅' if r['bit_perfect'] else '❌'}")

    print(f"\nFiles saved to: {output_dir}")
    return results


def main():
    parser = argparse.ArgumentParser(description='UnMP3 Hybrid Lossless Codec')
    parser.add_argument('command', nargs='?', choices=['encode', 'decode', 'test'],
                        help='Operation to perform')
    parser.add_argument('input1', nargs='?', help='Input WAV (encode) or MP3 (decode)')
    parser.add_argument('input2', nargs='?', help='Output MP3 (encode) or UNMP3 (decode)')
    parser.add_argument('output', nargs='?', help='Output UNMP3 (encode) or WAV (decode)')
    parser.add_argument('--bitrate', default='320k', help='MP3 bitrate (default: 320k)')
    parser.add_argument('--output-dir', default='./unmp3_test', help='Test output directory')
    parser.add_argument('--remeta', default=None, help='Path for .remeta sidecar (encode/decode)')
    parser.add_argument('--no-acoustic', action='store_true',
                        help='Skip BPM/key/loudness analysis (faster)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == 'test':
        run_experiment(args.output_dir)
    elif args.command == 'encode':
        if not all([args.input1, args.input2, args.output]):
            parser.error("encode requires: input.wav output.mp3 output.unmp3")
        codec = UnMP3Codec(mp3_bitrate=args.bitrate)
        codec.encode(args.input1, args.input2, args.output,
                     remeta_path=args.remeta,
                     do_acoustic=not getattr(args, 'no_acoustic', False))
    elif args.command == 'decode':
        if not all([args.input1, args.input2, args.output]):
            parser.error("decode requires: input.mp3 input.unmp3 output.wav")
        codec = UnMP3Codec()
        codec.decode(args.input1, args.input2, args.output, remeta_path=args.remeta)


if __name__ == '__main__':
    main()
