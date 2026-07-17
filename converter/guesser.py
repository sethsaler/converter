"""Intelligent output-format prediction.

Scores candidate formats from:
  1. Built-in conversion heuristics (HEIC→JPG, MP4→GIF, etc.)
  2. User history (what they actually chose for this source type)
  3. Batch context (prefer one shared format)
  4. File size / name hints
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from converter.engine import (
    OUTPUT_FORMATS,
    common_formats,
    detect_kind,
    source_format_key,
    suggested_formats,
)

HISTORY_PATH = Path.home() / ".config" / "format-converter" / "history.json"
MAX_HISTORY = 500

# src_fmt → ordered list of preferred destinations with base scores
HEURISTICS: dict[str, list[tuple[str, int, str]]] = {
    # Apple photos → universal shareable
    "heic": [
        ("jpg", 100, "HEIC photos usually become JPG for sharing"),
        ("png", 55, "Keep transparency / lossless"),
        ("webp", 40, "Smaller web-friendly image"),
    ],
    "heif": [
        ("jpg", 100, "HEIF photos usually become JPG for sharing"),
        ("png", 55, "Keep transparency / lossless"),
    ],
    # Screenshots / UI often want lossless
    "png": [
        ("jpg", 70, "Smaller file for photos / upload"),
        ("webp", 75, "Much smaller, modern web"),
        ("ico", 30, "If this is an icon"),
        ("avif", 40, "Tiny modern format"),
    ],
    "jpg": [
        ("png", 55, "Lossless / transparency"),
        ("webp", 70, "Smaller web image"),
        ("avif", 40, "Even smaller modern image"),
    ],
    "jpeg": [
        ("png", 55, "Lossless / transparency"),
        ("webp", 70, "Smaller web image"),
    ],
    "webp": [
        ("png", 75, "Universal, lossless option"),
        ("jpg", 65, "Maximum compatibility"),
    ],
    "gif": [
        ("mp4", 80, "Much smaller than animated GIF"),
        ("webm", 55, "Efficient web animation"),
        ("png", 35, "Single frame still"),
        ("jpg", 30, "Single frame photo"),
    ],
    "bmp": [
        ("png", 85, "Compress without quality loss"),
        ("jpg", 70, "Much smaller for photos"),
        ("webp", 55, "Modern compressed image"),
    ],
    "tiff": [
        ("jpg", 75, "Shareable photo from scan/print"),
        ("png", 70, "Lossless archive alternative"),
    ],
    "tif": [
        ("jpg", 75, "Shareable photo from scan/print"),
        ("png", 70, "Lossless archive alternative"),
    ],
    "ico": [
        ("png", 90, "Editable high-quality export"),
        ("jpg", 40, "Simple raster"),
    ],
    "avif": [
        ("jpg", 80, "Broad compatibility"),
        ("png", 60, "Lossless / transparency"),
        ("webp", 50, "Widely supported web format"),
    ],
    # Video
    "mp4": [
        ("gif", 85, "Short clips often become GIF"),
        ("webm", 50, "Web-optimized video"),
        ("mp3", 40, "Extract audio only"),
        ("mov", 30, "Apple-friendly container"),
    ],
    "mov": [
        ("mp4", 95, "MOV from iPhone → universal MP4"),
        ("gif", 70, "Quick animated clip"),
        ("mp3", 35, "Extract audio"),
    ],
    "mkv": [
        ("mp4", 90, "MKV → universal MP4"),
        ("webm", 45, "Web video"),
        ("mp3", 35, "Extract audio"),
    ],
    "avi": [
        ("mp4", 95, "Legacy AVI → modern MP4"),
        ("gif", 40, "Short clip to GIF"),
    ],
    "webm": [
        ("mp4", 90, "WebM → universal MP4"),
        ("gif", 50, "Animated preview"),
    ],
    "m4v": [
        ("mp4", 95, "M4V → standard MP4"),
        ("gif", 55, "Short clip to GIF"),
    ],
    "wmv": [
        ("mp4", 95, "Windows video → MP4"),
    ],
    "flv": [
        ("mp4", 95, "Flash video → MP4"),
    ],
    # Audio
    "wav": [
        ("mp3", 90, "WAV → compressed MP3"),
        ("flac", 55, "Lossless archive"),
        ("m4a", 45, "Apple-friendly compressed"),
    ],
    "flac": [
        ("mp3", 85, "Shareable compressed audio"),
        ("m4a", 50, "Apple devices"),
        ("wav", 40, "Uncompressed for editing"),
    ],
    "aiff": [
        ("mp3", 90, "AIFF → MP3"),
        ("wav", 55, "Cross-platform uncompressed"),
        ("m4a", 50, "Apple compressed"),
    ],
    "aif": [
        ("mp3", 90, "AIFF → MP3"),
        ("wav", 55, "Cross-platform uncompressed"),
    ],
    "m4a": [
        ("mp3", 80, "Broader device support"),
        ("wav", 40, "For editing"),
    ],
    "aac": [
        ("mp3", 80, "Broader device support"),
        ("m4a", 55, "Containerized AAC"),
    ],
    "ogg": [
        ("mp3", 85, "Universal audio"),
        ("wav", 40, "Uncompressed"),
    ],
    "opus": [
        ("mp3", 85, "Universal audio"),
        ("m4a", 45, "Apple devices"),
    ],
    "wma": [
        ("mp3", 95, "Windows audio → MP3"),
        ("wav", 40, "Uncompressed"),
    ],
    "mp3": [
        ("wav", 50, "For editing / DAW"),
        ("m4a", 45, "Apple devices"),
        ("flac", 35, "Lossless re-encode (limited value)"),
    ],
    "caf": [
        ("mp3", 85, "Core Audio → MP3"),
        ("wav", 60, "Uncompressed"),
        ("m4a", 55, "Apple compressed"),
    ],
}

# Kind-level defaults when no src-specific heuristic hits
KIND_DEFAULTS: dict[str, list[tuple[str, int, str]]] = {
    "image": [
        ("jpg", 50, "Most compatible image format"),
        ("png", 45, "Lossless image"),
        ("webp", 40, "Modern compressed image"),
    ],
    "video": [
        ("mp4", 70, "Most compatible video format"),
        ("gif", 55, "Animated preview"),
        ("webm", 35, "Web video"),
    ],
    "audio": [
        ("mp3", 70, "Most compatible audio format"),
        ("wav", 40, "Uncompressed audio"),
        ("m4a", 35, "Apple audio"),
    ],
}

# Filename / path keyword boosts
# When source is already the hinted format, these nudge toward a useful conversion instead.
NAME_HINTS: list[tuple[str, str, int, str]] = [
    # Screenshots that aren't PNG yet → PNG; if already PNG the same-format penalty + webp/jpg heuristics handle share size
    ("screenshot", "png", 95, "Screenshots prefer lossless PNG"),
    ("screen shot", "png", 95, "Screenshots prefer lossless PNG"),
    ("screen-shot", "png", 95, "Screenshots prefer lossless PNG"),
    ("screen_shot", "png", 95, "Screenshots prefer lossless PNG"),
    ("screenshot", "webp", 55, "Screenshot → compact WebP for sharing"),
    ("screen shot", "webp", 55, "Screenshot → compact WebP for sharing"),
    ("icon", "png", 45, "Icons export cleanly as PNG"),
    ("favicon", "ico", 60, "Favicon → ICO"),
    ("logo", "png", 40, "Logos often need transparency (PNG)"),
    ("photo", "jpg", 25, "Photos share best as JPG"),
    ("img_", "jpg", 15, "Camera-style name → JPG"),
    ("dsc", "jpg", 15, "Camera-style name → JPG"),
    ("recording", "mp3", 30, "Recordings often become MP3"),
    ("voice", "mp3", 30, "Voice notes → MP3"),
    ("podcast", "mp3", 35, "Podcasts → MP3"),
    ("meme", "gif", 40, "Meme clips → GIF"),
    ("clip", "gif", 20, "Short clips often become GIF"),
    ("reel", "mp4", 25, "Reels stay video (MP4)"),
    ("story", "mp4", 20, "Stories stay video (MP4)"),
]


@dataclass
class Guess:
    format: str
    score: float
    reason: str


class FormatGuesser:
    def __init__(self, history_path: Path = HISTORY_PATH) -> None:
        self.history_path = history_path
        self._history: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            if self.history_path.exists():
                data = json.loads(self.history_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._history = data[-MAX_HISTORY:]
        except (OSError, json.JSONDecodeError):
            self._history = []

    def _save(self) -> None:
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self.history_path.write_text(
                json.dumps(self._history[-MAX_HISTORY:], indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def record(self, source: Path, chosen_format: str) -> None:
        entry = {
            "src": source_format_key(source),
            "kind": detect_kind(source),
            "dst": chosen_format.lower().lstrip("."),
            "ts": time.time(),
        }
        self._history.append(entry)
        if len(self._history) > MAX_HISTORY:
            self._history = self._history[-MAX_HISTORY:]
        self._save()

    def history_stats(self) -> dict[str, Counter]:
        """Return Counter of dst formats keyed by src format."""
        by_src: dict[str, Counter] = {}
        for e in self._history:
            src = e.get("src") or ""
            dst = e.get("dst") or ""
            if not src or not dst:
                continue
            by_src.setdefault(src, Counter())[dst] += 1
        return by_src

    def _history_scores(self, src_key: str) -> dict[str, tuple[float, str]]:
        counts = self.history_stats().get(src_key, Counter())
        if not counts:
            # Fall back to kind-level history
            return {}
        total = sum(counts.values())
        out: dict[str, tuple[float, str]] = {}
        for dst, n in counts.most_common():
            # Up to +80 points for a clear personal preference
            score = min(80.0, (n / total) * 80 + n * 2)
            reason = f"You've chosen {OUTPUT_FORMATS.get(dst, {}).get('label', dst.upper())} for {src_key.upper()} {n}×"
            out[dst] = (score, reason)
        return out

    def _heuristic_scores(self, src_key: str, kind: str | None) -> dict[str, tuple[float, str]]:
        out: dict[str, tuple[float, str]] = {}
        for fmt, score, reason in HEURISTICS.get(src_key, []):
            out[fmt] = (float(score), reason)
        if not out and kind:
            for fmt, score, reason in KIND_DEFAULTS.get(kind, []):
                out[fmt] = (float(score), reason)
        return out

    def _name_boosts(self, path: Path) -> dict[str, tuple[float, str]]:
        name = path.name.lower()
        out: dict[str, tuple[float, str]] = {}
        for needle, fmt, score, reason in NAME_HINTS:
            if needle in name:
                prev = out.get(fmt)
                if not prev or score > prev[0]:
                    out[fmt] = (float(score), reason)
        return out

    def _size_boosts(self, path: Path, kind: str | None) -> dict[str, tuple[float, str]]:
        try:
            size = path.stat().st_size
        except OSError:
            return {}
        out: dict[str, tuple[float, str]] = {}
        mb = size / (1024 * 1024)
        if kind == "image" and mb > 5:
            out["jpg"] = (25, "Large image — JPG shrinks it for sharing")
            out["webp"] = (30, "Large image — WebP is much smaller")
        if kind == "video" and mb < 8:
            out["gif"] = (20, "Short/small video often becomes GIF")
        if kind == "video" and mb > 50:
            out["mp4"] = (15, "Large video — stick with efficient MP4")
            # discourage gif for huge files
            out["gif"] = (-30, "Large video makes a huge GIF")
        if kind == "audio" and mb > 20:
            out["mp3"] = (25, "Large audio — MP3 compresses well")
        return out

    def guess_for_file(self, path: Path, allowed: set[str] | None = None) -> list[Guess]:
        kind = detect_kind(path)
        src_key = source_format_key(path)
        valid = set(suggested_formats(path))
        if allowed is not None:
            valid &= allowed

        scores: dict[str, float] = {f: 0.0 for f in valid}
        reasons: dict[str, str] = {}

        def bump(fmt: str, score: float, reason: str, *, replace_if_higher: bool = True) -> None:
            if fmt not in scores:
                return
            scores[fmt] += score
            if replace_if_higher:
                if score > 0 and (fmt not in reasons or score >= 20):
                    # Prefer more specific / higher-signal reasons
                    if fmt not in reasons or abs(score) >= 25:
                        reasons[fmt] = reason

        for fmt, (sc, reason) in self._heuristic_scores(src_key, kind).items():
            bump(fmt, sc, reason)
        for fmt, (sc, reason) in self._history_scores(src_key).items():
            bump(fmt, sc, reason)
        for fmt, (sc, reason) in self._name_boosts(path).items():
            bump(fmt, sc, reason)
        for fmt, (sc, reason) in self._size_boosts(path, kind).items():
            bump(fmt, sc, reason)

        # Don't suggest converting to the same format unless it's a container remux
        same = src_key
        if same in scores and same not in ("gif",):  # gif re-encode sometimes useful
            scores[same] -= 50

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        guesses: list[Guess] = []
        for fmt, sc in ranked:
            if sc <= -40:
                continue
            reason = reasons.get(fmt) or OUTPUT_FORMATS.get(fmt, {}).get("desc", fmt)
            guesses.append(Guess(format=fmt, score=sc, reason=reason))
        return guesses

    def guess(self, paths: list[Path]) -> tuple[str | None, str, list[Guess]]:
        """
        Predict the best shared format for a batch.

        Returns (best_format, reason, top_alternatives).
        """
        if not paths:
            return None, "Add files to get a suggestion", []

        allowed = set(common_formats(paths))
        if not allowed:
            return None, "No shared target format for this mix", []

        # Aggregate scores across files
        agg: Counter = Counter()
        reason_votes: dict[str, Counter] = {}
        per_file_top: list[str] = []

        for p in paths:
            guesses = self.guess_for_file(p, allowed=allowed)
            if guesses:
                per_file_top.append(guesses[0].format)
            for g in guesses:
                # Weight top picks more
                weight = max(1.0, g.score)
                agg[g.format] += weight
                reason_votes.setdefault(g.format, Counter())[g.reason] += 1

        if not agg:
            # Fall back to first allowed
            best = next(iter(allowed))
            return best, OUTPUT_FORMATS[best]["desc"], [
                Guess(best, 0, OUTPUT_FORMATS[best]["desc"])
            ]

        # Consistency bonus: if most files independently pick the same top
        top_consensus = Counter(per_file_top)
        if top_consensus:
            consensus_fmt, n = top_consensus.most_common(1)[0]
            if n >= max(1, len(paths) * 0.6):
                agg[consensus_fmt] += 40

        ranked_fmts = [f for f, _ in agg.most_common() if f in allowed]
        best = ranked_fmts[0]
        best_reason = reason_votes.get(best, Counter()).most_common(1)
        reason = best_reason[0][0] if best_reason else OUTPUT_FORMATS[best]["desc"]

        # Build top list with averaged-ish scores
        alts: list[Guess] = []
        for fmt in ranked_fmts[:6]:
            r = reason_votes.get(fmt, Counter()).most_common(1)
            alts.append(
                Guess(
                    format=fmt,
                    score=float(agg[fmt]),
                    reason=r[0][0] if r else OUTPUT_FORMATS[fmt]["desc"],
                )
            )
        return best, reason, alts
