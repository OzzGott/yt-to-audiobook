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

When several URLs are given, up to 3 are processed at the same time
(change with -j). A failed video doesn't stop the others.

Requires yt-dlp and ffmpeg on your PATH.

Usage:
  python yt2audiobook.py URL [URL ...]
  python yt2audiobook.py URL1 URL2 URL3 -j 4      # 4 at a time
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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

_print_lock = threading.Lock()


class ConvertError(Exception):
    pass


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd, quiet=False):
    """Run a command. In quiet mode its output is captured (so parallel jobs
    don't scramble the terminal) and only shown if it fails."""
    if quiet:
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    else:
        result = subprocess.run(cmd)
    if result.returncode != 0:
        msg = f"{cmd[0]} failed (exit code {result.returncode})"
        if quiet:
            tail = (result.stderr or result.stdout or "").strip().splitlines()[-5:]
            if tail:
                msg += ":\n      " + "\n      ".join(tail)
        raise ConvertError(msg)


def safe_filename(name, max_len=150):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)  # illegal on Windows
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    return name[:max_len] or "audiobook"


def download(url, workdir, sponsorblock, quiet=False):
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
    if quiet:
        cmd.append("--no-progress")
    if sponsorblock:
        # Cut sponsor segments, self-promotion and "like and subscribe"
        # reminders; yt-dlp shifts the chapter times to match.
        cmd += ["--sponsorblock-remove", "sponsor,selfpromo,interaction"]
    cmd.append(url)
    run(cmd, quiet)

    def find(pattern):
        hits = glob.glob(os.path.join(workdir, pattern))
        return hits[0] if hits else None

    audio = find("*.m4a")
    info_path = find("*.info.json")
    thumb = find("*.jpg")
    if not audio or not info_path:
        raise ConvertError("yt-dlp finished but the audio or info file is missing")
    with open(info_path, encoding="utf-8") as f:
        info = json.load(f)
    return audio, thumb, info


def probe_json(path, *args):
    result = subprocess.run(["ffprobe", "-v", "error", *args, "-of", "json", path],
                            capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        raise ConvertError(f"ffprobe could not read {path}")
    return json.loads(result.stdout or "{}")


def audio_duration(path):
    streams = probe_json(path, "-select_streams", "a:0",
                         "-show_entries", "stream=duration").get("streams") or []
    try:
        return float(streams[0]["duration"])
    except (IndexError, KeyError, ValueError):
        raise ConvertError(f"could not read the audio length of {path}")


def read_chapters(path):
    chapters = []
    for ch in probe_json(path, "-show_chapters").get("chapters") or []:
        chapters.append((float(ch["start_time"]), float(ch["end_time"]),
                         (ch.get("tags") or {}).get("title", "")))
    return chapters


def _ffmeta_escape(text):
    return re.sub(r"([=;#\\\n])", r"\\\1", text)


def write_chapter_file(chapters, max_end, path):
    """Write chapters as an ffmetadata file, trimmed so none runs past max_end.

    YouTube chapter times are whole seconds, and the audio can be a fraction of
    a second shorter than the video. A chapter that runs past the end of the
    audio makes iPhone syncing silently skip the whole book."""
    limit_ms = int(max_end * 1000) - 50          # small safety margin
    lines = [";FFMETADATA1"]
    count = 0
    for start, end, title in chapters:
        s, e = int(start * 1000), min(int(end * 1000), limit_ms)
        if e - s < 1000:                         # drop empty/negligible chapters
            continue
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={s}", f"END={e}",
                  f"title={_ffmeta_escape(title)}"]
        count += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return count


def make_cover(thumb, out_path, mode, quiet=False):
    """Prepare the cover: 'crop' to centre square, 'fit' (pad to square), or 'original'."""
    filters = {
        "crop": "crop='min(iw,ih)':'min(iw,ih)'",
        "fit": "pad='max(iw,ih)':'max(iw,ih)':(ow-iw)/2:(oh-ih)/2:color=black",
        "original": "null",
    }
    run(["ffmpeg", "-loglevel", "error", "-y", "-i", thumb,
         "-vf", filters[mode], "-q:v", "2", out_path], quiet)
    return out_path


def assemble(audio, cover, out_path, meta, bitrate="64k", stereo=False, quiet=False,
             chapter_file=None):
    """Mux audio + cover + chapters + metadata into an .m4b."""
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", audio]
    if cover:
        cmd += ["-i", cover]
    chapter_input = None
    if chapter_file:
        chapter_input = 2 if cover else 1
        cmd += ["-i", chapter_file]
    cmd += ["-map", "0:a"]
    if cover:
        cmd += ["-map", "1:v", "-disposition:v:0", "attached_pic"]
    cmd += ["-c", "copy"]
    if bitrate != "original":
        # Re-encode the audio for speech: mono at a lower bitrate is
        # practically indistinguishable for spoken word and much smaller.
        cmd += ["-c:a", "aac", "-b:a", bitrate, "-ac", "2" if stereo else "1"]
    cmd += [
        "-map_metadata:g", "-1",  # drop yt-dlp's global tags, we write our own
        "-map_metadata:s", "-1",
        # chapters from the trimmed chapter file, or none at all
        "-map_chapters", str(chapter_input) if chapter_input is not None else "-1",
        "-f", "ipod",             # the .m4b flavour of MP4
    ]
    for key, value in meta.items():
        if value:
            cmd += ["-metadata", f"{key}={value}"]
    cmd.append(out_path)
    run(cmd, quiet)


def convert(url, args, tag="", quiet=False):
    """Convert one video. Returns the output path; raises ConvertError on failure."""
    with tempfile.TemporaryDirectory(prefix="yt2ab-") as workdir:
        log(f"{tag}Downloading {url}")
        audio, thumb, info = download(url, workdir, args.sponsorblock, quiet)

        title = args.title or info.get("title") or info.get("id")
        author = args.author or info.get("channel") or info.get("uploader") or "Unknown"
        year = (info.get("upload_date") or "")[:4]
        description = info.get("description") or ""
        source = info.get("webpage_url") or url

        cover = None
        if thumb and args.cover != "none":
            cover = make_cover(thumb, os.path.join(workdir, "cover.jpg"), args.cover, quiet)

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
        chapter_file = os.path.join(workdir, "chapters.txt")
        n_chapters = write_chapter_file(read_chapters(audio), audio_duration(audio), chapter_file)

        log(f"{tag}Building {os.path.basename(out_path)}")
        assemble(audio, cover, out_path, meta, args.bitrate, args.stereo, quiet,
                 chapter_file if n_chapters else None)

        log(f"{tag}Done: {out_path}"
            + (f"  ({n_chapters} chapters)" if n_chapters else "  (no chapters on this video)"))
        return out_path


def fix_chapters(path):
    """Trim chapters that run past the end of the audio in an existing .m4b,
    replacing the file. Copies the audio as is (no quality loss).
    Returns True if the file was changed."""
    duration = audio_duration(path)
    chapters = read_chapters(path)
    if not chapters or max(end for _, end, _ in chapters) <= duration:
        log(f"OK (no change needed): {path}")
        return False

    has_cover = bool(probe_json(path, "-select_streams", "v",
                                "-show_entries", "stream=index").get("streams"))
    with tempfile.TemporaryDirectory(prefix="yt2ab-fix-") as workdir:
        chapter_file = os.path.join(workdir, "chapters.txt")
        n = write_chapter_file(chapters, duration, chapter_file)
        tmp = os.path.join(workdir, "fixed.m4b")
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", path, "-i", chapter_file,
               "-map", "0:a"]
        if has_cover:
            cmd += ["-map", "0:v", "-disposition:v:0", "attached_pic"]
        cmd += ["-c", "copy", "-map_metadata", "0",
                "-map_chapters", "1" if n else "-1", "-f", "ipod", tmp]
        run(cmd, quiet=True)
        shutil.move(tmp, path)
    log(f"Fixed: {path}")
    return True


def main():
    p = argparse.ArgumentParser(
        description="Turn YouTube videos into .m4b audiobooks.",
        epilog="To repair existing .m4b files whose chapters run past the end of the "
               "audio (they won't sync to an iPhone): yt2audiobook.py --fix-chapters *.m4b")
    p.add_argument("urls", nargs="+", metavar="URL",
                   help="one or more YouTube video URLs (or .m4b files with --fix-chapters)")
    p.add_argument("--fix-chapters", action="store_true",
                   help="repair existing .m4b files given instead of URLs, in place")
    p.add_argument("-o", "--output", default=".", help="output folder (default: current folder)")
    p.add_argument("-j", "--jobs", type=int, default=3,
                   help="how many videos to process at the same time (default: 3)")
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

    if args.fix_chapters:
        for tool in ("ffmpeg", "ffprobe"):
            if not shutil.which(tool):
                die(f"{tool} not found on PATH")
        fixed = errors = 0
        for path in args.urls:
            try:
                fixed += fix_chapters(path)
            except ConvertError as e:
                log(f"FAILED: {path}\n      {e}")
                errors += 1
        log(f"\n{fixed} fixed, {len(args.urls) - fixed - errors} already fine, {errors} failed.")
        sys.exit(1 if errors else 0)

    for tool in ("yt-dlp", "ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            die(f"{tool} not found on PATH")
    if (args.author or args.title) and len(args.urls) > 1:
        die("--author/--title only make sense with a single URL")
    if args.jobs < 1:
        die("--jobs must be at least 1")

    args.output = os.path.expanduser(args.output)
    urls = list(dict.fromkeys(args.urls))   # drop duplicate links
    jobs = min(args.jobs, len(urls))
    failed = []

    if jobs == 1:
        # One at a time: show yt-dlp's normal progress output.
        for url in urls:
            try:
                convert(url, args)
            except ConvertError as e:
                log(f"FAILED: {url}\n      {e}")
                failed.append(url)
    else:
        total = len(urls)
        log(f"Processing {total} videos, {jobs} at a time...")
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(convert, url, args, f"[{i}/{total}] ", True): (i, url)
                for i, url in enumerate(urls, 1)
            }
            for fut in as_completed(futures):
                i, url = futures[fut]
                try:
                    fut.result()
                except ConvertError as e:
                    log(f"[{i}/{total}] FAILED: {url}\n      {e}")
                    failed.append(url)

    if len(urls) > 1:
        log(f"\nFinished: {len(urls) - len(failed)} succeeded, {len(failed)} failed.")
        for url in failed:
            log(f"  failed: {url}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()