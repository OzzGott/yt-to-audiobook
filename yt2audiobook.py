#!/usr/bin/env python3
"""
yt2audiobook — turn a YouTube video into an .m4b audiobook.

  * audio      -> AAC in an .m4b container (the standard audiobook format),
                 re-encoded to mono 64 kbps by default to keep files small
  * thumbnail  -> embedded cover art (cropped square by default)
  * channel    -> author (artist / album artist) and narrator (composer)
  * title      -> book title
  * chapters   -> YouTube chapters (incl. timestamps in the description)
  * description, upload year and source URL are stored as well
  * flagged as an Audiobook (iTunes media type), genre "Audiobook"

Requires yt-dlp and ffmpeg on your PATH.

Usage:
  python yt2audiobook.py URL [URL ...]
  python yt2audiobook.py URL -o ~/Audiobooks --cover fit --sponsorblock
  python yt2audiobook.py URL --author "Real Author" --title "Real Title"
  python yt2audiobook.py URL --bitrate 48k        # smaller file
  python yt2audiobook.py URL --bitrate original   # keep YouTube's audio as is
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd):
    result = subprocess.run(cmd)
    if result.returncode != 0:
        die(f"command failed: {cmd[0]} (exit code {result.returncode})")


def safe_filename(name, max_len=150):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)  # illegal on Windows
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    return name[:max_len] or "audiobook"


def download(url, workdir, sponsorblock):
    """Download audio (m4a with chapters), the thumbnail and the info JSON."""
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "-x", "--audio-format", "m4a",      # no re-encode if already AAC
        "--embed-chapters",
        "--write-thumbnail", "--convert-thumbnails", "jpg",
        "--write-info-json",
        "-P", workdir,
        "-o", "%(id)s.%(ext)s",
        "-o", "thumbnail:%(id)s.%(ext)s",
        "-o", "infojson:%(id)s",
    ]
    if sponsorblock:
        # Cut sponsor segments, self-promotion and "like and subscribe"
        # reminders; yt-dlp shifts the chapter times to match.
        cmd += ["--sponsorblock-remove", "sponsor,selfpromo,interaction"]
    cmd.append(url)
    run(cmd)

    def find(pattern):
        hits = glob.glob(os.path.join(workdir, pattern))
        return hits[0] if hits else None

    audio = find("*.m4a")
    info_path = find("*.info.json")
    thumb = find("*.jpg")
    if not audio or not info_path:
        die("yt-dlp finished but the audio or info file is missing")
    with open(info_path, encoding="utf-8") as f:
        info = json.load(f)
    return audio, thumb, info


def make_cover(thumb, out_path, mode):
    """Prepare the cover: 'crop' to centre square, 'fit' (pad to square), or 'original'."""
    filters = {
        "crop": "crop='min(iw,ih)':'min(iw,ih)'",
        "fit": "pad='max(iw,ih)':'max(iw,ih)':(ow-iw)/2:(oh-ih)/2:color=black",
        "original": "null",
    }
    run(["ffmpeg", "-loglevel", "error", "-y", "-i", thumb,
         "-vf", filters[mode], "-q:v", "2", out_path])
    return out_path


def assemble(audio, cover, out_path, meta, bitrate="64k", stereo=False):
    """Mux audio + cover + chapters + metadata into an .m4b."""
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", audio]
    if cover:
        cmd += ["-i", cover]
    cmd += ["-map", "0:a"]
    if cover:
        cmd += ["-map", "1:v", "-disposition:v:0", "attached_pic"]
    cmd += [
        "-c", "copy",
    ]
    if bitrate != "original":
        # Re-encode the audio for speech: mono at a lower bitrate is
        # practically indistinguishable for spoken word and much smaller.
        cmd += ["-c:a", "aac", "-b:a", bitrate, "-ac", "2" if stereo else "1"]
    cmd += [
        "-map_metadata:g", "-1",  # drop yt-dlp's global tags, we write our own
        "-map_metadata:s", "-1",
        "-map_chapters", "0",     # keep the chapters (with their titles)
        "-f", "ipod",            # the .m4b flavour of MP4
    ]
    for key, value in meta.items():
        if value:
            cmd += ["-metadata", f"{key}={value}"]
    cmd.append(out_path)
    run(cmd)


def convert(url, args):
    with tempfile.TemporaryDirectory(prefix="yt2ab-") as workdir:
        print(f"\n==> Downloading {url}")
        audio, thumb, info = download(url, workdir, args.sponsorblock)

        title = args.title or info.get("title") or info.get("id")
        author = args.author or info.get("channel") or info.get("uploader") or "Unknown"
        year = (info.get("upload_date") or "")[:4]
        description = info.get("description") or ""
        source = info.get("webpage_url") or url

        cover = None
        if thumb and args.cover != "none":
            cover = make_cover(thumb, os.path.join(workdir, "cover.jpg"), args.cover)

        meta = {
            "title": title,
            "album": title,
            "artist": author,
            "album_artist": author,
            "composer": author,        # many players show this as the narrator
            "genre": "Audiobook",
            "date": year,
            "description": description[:255],
            "synopsis": description,   # long description
            "comment": source,
            "media_type": "2",         # iTunes "Audiobook" type
        }

        os.makedirs(args.output, exist_ok=True)
        out_path = os.path.join(args.output, safe_filename(f"{author} - {title}") + ".m4b")
        print(f"==> Building {os.path.basename(out_path)}")
        assemble(audio, cover, out_path, meta, args.bitrate, args.stereo)

        n_chapters = len(info.get("chapters") or [])
        print(f"==> Done: {out_path}"
              + (f"  ({n_chapters} chapters)" if n_chapters else "  (no chapters on this video)"))


def main():
    p = argparse.ArgumentParser(description="Turn YouTube videos into .m4b audiobooks.")
    p.add_argument("urls", nargs="+", help="one or more YouTube video URLs")
    p.add_argument("-o", "--output", default=".", help="output folder (default: current folder)")
    p.add_argument("--cover", choices=["crop", "fit", "original", "none"], default="crop",
                   help="how to turn the 16:9 thumbnail into a cover (default: crop to square)")
    p.add_argument("--sponsorblock", action="store_true",
                   help="cut sponsor/self-promo segments using SponsorBlock")
    p.add_argument("--bitrate", default="64k",
                   help="audio bitrate, e.g. 48k, 64k, 96k, or 'original' to keep "
                        "YouTube's audio untouched (default: 64k)")
    p.add_argument("--stereo", action="store_true",
                   help="keep stereo (default: mono, which suits spoken word)")
    p.add_argument("--author", help="override the author (default: channel name)")
    p.add_argument("--title", help="override the title (default: video title)")
    args = p.parse_args()

    for tool in ("yt-dlp", "ffmpeg"):
        if not shutil.which(tool):
            die(f"{tool} not found on PATH")
    if (args.author or args.title) and len(args.urls) > 1:
        die("--author/--title only make sense with a single URL")

    args.output = os.path.expanduser(args.output)
    for url in args.urls:
        convert(url, args)


if __name__ == "__main__":
    main()
