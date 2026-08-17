"""
Lossless exploratory dump of Garmin FIT files.

For every FIT file, this script writes a JSON document containing:

- Source file metadata and SHA-256 hash.
- Every decoded FIT message and field in an interpreted representation.
- Every decoded FIT message and field in a raw representation.
- Decoder errors from both passes.

The original FIT files are read-only and are never changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from garmin_fit_sdk import Decoder, Stream


GARMIN_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = GARMIN_DIR / "fit_files"
DEFAULT_OUTPUT_DIR = GARMIN_DIR / "exploration"


def json_safe(value: Any) -> Any:
    """Convert SDK values to JSON without silently discarding information."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (datetime, date, time)):
        return value.isoformat()

    if isinstance(value, bytes):
        return {
            "__type__": "bytes",
            "hex": value.hex(),
            "length": len(value),
        }

    if isinstance(value, bytearray):
        return {
            "__type__": "bytearray",
            "hex": bytes(value).hex(),
            "length": len(value),
        }

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]

    if isinstance(value, (set, frozenset)):
        return {
            "__type__": type(value).__name__,
            "items": [json_safe(item) for item in value],
        }

    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except (TypeError, ValueError):
            pass

    return {
        "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
        "repr": repr(value),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_fit(path: Path, *, interpreted: bool) -> tuple[dict, list, list]:
    """
    Decode a FIT file and retain both grouped messages and original message order.

    The raw pass disables scaling, datetime conversion, enum conversion, heart
    rate merging, and calculated field expansion. This preserves values closest
    to those encoded in the file. The interpreted pass enables every available
    convenience conversion for analysis.
    """
    ordered_messages = []

    def capture_message(message_number, message):
        ordered_messages.append(
            {
                "global_message_number": json_safe(message_number),
                "fields": json_safe(dict(message)),
            }
        )

    decoder = Decoder(Stream.from_file(str(path)))
    messages, errors = decoder.read(
        apply_scale_and_offset=interpreted,
        convert_datetimes_to_dates=interpreted,
        convert_types_to_strings=interpreted,
        enable_crc_check=True,
        expand_sub_fields=interpreted,
        expand_components=interpreted,
        merge_heart_rates=interpreted,
        mesg_listener=capture_message,
    )

    return (
        json_safe(messages),
        json_safe(errors),
        ordered_messages,
    )


def inspect_fit(path: Path) -> dict:
    interpreted_messages, interpreted_errors, interpreted_order = decode_fit(
        path,
        interpreted=True,
    )
    raw_messages, raw_errors, raw_order = decode_fit(
        path,
        interpreted=False,
    )

    interpreted_counts = {
        message_name: len(message_list)
        for message_name, message_list in interpreted_messages.items()
    }

    return {
        "source": {
            "file_name": path.name,
            "absolute_path": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "modified_at": datetime.fromtimestamp(
                path.stat().st_mtime
            ).astimezone().isoformat(),
            "sha256": sha256_file(path),
        },
        "summary": {
            "message_types": len(interpreted_counts),
            "total_messages": sum(interpreted_counts.values()),
            "messages_by_type": interpreted_counts,
            "interpreted_decode_error_count": len(interpreted_errors),
            "raw_decode_error_count": len(raw_errors),
        },
        "interpreted": {
            "description": (
                "Scaled values, Python datetimes, readable enum names, expanded "
                "subfields/components, and merged heart-rate records."
            ),
            "errors": interpreted_errors,
            "messages_grouped_by_type": interpreted_messages,
            "messages_in_file_order": interpreted_order,
        },
        "raw": {
            "description": (
                "Unscaled numeric values without datetime/enum conversion, "
                "component expansion, subfield expansion, or heart-rate merging."
            ),
            "errors": raw_errors,
            "messages_grouped_by_type": raw_messages,
            "messages_in_file_order": raw_order,
        },
    }


def find_fit_files(input_dir: Path, recursive: bool) -> list[Path]:
    iterator = input_dir.rglob("*") if recursive else input_dir.glob("*")
    return sorted(
        path
        for path in iterator
        if path.is_file() and path.suffix.lower() == ".fit"
    )


def write_text_summary(results: list[dict], failures: list[dict], output: Path):
    message_types = Counter()
    total_messages = 0

    for result in results:
        counts = result["summary"]["messages_by_type"]
        message_types.update(counts)
        total_messages += result["summary"]["total_messages"]

    lines = [
        "GARMIN FIT EXPLORATION",
        "=" * 80,
        f"Files decoded : {len(results)}",
        f"Files failed  : {len(failures)}",
        f"Total messages: {total_messages}",
        "",
        "MESSAGE TYPES ACROSS ALL FILES",
        "-" * 80,
    ]

    if message_types:
        width = max(len(name) for name in message_types)
        lines.extend(
            f"{name:<{width}} : {count}"
            for name, count in sorted(message_types.items())
        )
    else:
        lines.append("No messages decoded.")

    if failures:
        lines.extend(["", "FAILURES", "-" * 80])
        lines.extend(
            f"{failure['file']}: {failure['error']}" for failure in failures
        )

    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Dump every available field from Garmin FIT files."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"FIT directory (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Search for FIT files recursively.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input.resolve()
    output_dir = args.output.resolve()

    if not input_dir.is_dir():
        print(f"Input directory does not exist: {input_dir}")
        return 1

    fit_files = find_fit_files(input_dir, args.recursive)
    if not fit_files:
        print(f"No FIT files found in: {input_dir}")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    failures = []

    for index, fit_path in enumerate(fit_files, start=1):
        print(f"[{index}/{len(fit_files)}] Decoding {fit_path.name}...")
        try:
            result = inspect_fit(fit_path)
            output_file = output_dir / f"{fit_path.stem}_exploration.json"
            with output_file.open("w", encoding="utf-8") as file:
                json.dump(result, file, indent=2, ensure_ascii=False)
            results.append(result)
            print(
                f"  {result['summary']['total_messages']} messages, "
                f"{result['summary']['message_types']} types -> {output_file.name}"
            )
        except Exception as error:
            failures.append(
                {
                    "file": str(fit_path),
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
            print(f"  ERROR: {type(error).__name__}: {error}")

    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "input_directory": str(input_dir),
        "output_directory": str(output_dir),
        "files_discovered": len(fit_files),
        "files_decoded": len(results),
        "files_failed": len(failures),
        "results": [
            {
                "file_name": result["source"]["file_name"],
                "sha256": result["source"]["sha256"],
                **result["summary"],
            }
            for result in results
        ],
        "failures": failures,
    }

    with (output_dir / "manifest.json").open("w", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2, ensure_ascii=False)

    write_text_summary(results, failures, output_dir / "summary.txt")
    print(f"\nExploration complete: {output_dir}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
