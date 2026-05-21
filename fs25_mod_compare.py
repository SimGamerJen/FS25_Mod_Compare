#!/usr/bin/env python3
"""
FS25 Mod Compare Tool

Compares either:

1) Active mods between two savegames/careerSavegame.xml files.
2) Installed mod ZIPs/folders between two FS25 mods folders, including version differences.

Typical uses:

    # Which mods are activated in one save but not the other?
    python fs25_mod_compare.py savegames savegame1 savegame2 --presence-only

    # Which mods exist in one mods folder but not the other, and which versions differ?
    python fs25_mod_compare.py folders "C:/FS25/mods_old" "C:/FS25/mods_new"

    # Folder compare, names only
    python fs25_mod_compare.py folders mods_old mods_new --names-only

    # JSON output
    python fs25_mod_compare.py folders mods_old mods_new --json

Notes:
- Savegame comparison reads <mod .../> entries from careerSavegame.xml.
- Folder comparison treats the ZIP/folder stem as the modName, e.g. FS25_MyMod.zip -> FS25_MyMod.
- Folder comparison reads modDesc.xml from inside ZIP mods and loose folder mods to extract version/title/author.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


# -----------------------------------------------------------------------------
# Shared models
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class SaveModEntry:
    mod_name: str
    title: str = ""
    version: str = ""
    required: str = ""
    file_hash: str = ""

    @property
    def key(self) -> str:
        return self.mod_name.lower()


@dataclass(frozen=True)
class FolderModEntry:
    mod_name: str
    path: str
    source_type: str  # "zip" or "folder"
    title: str = ""
    version: str = ""
    author: str = ""
    desc_version: str = ""
    multiplayer_supported: str = ""
    mod_desc_found: bool = False
    read_error: str = ""

    @property
    def key(self) -> str:
        return self.mod_name.lower()


# -----------------------------------------------------------------------------
# XML helpers
# -----------------------------------------------------------------------------

def strip_xml_namespace(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def child_text(root: ET.Element, child_name: str) -> str:
    for child in list(root):
        if strip_xml_namespace(child.tag) == child_name:
            return (child.text or "").strip()
    return ""


def first_title_text(root: ET.Element) -> str:
    """Extract a useful title from modDesc.xml, tolerating localized title blocks."""
    for child in list(root):
        if strip_xml_namespace(child.tag) != "title":
            continue

        # Common simple form: <title>My Mod</title>
        if child.text and child.text.strip():
            return child.text.strip()

        # Common localized form: <title><en>My Mod</en><de>...</de></title>
        preferred = ["en", "en_us", "en_gb"]
        localized: Dict[str, str] = {}
        for sub in list(child):
            name = strip_xml_namespace(sub.tag).lower()
            value = (sub.text or "").strip()
            if value:
                localized[name] = value

        for lang in preferred:
            if lang in localized:
                return localized[lang]
        if localized:
            return next(iter(localized.values()))

    return ""


def parse_mod_desc_xml(xml_bytes: bytes) -> Dict[str, str]:
    root = ET.fromstring(xml_bytes)
    return {
        "title": first_title_text(root),
        "version": child_text(root, "version"),
        "author": child_text(root, "author"),
        "desc_version": (root.attrib.get("descVersion") or "").strip(),
        "multiplayer_supported": child_text(root, "multiplayer"),
    }


# -----------------------------------------------------------------------------
# Savegame comparison
# -----------------------------------------------------------------------------

def resolve_career_xml(path_arg: str) -> Path:
    path = Path(path_arg).expanduser()
    if path.is_dir():
        path = path / "careerSavegame.xml"
    if not path.exists():
        raise FileNotFoundError(f"Could not find careerSavegame.xml at: {path}")
    if path.name.lower() != "careersavegame.xml":
        raise ValueError(f"Expected a careerSavegame.xml file or savegame folder, got: {path}")
    return path


def load_savegame_mods(path_arg: str) -> Tuple[Path, Dict[str, SaveModEntry]]:
    xml_path = resolve_career_xml(path_arg)
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        raise ValueError(f"Failed to parse XML: {xml_path}\n{exc}") from exc

    mods: Dict[str, SaveModEntry] = {}
    for node in tree.getroot().findall(".//mod"):
        mod_name = (node.get("modName") or "").strip()
        if not mod_name:
            continue
        entry = SaveModEntry(
            mod_name=mod_name,
            title=(node.get("title") or "").strip(),
            version=(node.get("version") or "").strip(),
            required=(node.get("required") or "").strip(),
            file_hash=(node.get("fileHash") or "").strip(),
        )
        if entry.key in mods:
            print(f"Warning: duplicate modName in {xml_path}: {entry.mod_name}", file=sys.stderr)
        mods[entry.key] = entry
    return xml_path, mods


def save_changed_fields(left: SaveModEntry, right: SaveModEntry, ignore_hash: bool = False) -> Dict[str, Tuple[str, str]]:
    fields = ["title", "version", "required", "file_hash"]
    if ignore_hash:
        fields.remove("file_hash")
    changes: Dict[str, Tuple[str, str]] = {}
    for field in fields:
        left_value = getattr(left, field)
        right_value = getattr(right, field)
        if left_value != right_value:
            changes[field] = (left_value, right_value)
    return changes


def build_savegame_diff(left_mods: Dict[str, SaveModEntry], right_mods: Dict[str, SaveModEntry], ignore_hash: bool) -> dict:
    left_keys = set(left_mods)
    right_keys = set(right_mods)
    only_left = [left_mods[k] for k in sorted(left_keys - right_keys, key=lambda x: left_mods[x].mod_name.lower())]
    only_right = [right_mods[k] for k in sorted(right_keys - left_keys, key=lambda x: right_mods[x].mod_name.lower())]

    changed = []
    for key in sorted(left_keys & right_keys, key=lambda x: left_mods[x].mod_name.lower()):
        changes = save_changed_fields(left_mods[key], right_mods[key], ignore_hash=ignore_hash)
        if changes:
            changed.append({
                "modName": left_mods[key].mod_name,
                "left": asdict(left_mods[key]),
                "right": asdict(right_mods[key]),
                "changes": changes,
            })
    return {
        "only_left": only_left,
        "only_right": only_right,
        "changed": changed,
        "common_count": len(left_keys & right_keys),
    }


# -----------------------------------------------------------------------------
# Mods folder comparison
# -----------------------------------------------------------------------------

def natural_mod_sort_key(entry: FolderModEntry | SaveModEntry) -> str:
    return entry.mod_name.lower()


def load_zip_mod(zip_path: Path) -> FolderModEntry:
    mod_name = zip_path.stem
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            mod_desc_candidates = [name for name in zf.namelist() if name.lower().endswith("moddesc.xml")]
            # Prefer root-level modDesc.xml, but tolerate badly packed archives.
            root_level = [name for name in mod_desc_candidates if "/" not in name.replace("\\", "/").strip("/")]
            chosen = root_level[0] if root_level else (mod_desc_candidates[0] if mod_desc_candidates else None)
            if not chosen:
                return FolderModEntry(
                    mod_name=mod_name,
                    path=str(zip_path),
                    source_type="zip",
                    mod_desc_found=False,
                    read_error="modDesc.xml not found in ZIP",
                )
            data = zf.read(chosen)
            parsed = parse_mod_desc_xml(data)
            return FolderModEntry(
                mod_name=mod_name,
                path=str(zip_path),
                source_type="zip",
                title=parsed.get("title", ""),
                version=parsed.get("version", ""),
                author=parsed.get("author", ""),
                desc_version=parsed.get("desc_version", ""),
                multiplayer_supported=parsed.get("multiplayer_supported", ""),
                mod_desc_found=True,
            )
    except Exception as exc:
        return FolderModEntry(
            mod_name=mod_name,
            path=str(zip_path),
            source_type="zip",
            mod_desc_found=False,
            read_error=f"Could not read ZIP/modDesc.xml: {exc}",
        )


def load_folder_mod(folder_path: Path) -> FolderModEntry:
    mod_name = folder_path.name
    mod_desc = folder_path / "modDesc.xml"
    if not mod_desc.exists():
        # Tolerate lowercase on case-sensitive systems.
        matches = [p for p in folder_path.iterdir() if p.is_file() and p.name.lower() == "moddesc.xml"]
        mod_desc = matches[0] if matches else mod_desc

    if not mod_desc.exists():
        return FolderModEntry(
            mod_name=mod_name,
            path=str(folder_path),
            source_type="folder",
            mod_desc_found=False,
            read_error="modDesc.xml not found in folder",
        )

    try:
        parsed = parse_mod_desc_xml(mod_desc.read_bytes())
        return FolderModEntry(
            mod_name=mod_name,
            path=str(folder_path),
            source_type="folder",
            title=parsed.get("title", ""),
            version=parsed.get("version", ""),
            author=parsed.get("author", ""),
            desc_version=parsed.get("desc_version", ""),
            multiplayer_supported=parsed.get("multiplayer_supported", ""),
            mod_desc_found=True,
        )
    except Exception as exc:
        return FolderModEntry(
            mod_name=mod_name,
            path=str(folder_path),
            source_type="folder",
            mod_desc_found=False,
            read_error=f"Could not read folder modDesc.xml: {exc}",
        )


def looks_like_fs_mod_zip(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() != ".zip":
        return False
    # FS mods are normally FS25_*.zip, but not all private/dev mods follow this perfectly.
    # Accept all zip files and report modDesc errors if they are not actually mods.
    return True


def load_mod_folder(path_arg: str) -> Tuple[Path, Dict[str, FolderModEntry], List[str]]:
    folder = Path(path_arg).expanduser()
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"Mods folder not found: {folder}")

    mods: Dict[str, FolderModEntry] = {}
    warnings: List[str] = []

    for item in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if looks_like_fs_mod_zip(item):
            entry = load_zip_mod(item)
        elif item.is_dir() and (item / "modDesc.xml").exists():
            entry = load_folder_mod(item)
        elif item.is_dir():
            # Loose mod folders without modDesc.xml are likely not real mods; ignore by default.
            continue
        else:
            continue

        if entry.key in mods:
            previous = mods[entry.key]
            warnings.append(f"Duplicate modName in {folder}: {entry.mod_name} ({previous.path}) and ({entry.path})")
        mods[entry.key] = entry

    return folder, mods, warnings


def folder_changed_fields(left: FolderModEntry, right: FolderModEntry, version_only: bool = False) -> Dict[str, Tuple[str, str]]:
    fields = ["version"] if version_only else [
        "version",
        "title",
        "author",
        "desc_version",
        "multiplayer_supported",
        "source_type",
        "mod_desc_found",
        "read_error",
    ]
    changes: Dict[str, Tuple[str, str]] = {}
    for field in fields:
        left_value = str(getattr(left, field))
        right_value = str(getattr(right, field))
        if left_value != right_value:
            changes[field] = (left_value, right_value)
    return changes


def build_folder_diff(left_mods: Dict[str, FolderModEntry], right_mods: Dict[str, FolderModEntry], version_only: bool) -> dict:
    left_keys = set(left_mods)
    right_keys = set(right_mods)
    only_left = [left_mods[k] for k in sorted(left_keys - right_keys, key=lambda x: left_mods[x].mod_name.lower())]
    only_right = [right_mods[k] for k in sorted(right_keys - left_keys, key=lambda x: right_mods[x].mod_name.lower())]

    changed = []
    for key in sorted(left_keys & right_keys, key=lambda x: left_mods[x].mod_name.lower()):
        changes = folder_changed_fields(left_mods[key], right_mods[key], version_only=version_only)
        if changes:
            changed.append({
                "modName": left_mods[key].mod_name,
                "left": asdict(left_mods[key]),
                "right": asdict(right_mods[key]),
                "changes": changes,
            })

    unreadable_left = [m for m in left_mods.values() if m.read_error]
    unreadable_right = [m for m in right_mods.values() if m.read_error]

    return {
        "only_left": only_left,
        "only_right": only_right,
        "changed": changed,
        "unreadable_left": sorted(unreadable_left, key=natural_mod_sort_key),
        "unreadable_right": sorted(unreadable_right, key=natural_mod_sort_key),
        "common_count": len(left_keys & right_keys),
    }


# -----------------------------------------------------------------------------
# Output formatting
# -----------------------------------------------------------------------------

def print_section(title: str, rows: Iterable[str]) -> None:
    rows = list(rows)
    print(f"\n{title}")
    print("-" * len(title))
    if not rows:
        print("None")
        return
    for row in rows:
        print(f"- {row}")


def format_save_mod(entry: SaveModEntry, names_only: bool = False) -> str:
    if names_only:
        return entry.mod_name
    title = f" — {entry.title}" if entry.title else ""
    version = f" v{entry.version}" if entry.version else ""
    required = f" required={entry.required}" if entry.required else ""
    file_hash = f" hash={entry.file_hash}" if entry.file_hash else " hash=<blank>"
    return f"{entry.mod_name}{title}{version}{required}{file_hash}"


def format_folder_mod(entry: FolderModEntry, names_only: bool = False) -> str:
    if names_only:
        return entry.mod_name
    title = f" — {entry.title}" if entry.title else ""
    version = f" v{entry.version}" if entry.version else " v<blank>"
    author = f" author={entry.author}" if entry.author else ""
    source = f" [{entry.source_type}]"
    err = f" ERROR={entry.read_error}" if entry.read_error else ""
    return f"{entry.mod_name}{title}{version}{author}{source}{err}"


def print_change_rows(changed: List[dict]) -> None:
    rows: List[str] = []
    for item in changed:
        field_parts = []
        for field, values in item["changes"].items():
            left_value, right_value = values
            field_parts.append(f"{field}: '{left_value}' -> '{right_value}'")
        rows.append(f"{item['modName']} ({'; '.join(field_parts)})")
    print_section("Present in both, but different metadata/version", rows)


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------

def cmd_savegames(args: argparse.Namespace) -> int:
    try:
        left_path, left_mods = load_savegame_mods(args.left)
        right_path, right_mods = load_savegame_mods(args.right)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    diff = build_savegame_diff(left_mods, right_mods, ignore_hash=args.ignore_hash)

    if args.json:
        payload = {
            "mode": "savegames",
            "left": {"name": args.left_name, "path": str(left_path), "mod_count": len(left_mods)},
            "right": {"name": args.right_name, "path": str(right_path), "mod_count": len(right_mods)},
            "common_count": diff["common_count"],
            "only_left": [asdict(m) for m in diff["only_left"]],
            "only_right": [asdict(m) for m in diff["only_right"]],
        }
        if not args.presence_only:
            payload["changed"] = diff["changed"]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print("FS25 Savegame Active Mod Diff")
    print("=============================")
    print(f"{args.left_name}:  {left_path}  ({len(left_mods)} active mods)")
    print(f"{args.right_name}: {right_path}  ({len(right_mods)} active mods)")
    print(f"Active in both: {diff['common_count']}")

    print_section(f"Activated only in {args.left_name}", (format_save_mod(m, args.names_only) for m in diff["only_left"]))
    print_section(f"Activated only in {args.right_name}", (format_save_mod(m, args.names_only) for m in diff["only_right"]))

    if not args.presence_only:
        print_change_rows(diff["changed"])

    has_presence_diff = bool(diff["only_left"] or diff["only_right"])
    has_metadata_diff = bool(diff["changed"]) and not args.presence_only
    return 1 if has_presence_diff or has_metadata_diff else 0


def cmd_folders(args: argparse.Namespace) -> int:
    try:
        left_path, left_mods, left_warnings = load_mod_folder(args.left)
        right_path, right_mods, right_warnings = load_mod_folder(args.right)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    for warning in left_warnings + right_warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    diff = build_folder_diff(left_mods, right_mods, version_only=args.version_only)

    if args.json:
        payload = {
            "mode": "folders",
            "left": {"name": args.left_name, "path": str(left_path), "mod_count": len(left_mods)},
            "right": {"name": args.right_name, "path": str(right_path), "mod_count": len(right_mods)},
            "common_count": diff["common_count"],
            "only_left": [asdict(m) for m in diff["only_left"]],
            "only_right": [asdict(m) for m in diff["only_right"]],
            "changed": diff["changed"],
            "unreadable_left": [asdict(m) for m in diff["unreadable_left"]],
            "unreadable_right": [asdict(m) for m in diff["unreadable_right"]],
            "warnings": left_warnings + right_warnings,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print("FS25 Mods Folder Diff")
    print("=====================")
    print(f"{args.left_name}:  {left_path}  ({len(left_mods)} detected mods)")
    print(f"{args.right_name}: {right_path}  ({len(right_mods)} detected mods)")
    print(f"Present in both folders: {diff['common_count']}")

    print_section(f"Exists only in {args.left_name}", (format_folder_mod(m, args.names_only) for m in diff["only_left"]))
    print_section(f"Exists only in {args.right_name}", (format_folder_mod(m, args.names_only) for m in diff["only_right"]))

    print_change_rows(diff["changed"])

    if args.show_read_errors:
        print_section(f"Read/modDesc issues in {args.left_name}", (format_folder_mod(m, args.names_only) for m in diff["unreadable_left"]))
        print_section(f"Read/modDesc issues in {args.right_name}", (format_folder_mod(m, args.names_only) for m in diff["unreadable_right"]))

    has_diff = bool(diff["only_left"] or diff["only_right"] or diff["changed"])
    return 1 if has_diff else 0


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare FS25 savegame active mods or mods folder contents.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    save_parser = subparsers.add_parser("savegames", help="Compare active mods in two savegames/careerSavegame.xml files")
    save_parser.add_argument("left", help="First savegame folder or careerSavegame.xml")
    save_parser.add_argument("right", help="Second savegame folder or careerSavegame.xml")
    save_parser.add_argument("--left-name", default="LEFT", help="Display name for the first savegame")
    save_parser.add_argument("--right-name", default="RIGHT", help="Display name for the second savegame")
    save_parser.add_argument("--ignore-hash", action="store_true", help="Ignore fileHash differences when checking metadata")
    save_parser.add_argument("--presence-only", action="store_true", help="Only report mods activated in one savegame but not the other")
    save_parser.add_argument("--names-only", action="store_true", help="Only print modName values in text output")
    save_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of text")
    save_parser.set_defaults(func=cmd_savegames)

    folder_parser = subparsers.add_parser("folders", help="Compare two FS25 mods folders")
    folder_parser.add_argument("left", help="First mods folder")
    folder_parser.add_argument("right", help="Second mods folder")
    folder_parser.add_argument("--left-name", default="LEFT", help="Display name for the first folder")
    folder_parser.add_argument("--right-name", default="RIGHT", help="Display name for the second folder")
    folder_parser.add_argument("--version-only", action="store_true", help="For common mods, only report version differences")
    folder_parser.add_argument("--names-only", action="store_true", help="Only print modName values in text output")
    folder_parser.add_argument("--show-read-errors", action="store_true", help="Show ZIP/modDesc.xml read issues as separate sections")
    folder_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of text")
    folder_parser.set_defaults(func=cmd_folders)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
