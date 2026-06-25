I had a thought to make a "partner file" for mp3 files that can undo the destructive loss processes that strip data from the mp3 file.
By storing the stripped data in a paired file called the "UNMP3" this allows the two files to be recombined back to their original raw wav format.

The idea here was to create a way of supplying not only data for returning mp3 to a lossless form without distributing the actual .wav file but that
these unmp3 files might have use in stem seperation or other analytic understanding of mp3 data they are working with.

The first application of taking a .wav raw format and splitting rendering to not only the Mp3 version BUT also presering the loss in the unmp3
and creating a split stream way of transfering data in an easily restored format for instance in application where you want a version for streaming
but you may want to supply the unmp3 version to people who have a need for higher quality such as broadcast.
The thought into loss and its potential dirtying of AI audio usages raised the thought that perhaps the loss even though mostly noise
to humans might actually serve useful additional functions in an age of AI generative audio and stem splitting.

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
