"""Fast output size estimates (no full encode).

Uses source metadata + format/quality heuristics so the UI can update
instantly when the user changes format, quality, or compress preset.
Estimates are approximate — real output varies with content complexity.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from converter.engine import (
    COMPRESS_PRESETS,
    OUTPUT_FORMATS,
    compress_target_format,
    detect_kind,
    source_format_key,
)

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False


@dataclass
class SizeEstimate:
    source_bytes: int
    estimated_bytes: int
    format: str
    confidence: str  # high | medium | low
    note: str = ""

    @property
    def delta_bytes(self) -> int:
        return self.estimated_bytes - self.source_bytes

    @property
    def ratio(self) -> float:
        if self.source_bytes <= 0:
            return 1.0
        return self.estimated_bytes / self.source_bytes

    @property
    def pct_change(self) -> float:
        """Positive = larger, negative = smaller."""
        if self.source_bytes <= 0:
            return 0.0
        return (self.estimated_bytes / self.source_bytes - 1.0) * 100.0


def format_bytes(n: int) -> str:
    n = max(0, int(n))
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def format_delta(est: SizeEstimate) -> str:
    """Human string like '↓ 2.1 MB (−64%)' or '↑ 120 KB'."""
    if est.source_bytes <= 0:
        return format_bytes(est.estimated_bytes)

    pct = est.pct_change
    out = format_bytes(est.estimated_bytes)

    if abs(pct) < 3:
        return f"≈ {out}  (similar)"

    if pct < 0:
        saved = format_bytes(est.source_bytes - est.estimated_bytes)
        return f"≈ {out}  (−{abs(pct):.0f}% · save {saved})"
    return f"≈ {out}  (+{pct:.0f}%)"


# Bits-per-pixel baselines for images at quality ~80 (approx)
_IMAGE_BPP: dict[str, float] = {
    "jpg": 1.2,
    "jpeg": 1.2,
    "webp": 0.7,
    "avif": 0.45,
    "png": 6.0,  # highly content-dependent; good for photos over-estimate, UI under
    "gif": 2.5,
    "bmp": 24.0,
    "tiff": 16.0,
    "ico": 8.0,
}

# Quality scaling for lossy image formats (quality 100 → multiplier)
def _image_quality_factor(fmt: str, quality: int) -> float:
    q = max(1, min(100, quality)) / 100.0
    if fmt in {"png", "bmp", "tiff", "gif", "ico"}:
        # lossless / palette — quality barely matters (png compress effort only)
        return 1.0 if fmt != "png" else (0.85 + 0.15 * q)
    if fmt == "webp" and q >= 1.0:
        # lossless webp roughly 2–4× larger than lossy q90
        return 3.2
    # lossy: roughly exponential-ish — q50 ≈ 0.45× of q90, q100 ≈ 1.5× of q90
    # fit: factor relative to q=0.8 baseline
    base = 0.8
    # use power curve
    return (q / base) ** 1.35


def _probe_media(path: Path) -> dict | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None


@lru_cache(maxsize=256)
def _cached_probe(path_str: str, mtime_ns: int) -> tuple | None:
    """Cache ffprobe results keyed by path+mtime."""
    data = _probe_media(Path(path_str))
    if not data:
        return None
    duration = float(data.get("format", {}).get("duration") or 0)
    width = height = 0
    has_audio = False
    has_video = False
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not has_video:
            width = int(s.get("width") or 0)
            height = int(s.get("height") or 0)
            has_video = True
        if s.get("codec_type") == "audio":
            has_audio = True
    return (duration, width, height, has_audio, has_video)


def _probe(path: Path) -> tuple[float, int, int, bool, bool] | None:
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    return _cached_probe(str(path.resolve()), mtime)


@lru_cache(maxsize=256)
def _cached_image_size(path_str: str, mtime_ns: int) -> tuple[int, int] | None:
    if not HAS_PIL:
        return None
    try:
        with Image.open(path_str) as im:
            return im.size
    except OSError:
        return None


def _image_dims(path: Path) -> tuple[int, int] | None:
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    return _cached_image_size(str(path.resolve()), mtime)


def _apply_max_dim(w: int, h: int, max_dim: int | None) -> tuple[int, int]:
    if not max_dim or max(w, h) <= max_dim:
        return w, h
    if w >= h:
        return max_dim, max(1, int(h * max_dim / w))
    return max(1, int(w * max_dim / h)), max_dim


def _lossy_q_ratio(quality: int, ref: int = 90) -> float:
    """Relative size vs a reference quality for lossy codecs."""
    q = max(1, min(100, quality)) / 100.0
    r = max(1, min(100, ref)) / 100.0
    return (q / r) ** 1.35


def estimate_image(
    path: Path,
    fmt: str,
    quality: int = 100,
    max_dim: int | None = None,
    source_bytes: int | None = None,
) -> SizeEstimate:
    src_sz = source_bytes if source_bytes is not None else path.stat().st_size
    dims = _image_dims(path)
    src_key = source_format_key(path)
    # Treat jpeg/jpg aliases as same
    src_norm = "jpg" if src_key in {"jpg", "jpeg"} else src_key
    fmt_norm = "jpg" if fmt in {"jpg", "jpeg"} else fmt

    w = h = 0
    scale = 1.0
    if dims:
        w, h = _apply_max_dim(dims[0], dims[1], max_dim)
        scale = (w * h) / (dims[0] * dims[1]) if dims[0] and dims[1] else 1.0

    # Prefer source-relative estimates for already-compressed inputs — much more
    # accurate than bits-per-pixel for real photos/screenshots.
    compressed_src = src_norm in {"jpg", "webp", "heic", "heif", "avif", "gif"}
    lossy_dst = fmt_norm in {"jpg", "webp", "avif"}

    raw: float | None = None

    if compressed_src and lossy_dst:
        # Efficiency of destination relative to a typical JPEG baseline
        efficiency = {"jpg": 1.0, "webp": 0.65, "avif": 0.5}.get(fmt_norm, 1.0)
        # HEIC is already efficient — closer to webp/avif than JPEG
        if src_norm in {"heic", "heif"}:
            efficiency = {"jpg": 1.15, "webp": 0.85, "avif": 0.7}.get(fmt_norm, 1.0)
        elif src_norm == "webp":
            efficiency = {"jpg": 1.4, "webp": 1.0, "avif": 0.8}.get(fmt_norm, 1.0)
        elif src_norm == "avif":
            efficiency = {"jpg": 1.6, "webp": 1.2, "avif": 1.0}.get(fmt_norm, 1.0)
        elif src_norm == "gif":
            efficiency = {"jpg": 0.5, "webp": 0.35, "avif": 0.3}.get(fmt_norm, 0.5)

        # WebP at quality 100 uses lossless encoding in our engine — larger
        if fmt_norm == "webp" and quality >= 100:
            raw = src_sz * (1.8 if src_norm in {"jpg", "heic", "heif"} else 1.1) * scale
        else:
            q_mult = _lossy_q_ratio(quality, ref=88)
            raw = src_sz * efficiency * q_mult * scale
            # Re-encode same lossy format at Max ≈ source (never invent growth)
            if fmt_norm == src_norm and quality >= 95 and scale >= 0.99:
                raw = src_sz * (0.92 + (quality - 95) * 0.015)
            # Lower quality than typical camera JPEG should never grow
            if fmt_norm in {"jpg", "webp", "avif"} and quality <= 90:
                raw = min(raw, src_sz * scale * efficiency)

    elif src_norm in {"bmp", "tiff"} and lossy_dst:
        # Uncompressed → lossy: huge win
        if dims:
            bpp = _IMAGE_BPP.get(fmt_norm, 1.2)
            raw = w * h * bpp * _image_quality_factor(fmt_norm, quality) / 8.0
        else:
            raw = src_sz * 0.04 * _lossy_q_ratio(quality, 85)
        raw = min(raw, src_sz * 0.15)

    elif src_norm == "png" and lossy_dst:
        # PNG → lossy. Screenshots stay larger ratio than photos, but still shrink.
        raw = src_sz * (0.4 if fmt_norm == "jpg" else 0.28 if fmt_norm == "webp" else 0.22)
        raw *= _lossy_q_ratio(quality, 85) * scale

    elif fmt_norm == "png":
        if src_norm in {"jpg", "jpeg", "heic", "heif", "webp", "avif"}:
            # Lossy → PNG almost always balloons
            raw = src_sz * 3.5 * scale
        elif src_norm in {"png", "bmp", "tiff"}:
            raw = src_sz * (0.65 if max_dim and scale < 1 else 0.85) * scale
        else:
            raw = src_sz * 1.2

    elif fmt_norm in {"bmp", "tiff"}:
        if dims:
            raw = w * h * (_IMAGE_BPP.get(fmt_norm, 16) / 8.0)
        else:
            raw = src_sz * (8.0 if fmt_norm == "bmp" else 2.0)

    elif fmt_norm == "gif":
        if dims:
            raw = w * h * _IMAGE_BPP["gif"] / 8.0
        else:
            raw = src_sz * 0.9

    elif fmt_norm == "ico":
        raw = min(src_sz, 256 * 256 * 4) * 0.5

    else:
        # Generic bpp model
        if dims:
            bpp = _IMAGE_BPP.get(fmt_norm, 2.0)
            raw = w * h * bpp * _image_quality_factor(fmt_norm, quality) / 8.0
        else:
            raw = src_sz * 0.8 * _image_quality_factor(fmt_norm, quality)

    est = max(512, int(raw if raw is not None else src_sz))
    conf = "high" if dims else "medium"
    note = f"{w}×{h}" if dims else "metadata unavailable"
    return SizeEstimate(src_sz, est, fmt, conf, note)


def estimate_video(
    path: Path,
    fmt: str,
    quality: int = 100,
    max_dim: int | None = None,
    source_bytes: int | None = None,
) -> SizeEstimate:
    src_sz = source_bytes if source_bytes is not None else path.stat().st_size
    probe = _probe(path)
    src_key = source_format_key(path)

    # GIF special case
    if fmt == "gif":
        if probe:
            duration, w, h, _, _ = probe
            w, h = _apply_max_dim(w or 640, h or 360, min(max_dim or 480, 480 if quality < 90 else 640))
            # palette GIF ~ 0.8–1.5 bpp at 12fps
            fps = 12
            est = int(duration * fps * w * h * 1.0 / 8)
            est = max(est, 8_000)
            return SizeEstimate(src_sz, est, fmt, "medium", f"{duration:.1f}s gif")
        return SizeEstimate(src_sz, max(int(src_sz * 0.4), 20_000), fmt, "low", "gif estimate")

    # Audio extract from video
    if fmt in {"mp3", "wav", "aac", "m4a", "flac", "ogg", "opus"}:
        return estimate_audio(path, fmt, quality, source_bytes=src_sz)

    if probe:
        duration, src_w, src_h, has_audio, _has_video = probe
        if not duration:
            duration = max(1.0, src_sz / 500_000)  # crude
        src_w = src_w or 1280
        src_h = src_h or 720
        w, h = _apply_max_dim(src_w, src_h, max_dim)
        scale = (w * h) / max(1, src_w * src_h)
        q = max(1, min(100, quality)) / 100.0

        # Prefer source-relative when source is a real (non-tiny) encode.
        # Synthetic / near-empty test files are too small for this prior.
        min_sane = max(50_000, int(duration * 20_000))  # ~20 KB/s floor
        use_src_prior = src_sz >= min_sane

        if use_src_prior:
            codec_eff = 0.85 if fmt == "webm" and src_key != "webm" else 1.0
            if (
                fmt != src_key
                and fmt in {"mp4", "mov", "mkv", "avi"}
                and src_key in {"webm", "mkv", "avi", "wmv", "flv"}
            ):
                codec_eff = 0.95
            # Quality curve relative to typical already-compressed source (~q85)
            q_mult = 0.45 + 0.55 * (q ** 1.2)
            if quality >= 95 and scale >= 0.99 and fmt == src_key:
                q_mult = 0.92 + (quality - 95) * 0.015
            est = int(src_sz * scale * q_mult * codec_eff)
        else:
            pixels = w * h
            bpp = (0.025 + 0.07 * q) if fmt == "webm" else (0.04 + 0.09 * q)
            video_bps = pixels * bpp * 30
            audio_bps = ((64 + q * 256) * 1000) if has_audio else 0
            est = int(duration * (video_bps + audio_bps) / 8 * 1.02)

        est = max(est, 8_000)
        conf = "high" if duration and use_src_prior else "medium"
        note = f"{w}×{h} · {duration:.0f}s"
        return SizeEstimate(src_sz, est, fmt, conf, note)

    # no probe
    q = max(1, min(100, quality)) / 100.0
    mult = 0.5 + 0.6 * q
    if fmt == "webm":
        mult *= 0.75
    if max_dim:
        mult *= 0.6
    est = max(20_000, int(src_sz * mult))
    return SizeEstimate(src_sz, est, fmt, "low", "no probe")


def estimate_audio(
    path: Path,
    fmt: str,
    quality: int = 100,
    source_bytes: int | None = None,
) -> SizeEstimate:
    src_sz = source_bytes if source_bytes is not None else path.stat().st_size
    probe = _probe(path)
    src_key = source_format_key(path)
    q = max(1, min(100, quality)) / 100.0

    duration = 0.0
    if probe:
        duration = probe[0]

    if fmt == "wav":
        # 16-bit stereo 44.1kHz ≈ 176 KB/s
        if duration:
            est = int(duration * 176400)
        else:
            est = int(src_sz * 4) if src_key != "wav" else src_sz
        return SizeEstimate(src_sz, max(est, 1000), fmt, "high" if duration else "low", "pcm")

    if fmt == "flac":
        if duration:
            est = int(duration * 90000)  # ~0.7 Mbps avg
        else:
            est = int(src_sz * 0.6) if src_key == "wav" else int(src_sz * 1.1)
        return SizeEstimate(src_sz, max(est, 1000), fmt, "medium" if duration else "low", "flac")

    # lossy
    br_kbps = 64 + q * 256  # 64–320
    if fmt == "opus":
        br_kbps *= 0.7
    if fmt == "ogg":
        br_kbps *= 0.85
    if duration:
        est = int(duration * br_kbps * 1000 / 8)
        conf = "high"
        note = f"{duration:.0f}s · {br_kbps:.0f}kbps"
    else:
        # ratio from source
        if src_key in {"wav", "flac", "aiff", "caf"}:
            est = int(src_sz * (br_kbps / 1400))  # wav ~1411kbps
        elif src_key in {"mp3", "aac", "m4a", "ogg", "opus"}:
            est = int(src_sz * (br_kbps / 192))
        else:
            est = int(src_sz * 0.1)  # video→audio extract rough
        conf = "low"
        note = f"~{br_kbps:.0f}kbps"

    # Same format + high quality ≈ source
    if fmt == src_key and quality >= 95:
        est = int(src_sz * (0.95 + (quality - 95) * 0.01))

    return SizeEstimate(src_sz, max(1000, est), fmt, conf, note)


def estimate_conversion(
    path: Path,
    fmt: str,
    quality: int = 100,
    max_dim: int | None = None,
) -> SizeEstimate | None:
    """Estimate output size for converting path → fmt."""
    if not path.is_file():
        return None
    fmt = fmt.lower().lstrip(".")
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in OUTPUT_FORMATS:
        return None

    kind = detect_kind(path)
    try:
        src_sz = path.stat().st_size
    except OSError:
        return None

    if kind == "image":
        return estimate_image(path, fmt, quality=quality, max_dim=max_dim, source_bytes=src_sz)
    if kind == "video":
        return estimate_video(path, fmt, quality=quality, max_dim=max_dim, source_bytes=src_sz)
    if kind == "audio":
        return estimate_audio(path, fmt, quality=quality, source_bytes=src_sz)
    return None


def estimate_compress(path: Path, preset: str = "balanced") -> SizeEstimate | None:
    if preset not in COMPRESS_PRESETS:
        return None
    cfg = COMPRESS_PRESETS[preset]
    fmt = compress_target_format(path, prefer_efficient=cfg["prefer_efficient"])
    return estimate_conversion(
        path,
        fmt,
        quality=cfg["quality"],
        max_dim=cfg["max_dim"],
    )


def estimate_batch(
    paths: list[Path],
    *,
    mode: str,
    fmt: str | None = None,
    quality: int = 100,
    max_dim: int | None = None,
    preset: str = "balanced",
) -> tuple[list[SizeEstimate | None], SizeEstimate | None]:
    """
    Estimate each file + a combined total.

    Returns (per_file, total_or_None).
    """
    per: list[SizeEstimate | None] = []
    for p in paths:
        if mode == "compress":
            per.append(estimate_compress(p, preset))
        elif fmt:
            per.append(estimate_conversion(p, fmt, quality=quality, max_dim=max_dim))
        else:
            per.append(None)

    valid = [e for e in per if e is not None]
    if not valid:
        return per, None
    total = SizeEstimate(
        source_bytes=sum(e.source_bytes for e in valid),
        estimated_bytes=sum(e.estimated_bytes for e in valid),
        format=fmt or preset,
        confidence="medium",
        note=f"{len(valid)} files",
    )
    return per, total
