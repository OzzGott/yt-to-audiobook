# yt2audiobook

Turn a YouTube video into a proper audiobook file (`.m4b`) for your phone.

- **Cover:** the video thumbnail, cropped to a square
- **Author:** the channel name
- **Title:** the video title
- **Chapters:** taken from the video's chapters or the timestamps in its description
- **Also included:** the description, upload year and a link back to the video

Apple Books, and Android audiobook apps such as Smart AudioBook Player or Voice, recognise `.m4b` as a book. They remember your position, show the chapters and let you change playback speed.

Files are compressed for speech by default (mono, 64 kbps), which is roughly half the size of YouTube's original audio with no loss you'd notice in spoken word.

## Requirements

| Tool | What it's for |
|---|---|
| **Python 3.10+** | Runs the script |
| **yt-dlp** | Downloads the video's audio, thumbnail and info |
| **ffmpeg** | Converts the audio and adds the cover, chapters and tags |
| **Deno** | A JavaScript runtime yt-dlp needs to download from YouTube |

## Installation

### macOS

Install [Homebrew](https://brew.sh) if you don't have it, then:

```bash
brew install python ffmpeg deno yt-dlp
```

### Windows

In PowerShell:

```powershell
winget install Python.Python.3.12 Gyan.FFmpeg DenoLand.Deno
pip install -r requirements.txt
```

Close and reopen PowerShell afterwards so the new commands are found.

### Linux (Debian/Ubuntu)

```bash
sudo apt install python3 python3-venv ffmpeg
curl -fsSL https://deno.land/install.sh | sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Check that it worked

Each of these should print a version number:

```bash
python3 --version
yt-dlp --version
ffmpeg -version
deno --version
```

On Windows, use `python` instead of `python3`.

## Usage

```bash
python3 yt2audiobook.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

The `.m4b` is saved in the current folder as `Channel - Title.m4b`. Put the link in quotes so your shell doesn't misread characters like `&`.

### Examples

```bash
# Several videos at once, saved to a specific folder
python3 yt2audiobook.py URL1 URL2 URL3 -o ~/Audiobooks

# Cut out sponsor segments (uses SponsorBlock)
python3 yt2audiobook.py URL --sponsorblock

# The uploader isn't the real author
python3 yt2audiobook.py URL --author "Jane Austen" --title "Pride and Prejudice"

# Even smaller file
python3 yt2audiobook.py URL --bitrate 48k
```

### Options

| Option | What it does |
|---|---|
| `-o`, `--output FOLDER` | Where to save the files (default: current folder) |
| `--bitrate RATE` | Audio quality: `32k`, `48k`, `64k` (default), `96k`, or `original` to keep YouTube's audio unchanged |
| `--stereo` | Keep stereo; useful when there's music worth hearing (default: mono) |
| `--cover MODE` | `crop` to a square (default), `fit` with black bars, `original` 16:9, or `none` |
| `--sponsorblock` | Remove sponsor, self-promotion and "like and subscribe" segments |
| `--author NAME` | Override the author (single video only) |
| `--title TITLE` | Override the title (single video only) |

Rough file sizes for one hour of audio:

| Bitrate | Size per hour | Quality for speech |
|---|---|---|
| 32k | ~15 MB | Slightly muffled, but fine |
| 48k | ~22 MB | Very good |
| 64k | ~29 MB | Essentially transparent |
| original | ~60 MB | Same as YouTube |

## Getting the files onto your phone

**iPhone:**
1. Open the **Books** app on your Mac.
2. Drag the `.m4b` files into its window. They appear under *Audiobooks*.
3. Connect the iPhone and select it in the Finder sidebar.
4. Open the **Audiobooks** tab, tick *Sync audiobooks*, and click **Apply**.

Dropping the files directly onto the iPhone in Finder doesn't work. They have to go through Books first.

**Android:** copy the files to the phone (USB, Google Drive, etc.) and open the folder in an audiobook app such as Smart AudioBook Player or Voice.

## Shrinking files you already made

If you made files with `--bitrate original` (or with an older version of this script), you can compress them in place without downloading again. This **replaces the originals**, so back them up first if you want to keep full quality, and run it only once per file.

macOS / Linux, from the folder with the files:

```bash
for f in *.m4b; do
  ffmpeg -loglevel error -y -i "$f" -map 0:a -map 0:v -c copy -c:a aac -b:a 64k -ac 1 \
    -disposition:v:0 attached_pic -f ipod "$f.tmp" && mv "$f.tmp" "$f" || rm -f "$f.tmp"
done
```

You may see warnings like `Application provided duration ... is invalid`. These are harmless.

## Troubleshooting

- **The download suddenly fails, or YouTube says "Sign in to confirm you're not a bot".** YouTube changes often, and yt-dlp releases fixes quickly. Update it with `brew upgrade yt-dlp` (macOS) or `pip install -U "yt-dlp[default]"` (Windows/Linux), then try again.
- **A "JavaScript runtime" or "n challenge" warning.** Deno is missing or not on your PATH. Install it as shown above and open a new terminal.
- **`yt-dlp not found on PATH` / `ffmpeg not found on PATH`.** Install the missing tool, or open a new terminal window so it's picked up.
- **No chapters.** The video doesn't have any. YouTube chapters come from timestamps the uploader added.

## A note on use

Only download videos you have the right to keep, such as your own uploads, public-domain works or content whose licence allows it. Respect YouTube's terms and creators' copyright.
