# Converter

A polished macOS media converter with drag-and-drop and a smart format guesser.

**Images** · HEIC/HEIF, JPG, PNG, WebP, GIF, BMP, TIFF, ICO, AVIF  
**Video** · MP4, MOV, MKV, AVI, WebM, M4V, WMV, FLV…  
**Audio** · MP3, WAV, AAC, M4A, FLAC, OGG, Opus, AIFF…

| Common jobs | Default guess |
|-------------|---------------|
| HEIC → JPG | Apple photo → shareable |
| MOV → MP4 | iPhone video → universal |
| MP4 → GIF | Short clip → animated GIF |
| WAV → MP3 | Uncompressed → portable |
| Screenshot → PNG | Keep lossless |
| Video → MP3 | Extract audio track |

## Quick start

```bash
./run.sh
```

Or:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

**Requirements:** Python 3.10+, [ffmpeg](https://ffmpeg.org/) (`brew install ffmpeg`) for video/audio.

## How it works

### Convert mode (default)

1. **Drop** files (or browse)
2. The app **guesses** a target format using heuristics + your past choices
3. Override anytime with the format chips
4. Quality defaults to **Max** (100%) — lower the slider only if you want a smaller file
5. Hit **Convert** — results never overwrite existing files

### Size estimates

As you change **format**, **quality**, or **compress preset**, each file shows an
estimated output size (e.g. `≈ 1.2 MB  (−64%)`) and a batch strip totals the delta.
Estimates use source metadata (resolution, duration) plus format heuristics — they
are approximate, not a dry-run encode.

### Compress mode

Switch to **Compress** to shrink files without hunting for a format:

| Preset | Quality | Max dimension | Notes |
|--------|---------|---------------|-------|
| **Light** | 88 | unlimited | Subtle re-encode |
| **Balanced** | 75 | 1920px | Good for phone photos / 1080p video |
| **Small** | 60 | 1280px | Aggressive; prefers WebP / MP4 / MP3 |

Outputs are named `*_compressed.ext` and the log shows before/after sizes plus % saved.

### Smart guesser

Scores every possible output from:

- Built-in rules (HEIC→JPG, MOV→MP4, screenshots→PNG, …)
- Filename hints (`Screenshot`, `voice`, `meme`, …)
- File size (small video → GIF; large image → WebP/JPG)
- Your history (`~/.config/format-converter/history.json`)

Successful conversions are remembered so the next HEIC batch prefers whatever *you* pick.

## Project layout

```
converter/
  engine.py    # Pillow + ffmpeg conversion
  guesser.py   # format prediction + learning
  app.py       # Tk GUI
main.py
run.sh
```

## Shortcuts

| Key | Action |
|-----|--------|
| ⌘O | Browse files |
| ⌘↩ | Convert |
| ⌘⌫ | Clear queue |
