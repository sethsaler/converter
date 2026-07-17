# Converter

A polished macOS media converter with drag-and-drop, a smart format guesser, and a CLI.

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

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/sethsaler/converter/main/install.sh | bash
```

This installs to `~/.local/share/converter`, puts `converter` on `~/.local/bin`, and (on macOS) drops a double-click launcher in `~/Applications/Converter.command`.

Make sure `~/.local/bin` is on your PATH:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

**Requirements:** Python 3.10+, [ffmpeg](https://ffmpeg.org/) (`brew install ffmpeg`) for video/audio.

### Manual / from source

```bash
git clone https://github.com/sethsaler/converter.git
cd converter
./run.sh          # GUI
# or double-click Converter.command in Finder
```

## Usage

### GUI

```bash
converter          # or: converter gui
./run.sh
# Double-click Converter.command
```

1. **Drop** files (or browse)
2. The app **guesses** a target format using heuristics + your past choices
3. Override anytime with the format chips
4. Quality defaults to **Max** (100%) — lower the slider only if you want a smaller file
5. Hit **Convert** — results never overwrite existing files

### CLI

```bash
# Smart-guess convert
converter photo.heic

# Explicit format
converter photo.heic -f jpg
converter clip.mov -f mp4 -q 85
converter song.wav -f mp3 -o ~/Desktop/out

# Compress presets (light | balanced | small)
converter compress shot.png -p small
converter compress video.mov -p balanced -o ~/Desktop

# List formats
converter --formats
converter --help
```

| Flag | Meaning |
|------|---------|
| `-f`, `--format` | Output format (default: smart guess) |
| `-q`, `--quality` | Quality 1–100 (default: 100) |
| `-o`, `--output` | Output directory |
| `--max-dim` | Cap longest side in pixels |
| `-p`, `--preset` | Compress: `light`, `balanced`, `small` |
| `-v`, `--verbose` | Engine progress |

Directories are expanded to supported media files inside them.

## How it works

### Size estimates (GUI)

As you change **format**, **quality**, or **compress preset**, each file shows an
estimated output size (e.g. `≈ 1.2 MB  (−64%)`) and a batch strip totals the delta.
Estimates use source metadata (resolution, duration) plus format heuristics — they
are approximate, not a dry-run encode.

### Compress mode

Switch to **Compress** (GUI) or use `converter compress` to shrink files without
hunting for a format:

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
  estimate.py  # size estimates
  app.py       # Tk GUI
  cli.py       # command-line interface
main.py
run.sh
Converter.command   # macOS double-click launcher
install.sh          # curl | bash installer
```

## Shortcuts (GUI)

| Key | Action |
|-----|--------|
| ⌘O | Browse files |
| ⌘↩ | Convert |
| ⌘⌫ | Clear queue |

## Uninstall

```bash
rm -rf ~/.local/share/converter ~/.local/bin/converter
rm -f ~/Applications/Converter.command   # macOS
```
