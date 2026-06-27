EDIT: To do I also want to add in a new feature called ".remeta" that will add in .wav metadata as  third file so the .mp3 + .unmp3 + .remeta will provide a fully profiled .wav file with its metadata distributed through 3 files in I am thinking the specific cases may be where accessing the individual files may be more efficient but you may still want to work with .wav files for specific purposes it offers a slightly improved format over .wav. A little weird but I think there may be uses.

I've added version 3 of UNmp3 this aims to add basic "song analysis" tools for tempo / pitch, and it expands the type of metadata that can be input.  there are dependencies to install such as  pip install mutagen
I am still testing both version 2 and version 3 so there may be bugs.

Version 4 - will include a few more analysis tools - as I think the .remeta data will be useful for automating audio processes.
analysis and .remeta info additions Noise floor shape , Dynamic envelope, Transient character, Harmonic distortion signature, delta compute

# UNMP3

I had a thought to make a "partner file" for mp3 files that can undo the destructive loss processes that strip data from the mp3 file.
By storing the stripped data in a paired file called the "UNMP3" this allows the two files to be recombined back to their original raw wav format. The idea here was to create a way of supplying not only data for returning mp3 to a lossless form without distributing the actual .wav file but that these unmp3 files might have use in stem seperation or other analytic understanding of mp3 data they are working with. The first application of taking a .wav raw format and splitting rendering to not only the Mp3 version BUT also presering the loss in the unmp3 and creating a split stream way of transfering data in an easily restored format for instance in application where you want a version for streaming but you may want to supply the unmp3 version to people who have a need for higher quality such as broadcast. The thought into loss and its potential dirtying of AI audio usages raised the thought that perhaps the loss even though mostly noiseto humans might actually serve useful additional functions in an age of AI generative audio and stem splitting. What is interesting about this and I still need to test, that the .unmp3 file is still an audio file and you can play it, even by changing it from .unmp3 to .mp3  however I'm not entirely sure if my methods make sense just yet more testing is needed.  Again I feel like this will be useful for audio processing purposes as mp3 files are fine for audio broadcast or listening at reduced datasizes to .wav however a file that will allow files to be reverted to wav or "worked with" analytically via AI tooks like generative AI or stem seperation to have a data sense of what information needs to be accounted for in a source file that isn't present, seems to actually have a usuage. I think there may be more to it. I havn't actually encountered this before, it was just an idea that popped into my head today so I thought I would run with the idea.  
**Experimental MP3 Residual Preservation Format** - the concept works, however I havn't tested it much yet.  I've started assembling this readme to prevent a misunderstanding of what the script aims to do or how it does it. 

UNMP3 is an experimental concept that attempts to preserve the information discarded during MP3 encoding by storing it in a companion file called an **`.unmp3`** file.

The goal is to allow a lossy MP3 and a corresponding UNMP3 file to be recombined to reconstruct the original WAV file, while still retaining the storage and streaming advantages of MP3.

In practice, reconstruction should be bit-perfect (or within 1–2 LSB of 16-bit) for the tested workflows, as shown in the verify function and experiment summary.

Total size (MP3 + UNMP3) is usually larger than a good FLAC but smaller than raw WAV, with streaming benefits from the MP3 part. Recall the idea is to provide a way of distributing content via MP3 but upgrading it back to a .wav file with improved audio quality. 

Reconstruction quality depends on matching sample rates, channel counts, and avoiding clipping (the code handles clipping).
---

## Concept

Traditional MP3 encoding removes audio information that is considered less audible to human listeners. Once discarded, that information is normally lost forever.

UNMP3 explores the idea of preserving that discarded information separately:

```text
Original WAV
     │
     ├──► MP3 Encoder ──► song.mp3
     │
     └──► Residual Data ──► song.unmp3
```

Later:

```text
song.mp3 + song.unmp3
          │
          ▼
Reconstructed WAV
```

The `.unmp3` file stores the **residual**, which represents the difference between the original WAV and the MP3 decoded back into WAV form.

---

## Why?

Potential applications include:

* Bit-perfect reconstruction of original WAV files
* Reduced storage requirements compared to distributing WAV files directly
* Broadcast and archival workflows
* Audio restoration research
* AI audio analysis
* Stem separation research
* Machine learning datasets
* Psychoacoustic analysis of MP3 encoding losses

The original idea arose from considering whether information discarded by lossy codecs might still be valuable in an era of AI-assisted audio processing.

While that discarded data may be largely inaudible to human listeners, it may still contain information useful for:

* Source separation
* Audio reconstruction
* Generative audio systems
* Training datasets
* Codec analysis

---

## What Is the Residual?

The residual is calculated as:

```text
Residual = Original WAV − Decoded MP3 WAV
```

Anything remaining in the residual represents information removed by the MP3 encoder.

### What the Residual Contains

| Component                 | Description                                                    |
| ------------------------- | -------------------------------------------------------------- |
| High-Frequency Loss       | Audio removed above the MP3 codec's effective frequency cutoff |
| Pre-Echo & Smearing       | Transient detail softened during compression                   |
| Quantization Noise        | Noise introduced by lossy quantization                         |
| Stereo Phase Differences  | Changes caused by joint stereo encoding                        |
| Psychoacoustic Discarding | Content deemed inaudible by masking models                     |

Listening to the residual often reveals exactly what the MP3 encoder removed.

---

## Current Status

⚠️ **Experimental**

This project is currently a proof-of-concept and requires further testing.

One interesting observation is that the generated `.unmp3` file is still fundamentally audio data. In some cases it can even be listened to directly after renaming the extension, though its usefulness and behavior are still being evaluated.

Further testing is needed to determine:

* Reconstruction accuracy
* Compression efficiency
* AI analysis usefulness
* Practical real-world applications

---

# Features

* Create MP3 + UNMP3 companion files
* Reconstruct original WAV files
* Bit-perfect verification
* Residual analysis
* Command-line interface
* GUI version for easier operation

---

# Installation

## Step 1: Install Python

### Windows

Download Python from:

https://www.python.org

During installation, check:

```text
Add Python to PATH
```

### macOS

```bash
brew install python3
```

### Linux

Python is usually preinstalled.

Verify installation:

```bash
python --version
```

---

## Step 2: Install NumPy

```bash
pip install numpy
```

---

## Step 3: Install FFmpeg

### Windows

Download FFmpeg and add the `bin` directory to your PATH.

https://ffmpeg.org

### macOS

```bash
brew install ffmpeg
```

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install ffmpeg
```

### Fedora

```bash
sudo dnf install ffmpeg
```

Verify required codecs:

```bash
ffmpeg -encoders | findstr mp3lame
```

Windows

```bash
ffmpeg -encoders | grep mp3lame
ffmpeg -encoders | grep flac
```

macOS / Linux

You should see:

```text
libmp3lame
flac
```

---

# Usage

## Run Test Suite

```bash
python unmp3.py test
```

This generates a 10-second test tone and performs:

* MP3 encoding
* Residual generation
* Reconstruction
* Verification

Tested at:

* 128 kbps
* 192 kbps
* 256 kbps
* 320 kbps

A size comparison table is displayed when complete.

---

## Create MP3 + UNMP3

```bash
python unmp3.py encode song.wav song.mp3 song.unmp3 --bitrate 320k
```

Produces:

```text
song.mp3
song.unmp3
```

---

## Reconstruct Original WAV

```bash
python unmp3.py decode song.mp3 song.unmp3 song_restored.wav
```

Produces:

```text
song_restored.wav
```

---

## Verify Reconstruction

```python
from unmp3 import UnMP3Codec

codec = UnMP3Codec()

codec.verify(
    "song.wav",
    "song_restored.wav"
)
```

If verification succeeds:

```text
✓ 16-perfect reconstruction achieved
```

---

# Residual Generation Examples

## FFmpeg + SoX

```bash
# Decode MP3

ffmpeg -i file.mp3 \
    -ar 44100 \
    -ac 2 \
    -sample_fmt s16 \
    mp3_as_wav.wav

# Generate residual

sox original.wav \
    mp3_as_wav.wav \
    residual.wav \
    mix -1
```

---

## Python Example

```python
import numpy as np
import soundfile as sf

original, sr = sf.read("original.wav")
mp3_decoded, _ = sf.read("decoded_mp3.wav")

min_len = min(len(original), len(mp3_decoded))

diff = original[:min_len] - mp3_decoded[:min_len]

sf.write("residual.wav", diff, sr)
```

The resulting `residual.wav` contains everything the MP3 encoder removed.

---

# Future Ideas

Potential areas of exploration:

* Dedicated `.unmp3` file specification
* Improved compression of residual data
* FLAC-compressed residual streams
* AI-assisted reconstruction
* Stem separation enhancement
* Broadcast-quality restoration workflows
* Streaming + restoration distribution models
* Research into perceptual coding losses

---

# Disclaimer

UNMP3 is currently an experimental research project and should not be considered a finalized archival format. The concepts explored here are intended for investigation, learning, and experimentation within digital audio processing.



Added GUI version which allows the script to be run to launch of GUI to load files through select and click functions making it easier to use.
Step-by-Step Installation
Step 1: Install Python (if not already installed)
Windows: Download from python.org (check "Add to PATH")
macOS: brew install python3
Linux: Usually pre-installed
Verify:
python --version
Step 2: Install NumPy

pip install numpy
Step 3: Install FFmpeg (the heavy lifter)

Windows	Download from ffmpeg.org, add bin folder to PATH

macOS	brew install ffmpeg

Linux (Ubuntu/Debian)	sudo apt update && sudo apt install ffmpeg

Linux (Fedora)	sudo dnf install ffmpeg

Verify ffmpeg has the codecs we need:

ffmpeg -encoders | findstr mp3lame   # Windows
ffmpeg -encoders | grep mp3lame      # macOS/Linux
ffmpeg -encoders | grep flac
You should see libmp3lame and flac in the output.

How to use
TEST
python unmp3.py test
This generates a 10-second test tone and runs the full encode/decode/verify cycle at 4 bitrates (128k, 192k, 256k, 320k). You'll see a size comparison table at the end.

CREATE THE SPLIT Mp3 / UNMp3
python unmp3.py encode song.wav song.mp3 song.unmp3 --bitrate 320k

Reconstruct the RAW / Decode Back to WAV

python unmp3.py decode song.mp3 song.unmp3 song_restored.wav

Verify 
from unmp3 import UnMP3Codec
codec = UnMP3Codec()
codec.verify("song.wav", "song_restored.wav")
If BIT-PERFECT	Your .unmp3 successfully reconstructed the original
universally playable MP3 file for free.

For anyone having difficulty understanding the concept this should help you understand a bit more

Residual = Original WAV − (MP3 decoded back to WAV)       So when you compare the original raw and subtract the lossy version you are given the residual which is the UNMP3 my gimmick file name to describe the residual container format.


Anything non-zero in the residual is information the MP3 encoder threw away.
What the Residual Contains

Component	What You'll Hear/See
High-frequency loss	MP3 cuts everything above ~16–20 kHz (depending on bitrate). The residual will have "air" and shimmer above that cutoff.
Pre-echo & smearing	Transients (snare hits, cymbals) get blurred in time. The residual captures the sharp attack that's been softened.
Quantization noise	Psychoacoustic masking pushes noise into frequency bands where your ear is "distracted." The residual is full of this shaped noise.
Stereo phase issues	At lower bitrates, MP3 uses joint stereo which can collapse or alter stereo imaging. The residual reveals phase anomalies.
Practical Methods
1. Command Line (SoX + FFmpeg)
bash
Decode MP3 to WAV at same bit depth/sample rate as original
ffmpeg -i file.mp3 -ar 44100 -ac 2 -sample_fmt s16 mp3_as_wav.wav

Invert MP3 and mix with original (requires SoX)
sox original.wav mp3_as_wav.wav residual.wav mix -1
2. Python (librosa / scipy)
Python
import numpy as np
import soundfile as sf

original, sr = sf.read('original.wav')
mp3_decoded, _ = sf.read('decoded_mp3.wav')  # decode first

Ensure same length
min_len = min(len(original), len(mp3_decoded))
diff = original[:min_len] - mp3_decoded[:min_len]

sf.write('residual.wav', diff, sr) 
