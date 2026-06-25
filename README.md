I had a thought to make a "partner file" for mp3 files that can undo the destructive loss processes that strip data from the mp3 file.
By storing the stripped data in a paired file called the "UNMP3" this allows the two files to be recombined back to their original raw wav format.

The idea here was to create a way of supplying not only data for returning mp3 to a lossless form without distributing the actual .wav file but that
these unmp3 files might have use in stem seperation or other analytic understanding of mp3 data they are working with.

The first application of taking a .wav raw format and splitting rendering to not only the Mp3 version BUT also presering the loss in the unmp3
and creating a split stream way of transfering data in an easily restored format for instance in application where you want a version for streaming
but you may want to supply the unmp3 version to people who have a need for higher quality such as broadcast.
The thought into loss and its potential dirtying of AI audio usages raised the thought that perhaps the loss even though mostly noise
to humans might actually serve useful additional functions in an age of AI generative audio and stem splitting.

What is interesting about this and I still need to test, that the .unmp3 file is still an audio file and you can play it, even by changing it from .unmp3 to .mp3  however I'm not entirely sure if my methods make sense just yet more testing is needed.  Again I feel like this will be useful for audio processing purposes as mp3 files are fine for audio broadcast or listening at reduced datasizes to .wav however a file that will allow files to be reverted to wav or "worked with" analytically via AI tooks like generative AI or stem seperation to have a data sense of what information needs to be accounted for in a source file that isn't present, seems to actually have a usuage. I think there may be more to it. I havn't actually encountered this before, it was just an idea that popped into my head today so I thought I would run with the idea.



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


---------
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
