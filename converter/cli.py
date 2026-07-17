"""Command-line interface for Format Converter."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from converter.engine import (
    COMPRESS_PRESETS,
    FORMAT_LABELS,
    OUTPUT_FORMATS,
    compress_file,
    convert_file,
    detect_kind,
    has_ffmpeg,
)
from converter.guesser import FormatGuesser


def _format_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _expand_inputs(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file() and not child.name.startswith("."):
                    if detect_kind(child) is not None:
                        out.append(child)
        else:
            out.append(p)
    return out


def _print_formats() -> None:
    by_kind: dict[str, list[str]] = {"image": [], "video": [], "audio": [], "both": []}
    for key, meta in OUTPUT_FORMATS.items():
        by_kind[meta["kind"]].append(f"  {key:6}  {meta['desc']}")
    print("Output formats:")
    for kind, lines in by_kind.items():
        if not lines:
            continue
        label = "image/video" if kind == "both" else kind
        print(f"\n  [{label}]")
        print("\n".join(lines))
    print("\nCompress presets:")
    for key, cfg in COMPRESS_PRESETS.items():
        print(f"  {key:10}  {cfg['desc']}")
    if not has_ffmpeg():
        print("\nNote: ffmpeg not found — video/audio needs: brew install ffmpeg")


def _resolve_format(
    src: Path, fmt_arg: str | None, guesser: FormatGuesser
) -> tuple[str | None, str]:
    if fmt_arg:
        fmt = fmt_arg.lower().lstrip(".")
        if fmt == "jpeg":
            fmt = "jpg"
        return fmt, "manual"
    guesses = guesser.guess_for_file(src)
    if not guesses:
        return None, "could not guess format"
    best = guesses[0]
    return best.format, best.reason


def cmd_convert(args: argparse.Namespace) -> int:
    files = _expand_inputs(args.files)
    if not files:
        print("No input files.", file=sys.stderr)
        return 1

    out_dir = Path(args.output).expanduser().resolve() if args.output else None
    quality = max(1, min(100, args.quality))
    max_dim = args.max_dim
    guesser = FormatGuesser()
    ok = fail = 0

    for src in files:
        if not src.exists():
            print(f"✗  {src}: not found")
            fail += 1
            continue
        if detect_kind(src) is None:
            print(f"✗  {src.name}: unsupported type")
            fail += 1
            continue

        fmt, reason = _resolve_format(src, args.format, guesser)
        if fmt is None:
            print(f"✗  {src.name}: {reason}")
            fail += 1
            continue

        label = FORMAT_LABELS.get(fmt, fmt.upper())
        print(f"→  {src.name}  →  {label}  ({reason})")
        result = convert_file(
            src,
            fmt,
            output_dir=out_dir,
            quality=quality,
            max_dim=max_dim,
            log=(lambda m: print(f"   {m}")) if args.verbose else None,
        )
        if result.success and result.output:
            try:
                delta = (
                    f"  {_format_size(src.stat().st_size)}"
                    f" → {_format_size(result.output.stat().st_size)}"
                )
            except OSError:
                delta = ""
            print(f"✓  {result.output.name}{delta}")
            guesser.record(src, fmt)
            ok += 1
        else:
            print(f"✗  {src.name}: {result.message}")
            fail += 1

    print(f"\nDone: {ok} ok, {fail} failed")
    return 0 if fail == 0 else 1


def cmd_compress(args: argparse.Namespace) -> int:
    files = _expand_inputs(args.files)
    if not files:
        print("No input files.", file=sys.stderr)
        return 1

    preset = args.preset.lower()
    if preset not in COMPRESS_PRESETS:
        print(
            f"Unknown preset '{preset}'. Choose: {', '.join(COMPRESS_PRESETS)}",
            file=sys.stderr,
        )
        return 1

    out_dir = Path(args.output).expanduser().resolve() if args.output else None
    ok = fail = 0

    for src in files:
        if not src.exists():
            print(f"✗  {src}: not found")
            fail += 1
            continue
        if detect_kind(src) is None:
            print(f"✗  {src.name}: unsupported type")
            fail += 1
            continue

        print(f"→  {src.name}  [{preset}]")
        result = compress_file(
            src,
            preset=preset,
            output_dir=out_dir,
            log=(lambda m: print(f"   {m}")) if args.verbose else None,
        )
        if result.success and result.output:
            try:
                delta = (
                    f"  {_format_size(src.stat().st_size)}"
                    f" → {_format_size(result.output.stat().st_size)}"
                )
            except OSError:
                delta = ""
            print(f"✓  {result.output.name}{delta}  {result.message}")
            ok += 1
        else:
            print(f"✗  {src.name}: {result.message}")
            fail += 1

    print(f"\nDone: {ok} ok, {fail} failed")
    return 0 if fail == 0 else 1


def cmd_gui(_args: argparse.Namespace | None = None) -> int:
    from converter.app import main as gui_main

    gui_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="converter",
        description="Convert and compress images, video, and audio.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  converter                         open GUI
  converter photo.heic              smart-guess convert
  converter photo.heic -f jpg
  converter *.mov -f mp4 -q 85
  converter compress shot.png -p small
  converter --formats
""",
    )
    parser.add_argument(
        "--formats",
        action="store_true",
        help="list output formats and compress presets",
    )

    sub = parser.add_subparsers(dest="command")

    convert_p = sub.add_parser("convert", help="convert files to a format")
    convert_p.add_argument("files", nargs="+", help="files or directories")
    convert_p.add_argument(
        "-f",
        "--format",
        dest="format",
        default=None,
        help="output format (default: smart guess)",
    )
    convert_p.add_argument(
        "-q",
        "--quality",
        type=int,
        default=100,
        help="quality 1–100 (default: 100)",
    )
    convert_p.add_argument(
        "-o",
        "--output",
        default=None,
        help="output directory (default: next to source)",
    )
    convert_p.add_argument(
        "--max-dim",
        type=int,
        default=None,
        help="max width/height in pixels",
    )
    convert_p.add_argument("-v", "--verbose", action="store_true")
    convert_p.set_defaults(func=cmd_convert)

    compress_p = sub.add_parser("compress", help="shrink files with a preset")
    compress_p.add_argument("files", nargs="+", help="files or directories")
    compress_p.add_argument(
        "-p",
        "--preset",
        default="balanced",
        choices=list(COMPRESS_PRESETS),
        help="light | balanced | small (default: balanced)",
    )
    compress_p.add_argument("-o", "--output", default=None, help="output directory")
    compress_p.add_argument("-v", "--verbose", action="store_true")
    compress_p.set_defaults(func=cmd_compress)

    gui_p = sub.add_parser("gui", help="open the drag-and-drop GUI")
    gui_p.set_defaults(func=cmd_gui)

    return parser


def _rewrite_argv(raw: list[str]) -> list[str]:
    """Allow `converter photo.heic -f jpg` without the convert subcommand."""
    if not raw:
        return raw
    known = {"convert", "compress", "gui", "-h", "--help", "--formats"}
    if raw[0] in known:
        return raw
    return ["convert", *raw]


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    if not raw:
        return cmd_gui()

    if raw == ["--formats"] or (len(raw) == 1 and raw[0] == "--formats"):
        _print_formats()
        return 0

    parser = build_parser()
    rewritten = _rewrite_argv(raw)
    args = parser.parse_args(rewritten)

    if getattr(args, "formats", False):
        _print_formats()
        return 0

    func = getattr(args, "func", None)
    if func is None:
        return cmd_gui()
    return int(func(args))


if __name__ == "__main__":
    raise SystemExit(main())
