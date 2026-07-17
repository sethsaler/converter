"""Conversion engine for images, video, and audio."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:
    HEIF_SUPPORTED = False

ProgressCallback = Callable[[str], None]

IMAGE_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
    ".gif",
    ".heic",
    ".heif",
    ".ico",
    ".avif",
    ".jfif",
    ".psd",
}

VIDEO_EXTS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
    ".wmv",
    ".flv",
    ".mpeg",
    ".mpg",
    ".3gp",
    ".ts",
}

AUDIO_EXTS = {
    ".mp3",
    ".wav",
    ".aac",
    ".m4a",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
    ".aiff",
    ".aif",
    ".caf",
}

# Formats this app can write
OUTPUT_FORMATS: dict[str, dict] = {
    # Images
    "jpg": {"ext": ".jpg", "kind": "image", "label": "JPG", "desc": "Photos, universal"},
    "png": {"ext": ".png", "kind": "image", "label": "PNG", "desc": "Lossless, transparency"},
    "webp": {"ext": ".webp", "kind": "image", "label": "WebP", "desc": "Web, smaller files"},
    "gif": {"ext": ".gif", "kind": "both", "label": "GIF", "desc": "Animation / memes"},
    "bmp": {"ext": ".bmp", "kind": "image", "label": "BMP", "desc": "Uncompressed"},
    "tiff": {"ext": ".tiff", "kind": "image", "label": "TIFF", "desc": "Print / archive"},
    "ico": {"ext": ".ico", "kind": "image", "label": "ICO", "desc": "App / favicon"},
    "avif": {"ext": ".avif", "kind": "image", "label": "AVIF", "desc": "Modern web, tiny"},
    # Video
    "mp4": {"ext": ".mp4", "kind": "video", "label": "MP4", "desc": "Universal video"},
    "webm": {"ext": ".webm", "kind": "video", "label": "WebM", "desc": "Web video"},
    "mov": {"ext": ".mov", "kind": "video", "label": "MOV", "desc": "Apple / QuickTime"},
    "mkv": {"ext": ".mkv", "kind": "video", "label": "MKV", "desc": "Flexible container"},
    "avi": {"ext": ".avi", "kind": "video", "label": "AVI", "desc": "Legacy video"},
    # Audio
    "mp3": {"ext": ".mp3", "kind": "audio", "label": "MP3", "desc": "Universal audio"},
    "wav": {"ext": ".wav", "kind": "audio", "label": "WAV", "desc": "Lossless PCM"},
    "aac": {"ext": ".aac", "kind": "audio", "label": "AAC", "desc": "Efficient audio"},
    "m4a": {"ext": ".m4a", "kind": "audio", "label": "M4A", "desc": "Apple audio"},
    "flac": {"ext": ".flac", "kind": "audio", "label": "FLAC", "desc": "Lossless archive"},
    "ogg": {"ext": ".ogg", "kind": "audio", "label": "OGG", "desc": "Open source"},
    "opus": {"ext": ".opus", "kind": "audio", "label": "Opus", "desc": "Voice / streaming"},
}

FORMAT_LABELS = {k: v["label"] for k, v in OUTPUT_FORMATS.items()}


@dataclass
class ConversionResult:
    source: Path
    output: Path | None
    success: bool
    message: str


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def normalize_ext(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".jpeg":
        return ".jpg"
    if ext == ".tif":
        return ".tiff"
    if ext == ".jfif":
        return ".jpg"
    if ext == ".aif":
        return ".aiff"
    return ext


def source_format_key(path: Path) -> str:
    ext = normalize_ext(path).lstrip(".")
    aliases = {"jpeg": "jpg", "tif": "tiff", "jfif": "jpg", "aif": "aiff", "heif": "heic"}
    return aliases.get(ext, ext)


def detect_kind(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return None


def suggested_formats(path: Path) -> list[str]:
    """Return output format keys valid for a given source file."""
    kind = detect_kind(path)
    if kind == "image":
        return [k for k, v in OUTPUT_FORMATS.items() if v["kind"] in ("image", "both")]
    if kind == "video":
        # Video → video containers, gif; also extract audio
        return [
            k
            for k, v in OUTPUT_FORMATS.items()
            if v["kind"] in ("video", "both", "audio")
        ]
    if kind == "audio":
        return [k for k, v in OUTPUT_FORMATS.items() if v["kind"] == "audio"]
    return list(OUTPUT_FORMATS.keys())


def common_formats(paths: list[Path]) -> list[str]:
    """Intersection of valid formats, preserving a sensible order."""
    if not paths:
        return list(OUTPUT_FORMATS.keys())
    common: set[str] | None = None
    for p in paths:
        s = set(suggested_formats(p))
        common = s if common is None else common & s
    if not common:
        return []
    order = list(OUTPUT_FORMATS.keys())
    return [k for k in order if k in common]


def unique_output_path(directory: Path, stem: str, ext: str) -> Path:
    candidate = directory / f"{stem}{ext}"
    if not candidate.exists():
        return candidate
    n = 1
    while True:
        candidate = directory / f"{stem}_{n}{ext}"
        if not candidate.exists():
            return candidate
        n += 1


def _quality_to_jpeg(quality: int) -> int:
    """Map UI quality 1–100 to JPEG encoder quality. 100 = maximum."""
    return max(1, min(100, quality))


def _quality_to_video_crf(quality: int, *, codec: str = "h264") -> int:
    """Map quality to ffmpeg CRF (lower CRF = higher quality)."""
    q = max(0, min(100, quality))
    if codec == "vp9":
        # VP9: ~15 (near lossless) … 40 (small)
        return int(round(40 - (q / 100) * 25))
    # H.264: 14 (visually lossless) … 32
    return int(round(32 - (q / 100) * 18))


def _quality_to_audio_bitrate(quality: int) -> str:
    """Map quality to audio bitrate string."""
    q = max(0, min(100, quality))
    # 64k … 320k
    br = int(round(64 + (q / 100) * 256))
    return f"{br}k"


def _resize_image(img: Image.Image, max_dim: int | None) -> Image.Image:
    if not max_dim or max(img.size) <= max_dim:
        return img
    copy = img.copy()
    copy.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    return copy


def _prepare_image_for_format(img: Image.Image, ext: str, quality: int) -> tuple[Image.Image, str, dict]:
    save_kwargs: dict = {}
    q = _quality_to_jpeg(quality)

    if ext in {".jpg", ".jpeg"}:
        if img.mode in ("RGBA", "LA", "P"):
            if img.mode == "P":
                img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")
        save_kwargs["quality"] = q
        save_kwargs["optimize"] = True
        save_kwargs["subsampling"] = 0 if q >= 95 else -1
        return img, "JPEG", save_kwargs

    if ext == ".png":
        if img.mode == "P":
            img = img.convert("RGBA")
        save_kwargs["optimize"] = True
        # compress_level 0–9; higher = smaller. At quality 100 use max effort.
        save_kwargs["compress_level"] = 9 if q >= 90 else 6
        return img, "PNG", save_kwargs

    if ext == ".webp":
        if q >= 100:
            save_kwargs["lossless"] = True
            save_kwargs["method"] = 6
        else:
            save_kwargs["quality"] = q
            save_kwargs["method"] = 6 if q >= 90 else 4
        return img, "WEBP", save_kwargs

    if ext == ".gif":
        if img.mode not in ("P", "L"):
            img = img.convert("P", palette=Image.Palette.ADAPTIVE)
        return img, "GIF", save_kwargs

    if ext == ".bmp":
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        return img, "BMP", save_kwargs

    if ext in {".tiff", ".tif"}:
        return img, "TIFF", save_kwargs

    if ext == ".ico":
        if max(img.size) > 256:
            img = img.copy()
            img.thumbnail((256, 256), Image.Resampling.LANCZOS)
        if img.mode not in ("RGBA", "RGB"):
            img = img.convert("RGBA")
        return img, "ICO", save_kwargs

    if ext == ".avif":
        save_kwargs["quality"] = q
        return img, "AVIF", save_kwargs

    return img, ext.lstrip(".").upper(), save_kwargs


def convert_image(
    source: Path,
    output: Path,
    quality: int = 100,
    max_dim: int | None = None,
    log: ProgressCallback | None = None,
) -> None:
    if log:
        log(f"Opening {source.name}")

    with Image.open(source) as img:
        ext = output.suffix.lower()
        if getattr(img, "is_animated", False) and ext == ".gif":
            frames = []
            durations = []
            try:
                while True:
                    frame = _resize_image(img.copy(), max_dim)
                    if frame.mode not in ("P", "L"):
                        frame = frame.convert("P", palette=Image.Palette.ADAPTIVE)
                    frames.append(frame)
                    durations.append(img.info.get("duration", 100))
                    img.seek(img.tell() + 1)
            except EOFError:
                pass
            if frames:
                frames[0].save(
                    output,
                    format="GIF",
                    save_all=True,
                    append_images=frames[1:],
                    duration=durations,
                    loop=img.info.get("loop", 0),
                    optimize=True,
                )
                if log:
                    log(f"Saved animated GIF: {output.name}")
                return

        img.load()
        img = _resize_image(img, max_dim)
        prepared, fmt, kwargs = _prepare_image_for_format(img, ext, quality)
        prepared.save(output, format=fmt, **kwargs)

    if log:
        log(f"Saved {output.name}")


def _run_ffmpeg(cmd: list[str], log: ProgressCallback | None = None) -> None:
    if not has_ffmpeg():
        raise RuntimeError(
            "ffmpeg is required. Install with: brew install ffmpeg"
        )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown ffmpeg error").strip()
        tail = "\n".join(err.splitlines()[-10:])
        raise RuntimeError(f"ffmpeg failed:\n{tail}")


def _has_audio_stream(source: Path) -> bool:
    """Return True if ffprobe finds at least one audio stream."""
    if not shutil.which("ffprobe"):
        return True  # assume yes; let ffmpeg fail if not
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip())


def _video_scale_filter(max_dim: int | None) -> str | None:
    if not max_dim:
        return None
    # Scale so the longer side ≤ max_dim, keep aspect, even dimensions
    return (
        f"scale='min({max_dim},iw)':'min({max_dim},ih)'"
        f":force_original_aspect_ratio=decrease:flags=lanczos,"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )


def convert_video(
    source: Path,
    output: Path,
    quality: int = 100,
    max_dim: int | None = None,
    log: ProgressCallback | None = None,
) -> None:
    ext = output.suffix.lower()
    if log:
        log(f"Converting {source.name} → {output.name}")

    scale = _video_scale_filter(max_dim)
    abr = _quality_to_audio_bitrate(quality)

    if ext == ".gif":
        gif_w = min(max_dim or 480, 480) if quality < 90 else min(max_dim or 640, 640)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"fps=12,scale=min(iw\\,{gif_w}):-1:flags=lanczos,split[s0][s1];"
            "[s0]palettegen=max_colors=256[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5",
            "-loop",
            "0",
            str(output),
        ]
    elif ext in {".mp4", ".mov", ".mkv", ".avi"}:
        crf = _quality_to_video_crf(quality, codec="h264")
        preset = "slow" if quality >= 95 else "medium"
        cmd = [
            "ffmpeg", "-y", "-i", str(source),
            "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
            "-c:a", "aac", "-b:a", abr,
        ]
        if scale:
            cmd.extend(["-vf", scale])
        if ext == ".mp4":
            cmd.extend(["-movflags", "+faststart"])
        if ext == ".avi":
            # replace audio for broader AVI support
            cmd = [
                "ffmpeg", "-y", "-i", str(source),
                "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
                "-c:a", "mp3", "-b:a", abr,
            ]
            if scale:
                cmd.extend(["-vf", scale])
        cmd.append(str(output))
    elif ext == ".webm":
        crf = _quality_to_video_crf(quality, codec="vp9")
        cmd = [
            "ffmpeg", "-y", "-i", str(source),
            "-c:v", "libvpx-vp9", "-crf", str(crf), "-b:v", "0",
            "-c:a", "libopus", "-b:a", abr,
        ]
        if scale:
            cmd.extend(["-vf", scale])
        cmd.append(str(output))
    elif ext in {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".opus"}:
        convert_audio(source, output, quality=quality, log=log)
        return
    else:
        raise ValueError(f"Unsupported video output: {ext}")

    _run_ffmpeg(cmd, log=log)
    if not output.exists():
        raise RuntimeError("ffmpeg finished but output file was not created")
    if log:
        size_mb = output.stat().st_size / (1024 * 1024)
        log(f"Saved {output.name} ({size_mb:.1f} MB)")


def convert_audio(
    source: Path,
    output: Path,
    quality: int = 100,
    log: ProgressCallback | None = None,
) -> None:
    ext = output.suffix.lower()
    if log:
        log(f"Converting audio {source.name} → {output.name}")

    if not _has_audio_stream(source):
        raise RuntimeError(
            f"No audio stream in {source.name} — can't extract/convert to {ext.lstrip('.')}"
        )

    bitrate = _quality_to_audio_bitrate(quality)

    if ext == ".mp3":
        cmd = [
            "ffmpeg", "-y", "-i", str(source),
            "-vn", "-c:a", "libmp3lame", "-b:a", bitrate,
            str(output),
        ]
    elif ext == ".wav":
        cmd = [
            "ffmpeg", "-y", "-i", str(source),
            "-vn", "-c:a", "pcm_s16le",
            str(output),
        ]
    elif ext == ".aac":
        cmd = [
            "ffmpeg", "-y", "-i", str(source),
            "-vn", "-c:a", "aac", "-b:a", bitrate,
            str(output),
        ]
    elif ext == ".m4a":
        cmd = [
            "ffmpeg", "-y", "-i", str(source),
            "-vn", "-c:a", "aac", "-b:a", bitrate,
            str(output),
        ]
    elif ext == ".flac":
        cmd = [
            "ffmpeg", "-y", "-i", str(source),
            "-vn", "-c:a", "flac",
            str(output),
        ]
    elif ext == ".ogg":
        cmd = [
            "ffmpeg", "-y", "-i", str(source),
            "-vn", "-c:a", "libvorbis", "-b:a", bitrate,
            str(output),
        ]
    elif ext == ".opus":
        cmd = [
            "ffmpeg", "-y", "-i", str(source),
            "-vn", "-c:a", "libopus", "-b:a", bitrate,
            str(output),
        ]
    else:
        raise ValueError(f"Unsupported audio output: {ext}")

    _run_ffmpeg(cmd, log=log)
    if not output.exists():
        raise RuntimeError("ffmpeg finished but output file was not created")
    if log:
        size_kb = output.stat().st_size / 1024
        log(f"Saved {output.name} ({size_kb:.0f} KB)")


# ── Compression ────────────────────────────────────────────────────────────

COMPRESS_PRESETS: dict[str, dict] = {
    "light": {
        "label": "Light",
        "desc": "Subtle size cut, barely visible change",
        "quality": 88,
        "max_dim": None,
        "prefer_efficient": False,
    },
    "balanced": {
        "label": "Balanced",
        "desc": "Good savings, sharp on most screens",
        "quality": 75,
        "max_dim": 1920,
        "prefer_efficient": False,
    },
    "small": {
        "label": "Small",
        "desc": "Aggressive shrink for sharing / upload",
        "quality": 60,
        "max_dim": 1280,
        "prefer_efficient": True,
    },
}


def compress_target_format(source: Path, *, prefer_efficient: bool = False) -> str:
    """Choose best output format when compressing a file."""
    kind = detect_kind(source)
    src = source_format_key(source)

    if kind == "image":
        if prefer_efficient:
            # WebP wins size; JPG if already photo-like and we want max compat
            if src in {"png", "bmp", "tiff", "heic", "heif", "gif"}:
                # Keep alpha-friendly webp for png
                return "webp" if src in {"png", "gif"} else "jpg"
            if src in {"jpg", "jpeg", "webp", "avif"}:
                return "webp" if prefer_efficient else src
            return "jpg"
        # Stay same format when writable, else map to closest
        if src in OUTPUT_FORMATS and OUTPUT_FORMATS[src]["kind"] in ("image", "both"):
            # Re-encoding BMP/TIFF as PNG is still a big win without changing intent much
            if src in {"bmp", "tiff"}:
                return "png"
            if src in {"heic", "heif"}:
                return "jpg"
            return src
        return "jpg"

    if kind == "video":
        if prefer_efficient:
            return "mp4"
        if src in {"mp4", "webm", "mov", "mkv"}:
            return src if src in OUTPUT_FORMATS else "mp4"
        return "mp4"

    if kind == "audio":
        if prefer_efficient or src in {"wav", "flac", "aiff", "caf", "wma"}:
            return "mp3"
        if src in OUTPUT_FORMATS and OUTPUT_FORMATS[src]["kind"] == "audio":
            return src
        return "mp3"

    return "jpg"


def convert_file(
    source: Path,
    output_format: str,
    output_dir: Path | None = None,
    quality: int = 100,
    max_dim: int | None = None,
    log: ProgressCallback | None = None,
    stem_suffix: str | None = None,
) -> ConversionResult:
    source = source.expanduser().resolve()
    if not source.is_file():
        return ConversionResult(source, None, False, f"Not a file: {source}")

    fmt = output_format.lower().lstrip(".")
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in OUTPUT_FORMATS:
        return ConversionResult(source, None, False, f"Unknown format: {fmt}")

    kind = detect_kind(source)
    if kind is None:
        return ConversionResult(
            source,
            None,
            False,
            f"Unsupported source type: {source.suffix or '(no extension)'}",
        )

    target_kind = OUTPUT_FORMATS[fmt]["kind"]
    # Validation matrix
    if target_kind == "image" and kind != "image":
        return ConversionResult(
            source, None, False, f"Cannot convert {kind} → {fmt}"
        )
    if target_kind == "video" and kind != "video":
        return ConversionResult(
            source, None, False, f"Cannot convert {kind} → {fmt}"
        )
    if target_kind == "audio" and kind not in ("audio", "video"):
        return ConversionResult(
            source, None, False, f"Cannot convert {kind} → {fmt}"
        )
    if target_kind == "both":
        # gif: image or video only
        if kind not in ("image", "video"):
            return ConversionResult(
                source, None, False, f"Cannot convert {kind} → {fmt}"
            )

    if kind == "image" and source.suffix.lower() in {".heic", ".heif"} and not HEIF_SUPPORTED:
        return ConversionResult(
            source,
            None,
            False,
            "HEIC support missing. Install: pip install pillow-heif",
        )

    if kind in ("video", "audio") or (kind == "video" and target_kind == "audio"):
        if not has_ffmpeg() and kind != "image":
            return ConversionResult(
                source, None, False, "ffmpeg required (brew install ffmpeg)"
            )

    out_dir = (output_dir or source.parent).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = OUTPUT_FORMATS[fmt]["ext"]
    stem = source.stem
    if stem_suffix:
        stem = f"{stem}{stem_suffix}"
    elif normalize_ext(source) == ext:
        stem = f"{stem}_converted"
    output = unique_output_path(out_dir, stem, ext)

    try:
        if kind == "image":
            convert_image(
                source, output, quality=quality, max_dim=max_dim, log=log
            )
        elif kind == "video":
            convert_video(
                source, output, quality=quality, max_dim=max_dim, log=log
            )
        elif kind == "audio":
            convert_audio(source, output, quality=quality, log=log)
        else:
            return ConversionResult(source, None, False, f"No handler for {kind}")
        return ConversionResult(source, output, True, "OK")
    except Exception as exc:  # noqa: BLE001
        return ConversionResult(source, None, False, str(exc))


def compress_file(
    source: Path,
    preset: str = "balanced",
    output_dir: Path | None = None,
    log: ProgressCallback | None = None,
) -> ConversionResult:
    """Re-encode a file for smaller size using a named preset."""
    if preset not in COMPRESS_PRESETS:
        return ConversionResult(source, None, False, f"Unknown compress preset: {preset}")

    cfg = COMPRESS_PRESETS[preset]
    fmt = compress_target_format(source, prefer_efficient=cfg["prefer_efficient"])
    result = convert_file(
        source,
        fmt,
        output_dir=output_dir,
        quality=cfg["quality"],
        max_dim=cfg["max_dim"],
        log=log,
        stem_suffix=f"_compressed",
    )
    if result.success and result.output and source.exists():
        try:
            src_sz = source.stat().st_size
            out_sz = result.output.stat().st_size
            if out_sz >= src_sz and src_sz > 0:
                # Output larger than source — still success, but note it
                pct = (out_sz / src_sz - 1) * 100
                result.message = f"OK (result {pct:.0f}% larger — source may already be compressed)"
            else:
                saved = (1 - out_sz / src_sz) * 100 if src_sz else 0
                result.message = f"OK (−{saved:.0f}%)"
        except OSError:
            pass
    return result


def convert_many(
    sources: list[Path],
    output_format: str,
    output_dir: Path | None = None,
    quality: int = 100,
    max_dim: int | None = None,
    log: ProgressCallback | None = None,
) -> list[ConversionResult]:
    results: list[ConversionResult] = []
    total = len(sources)
    for i, src in enumerate(sources, start=1):
        if log:
            log(f"[{i}/{total}] {src.name}")
        results.append(
            convert_file(
                src,
                output_format,
                output_dir=output_dir,
                quality=quality,
                max_dim=max_dim,
                log=log,
            )
        )
    return results
