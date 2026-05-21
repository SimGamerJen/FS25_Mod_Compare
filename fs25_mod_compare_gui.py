#!/usr/bin/env python3
"""
FS25 Mod Compare GUI

Desktop GUI wrapper for comparing:
  1) Active mods between two savegames/careerSavegame.xml files.
  2) Installed mod ZIPs/folders between two FS25 mods folders, including version differences.

No external dependencies required. Uses Python's standard tkinter library.

Typical use:
  python fs25_mod_compare_gui.py

Notes:
- Savegame comparison reads <mod .../> entries from careerSavegame.xml.
- Mods folder comparison scans ZIP mods and loose folders containing modDesc.xml.
- Folder comparison uses the ZIP/folder stem as the mod identity, e.g. FS25_MyMod.zip -> FS25_MyMod.
"""

from __future__ import annotations

import csv
import json
import sys
import traceback
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as exc:  # pragma: no cover
    raise SystemExit("tkinter is required for the GUI. Install a Python build that includes Tk support.") from exc


# =============================================================================
# Core comparison logic
# =============================================================================

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
    for child in list(root):
        if strip_xml_namespace(child.tag) != "title":
            continue

        if child.text and child.text.strip():
            return child.text.strip()

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


def load_zip_mod(zip_path: Path) -> FolderModEntry:
    mod_name = zip_path.stem
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            candidates = [name for name in zf.namelist() if name.lower().endswith("moddesc.xml")]
            root_level = [name for name in candidates if "/" not in name.replace("\\", "/").strip("/")]
            chosen = root_level[0] if root_level else (candidates[0] if candidates else None)
            if not chosen:
                return FolderModEntry(mod_name=mod_name, path=str(zip_path), source_type="zip", read_error="modDesc.xml not found in ZIP")
            parsed = parse_mod_desc_xml(zf.read(chosen))
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
        return FolderModEntry(mod_name=mod_name, path=str(zip_path), source_type="zip", read_error=f"Could not read ZIP/modDesc.xml: {exc}")


def load_folder_mod(folder_path: Path) -> FolderModEntry:
    mod_name = folder_path.name
    mod_desc = folder_path / "modDesc.xml"
    if not mod_desc.exists():
        matches = [p for p in folder_path.iterdir() if p.is_file() and p.name.lower() == "moddesc.xml"]
        mod_desc = matches[0] if matches else mod_desc

    if not mod_desc.exists():
        return FolderModEntry(mod_name=mod_name, path=str(folder_path), source_type="folder", read_error="modDesc.xml not found in folder")

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
        return FolderModEntry(mod_name=mod_name, path=str(folder_path), source_type="folder", read_error=f"Could not read folder modDesc.xml: {exc}")


def looks_like_fs_mod_zip(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".zip"


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
            matches = [p for p in item.iterdir() if p.is_file() and p.name.lower() == "moddesc.xml"]
            if not matches:
                continue
            entry = load_folder_mod(item)
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
        "unreadable_left": sorted(unreadable_left, key=lambda m: m.mod_name.lower()),
        "unreadable_right": sorted(unreadable_right, key=lambda m: m.mod_name.lower()),
        "common_count": len(left_keys & right_keys),
    }


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


def changed_to_text(changed: List[dict]) -> List[str]:
    rows: List[str] = []
    for item in changed:
        field_parts = []
        for field, values in item["changes"].items():
            left_value, right_value = values
            field_parts.append(f"{field}: '{left_value}' -> '{right_value}'")
        rows.append(f"{item['modName']} ({'; '.join(field_parts)})")
    return rows


# =============================================================================
# GUI
# =============================================================================

class ModCompareApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FS25 Mod Compare")
        self.geometry("1180x760")
        self.minsize(980, 620)

        self.mode = tk.StringVar(value="savegames")
        self.left_path = tk.StringVar()
        self.right_path = tk.StringVar()
        self.left_name = tk.StringVar(value="LEFT")
        self.right_name = tk.StringVar(value="RIGHT")

        self.presence_only = tk.BooleanVar(value=True)
        self.ignore_hash = tk.BooleanVar(value=True)
        self.version_only = tk.BooleanVar(value=True)
        self.names_only = tk.BooleanVar(value=False)
        self.show_read_errors = tk.BooleanVar(value=False)

        self.last_payload: dict | None = None
        self.last_rows: List[dict] = []

        self._build_ui()
        self._update_mode_ui()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        mode_frame = ttk.LabelFrame(root, text="Comparison mode", padding=10)
        mode_frame.pack(fill=tk.X)
        ttk.Radiobutton(mode_frame, text="Savegames: active mods in careerSavegame.xml", variable=self.mode, value="savegames", command=self._update_mode_ui).pack(side=tk.LEFT, padx=(0, 20))
        ttk.Radiobutton(mode_frame, text="Mod folders: installed ZIPs/folders and versions", variable=self.mode, value="folders", command=self._update_mode_ui).pack(side=tk.LEFT)

        path_frame = ttk.LabelFrame(root, text="Inputs", padding=10)
        path_frame.pack(fill=tk.X, pady=(10, 0))
        path_frame.columnconfigure(1, weight=1)
        path_frame.columnconfigure(3, weight=0)

        ttk.Label(path_frame, text="Left name:").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(path_frame, textvariable=self.left_name, width=16).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(path_frame, text="Right name:").grid(row=0, column=2, sticky="e", padx=(12, 6), pady=3)
        ttk.Entry(path_frame, textvariable=self.right_name, width=16).grid(row=0, column=3, sticky="w", pady=3)

        ttk.Label(path_frame, text="Left path:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(path_frame, textvariable=self.left_path).grid(row=1, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Button(path_frame, text="Browse...", command=lambda: self._browse("left")).grid(row=1, column=3, sticky="e", padx=(8, 0), pady=3)

        ttk.Label(path_frame, text="Right path:").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(path_frame, textvariable=self.right_path).grid(row=2, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Button(path_frame, text="Browse...", command=lambda: self._browse("right")).grid(row=2, column=3, sticky="e", padx=(8, 0), pady=3)

        options = ttk.LabelFrame(root, text="Options", padding=10)
        options.pack(fill=tk.X, pady=(10, 0))
        self.save_options_frame = ttk.Frame(options)
        self.save_options_frame.pack(fill=tk.X)
        self.presence_cb = ttk.Checkbutton(self.save_options_frame, text="Presence only: show mods activated in one save but not the other", variable=self.presence_only)
        self.presence_cb.pack(side=tk.LEFT, padx=(0, 20))
        self.ignore_hash_cb = ttk.Checkbutton(self.save_options_frame, text="Ignore fileHash differences", variable=self.ignore_hash)
        self.ignore_hash_cb.pack(side=tk.LEFT, padx=(0, 20))

        self.folder_options_frame = ttk.Frame(options)
        self.folder_options_frame.pack(fill=tk.X)
        self.version_cb = ttk.Checkbutton(self.folder_options_frame, text="Version-only metadata check", variable=self.version_only)
        self.version_cb.pack(side=tk.LEFT, padx=(0, 20))
        self.read_errors_cb = ttk.Checkbutton(self.folder_options_frame, text="Show ZIP/modDesc read issues", variable=self.show_read_errors)
        self.read_errors_cb.pack(side=tk.LEFT, padx=(0, 20))

        ttk.Checkbutton(options, text="Names only", variable=self.names_only).pack(anchor="w", pady=(6, 0))

        actions = ttk.Frame(root)
        actions.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(actions, text="Run comparison", command=self.run_comparison).pack(side=tk.LEFT)
        ttk.Button(actions, text="Copy results", command=self.copy_results).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Export text...", command=self.export_text).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Export CSV...", command=self.export_csv).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Export JSON...", command=self.export_json).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Clear", command=self.clear_results).pack(side=tk.RIGHT)

        self.summary_var = tk.StringVar(value="Ready.")
        ttk.Label(root, textvariable=self.summary_var).pack(fill=tk.X, pady=(8, 4))

        paned = ttk.PanedWindow(root, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        table_frame = ttk.Frame(paned)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table_frame, columns=("category", "mod", "left", "right", "details"), show="headings")
        self.tree.heading("category", text="Category")
        self.tree.heading("mod", text="Mod")
        self.tree.heading("left", text="Left")
        self.tree.heading("right", text="Right")
        self.tree.heading("details", text="Details")
        self.tree.column("category", width=190, minwidth=130, stretch=False)
        self.tree.column("mod", width=270, minwidth=170, stretch=True)
        self.tree.column("left", width=230, minwidth=120, stretch=True)
        self.tree.column("right", width=230, minwidth=120, stretch=True)
        self.tree.column("details", width=420, minwidth=180, stretch=True)
        yscroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        paned.add(table_frame, weight=3)

        text_frame = ttk.Frame(paned)
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.text = tk.Text(text_frame, wrap=tk.NONE, height=12)
        text_y = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.text.yview)
        text_x = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL, command=self.text.xview)
        self.text.configure(yscrollcommand=text_y.set, xscrollcommand=text_x.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        text_y.grid(row=0, column=1, sticky="ns")
        text_x.grid(row=1, column=0, sticky="ew")
        paned.add(text_frame, weight=2)

    def _update_mode_ui(self) -> None:
        if self.mode.get() == "savegames":
            self.save_options_frame.pack(fill=tk.X)
            self.folder_options_frame.forget()
            self.summary_var.set("Savegame mode: compare active <mod> entries in careerSavegame.xml.")
        else:
            self.save_options_frame.forget()
            self.folder_options_frame.pack(fill=tk.X)
            self.summary_var.set("Folder mode: compare installed ZIP/folder mods and modDesc.xml versions.")

    def _browse(self, side: str) -> None:
        target = self.left_path if side == "left" else self.right_path
        if self.mode.get() == "savegames":
            # Most convenient case is selecting the savegame folder, but allow direct XML too.
            folder = filedialog.askdirectory(title="Select savegame folder containing careerSavegame.xml")
            if folder:
                target.set(folder)
            else:
                file_path = filedialog.askopenfilename(title="Or select careerSavegame.xml", filetypes=[("careerSavegame.xml", "careerSavegame.xml"), ("XML files", "*.xml"), ("All files", "*.*")])
                if file_path:
                    target.set(file_path)
        else:
            folder = filedialog.askdirectory(title="Select FS25 mods folder")
            if folder:
                target.set(folder)

    def run_comparison(self) -> None:
        left = self.left_path.get().strip()
        right = self.right_path.get().strip()
        if not left or not right:
            messagebox.showwarning("Missing paths", "Select both left and right paths first.")
            return

        try:
            if self.mode.get() == "savegames":
                self._run_savegame_compare(left, right)
            else:
                self._run_folder_compare(left, right)
        except Exception as exc:
            traceback_text = traceback.format_exc()
            self._set_text(f"ERROR: {exc}\n\n{traceback_text}")
            self.summary_var.set(f"Error: {exc}")
            messagebox.showerror("Comparison failed", str(exc))

    def _run_savegame_compare(self, left: str, right: str) -> None:
        left_path, left_mods = load_savegame_mods(left)
        right_path, right_mods = load_savegame_mods(right)
        diff = build_savegame_diff(left_mods, right_mods, ignore_hash=self.ignore_hash.get())

        payload = {
            "mode": "savegames",
            "left": {"name": self.left_name.get(), "path": str(left_path), "mod_count": len(left_mods)},
            "right": {"name": self.right_name.get(), "path": str(right_path), "mod_count": len(right_mods)},
            "common_count": diff["common_count"],
            "only_left": [asdict(m) for m in diff["only_left"]],
            "only_right": [asdict(m) for m in diff["only_right"]],
            "changed": diff["changed"] if not self.presence_only.get() else [],
        }

        rows: List[dict] = []
        for m in diff["only_left"]:
            rows.append({"category": f"Activated only in {self.left_name.get()}", "mod": m.mod_name, "left": format_save_mod(m, self.names_only.get()), "right": "", "details": ""})
        for m in diff["only_right"]:
            rows.append({"category": f"Activated only in {self.right_name.get()}", "mod": m.mod_name, "left": "", "right": format_save_mod(m, self.names_only.get()), "details": ""})
        if not self.presence_only.get():
            for item in diff["changed"]:
                details = "; ".join(f"{field}: '{vals[0]}' -> '{vals[1]}'" for field, vals in item["changes"].items())
                rows.append({"category": "Metadata/version differs", "mod": item["modName"], "left": item["left"].get("version", ""), "right": item["right"].get("version", ""), "details": details})

        lines = [
            "FS25 Savegame Active Mod Diff",
            "=============================",
            f"{self.left_name.get()}:  {left_path}  ({len(left_mods)} active mods)",
            f"{self.right_name.get()}: {right_path}  ({len(right_mods)} active mods)",
            f"Active in both: {diff['common_count']}",
            "",
        ]
        lines.extend(self._section_text(f"Activated only in {self.left_name.get()}", [format_save_mod(m, self.names_only.get()) for m in diff["only_left"]]))
        lines.extend(self._section_text(f"Activated only in {self.right_name.get()}", [format_save_mod(m, self.names_only.get()) for m in diff["only_right"]]))
        if not self.presence_only.get():
            lines.extend(self._section_text("Present in both, but different metadata/version", changed_to_text(diff["changed"])))

        self.last_payload = payload
        self.last_rows = rows
        self._populate_tree(rows)
        self._set_text("\n".join(lines))
        self.summary_var.set(f"Done. {len(diff['only_left'])} only in left, {len(diff['only_right'])} only in right, {0 if self.presence_only.get() else len(diff['changed'])} changed.")

    def _run_folder_compare(self, left: str, right: str) -> None:
        left_path, left_mods, left_warnings = load_mod_folder(left)
        right_path, right_mods, right_warnings = load_mod_folder(right)
        diff = build_folder_diff(left_mods, right_mods, version_only=self.version_only.get())

        payload = {
            "mode": "folders",
            "left": {"name": self.left_name.get(), "path": str(left_path), "mod_count": len(left_mods)},
            "right": {"name": self.right_name.get(), "path": str(right_path), "mod_count": len(right_mods)},
            "common_count": diff["common_count"],
            "only_left": [asdict(m) for m in diff["only_left"]],
            "only_right": [asdict(m) for m in diff["only_right"]],
            "changed": diff["changed"],
            "unreadable_left": [asdict(m) for m in diff["unreadable_left"]],
            "unreadable_right": [asdict(m) for m in diff["unreadable_right"]],
            "warnings": left_warnings + right_warnings,
        }

        rows: List[dict] = []
        for m in diff["only_left"]:
            rows.append({"category": f"Exists only in {self.left_name.get()}", "mod": m.mod_name, "left": format_folder_mod(m, self.names_only.get()), "right": "", "details": ""})
        for m in diff["only_right"]:
            rows.append({"category": f"Exists only in {self.right_name.get()}", "mod": m.mod_name, "left": "", "right": format_folder_mod(m, self.names_only.get()), "details": ""})
        for item in diff["changed"]:
            details = "; ".join(f"{field}: '{vals[0]}' -> '{vals[1]}'" for field, vals in item["changes"].items())
            rows.append({"category": "Metadata/version differs", "mod": item["modName"], "left": item["left"].get("version", ""), "right": item["right"].get("version", ""), "details": details})
        if self.show_read_errors.get():
            for m in diff["unreadable_left"]:
                rows.append({"category": f"Read/modDesc issue in {self.left_name.get()}", "mod": m.mod_name, "left": m.path, "right": "", "details": m.read_error})
            for m in diff["unreadable_right"]:
                rows.append({"category": f"Read/modDesc issue in {self.right_name.get()}", "mod": m.mod_name, "left": "", "right": m.path, "details": m.read_error})

        lines = [
            "FS25 Mods Folder Diff",
            "=====================",
            f"{self.left_name.get()}:  {left_path}  ({len(left_mods)} detected mods)",
            f"{self.right_name.get()}: {right_path}  ({len(right_mods)} detected mods)",
            f"Present in both folders: {diff['common_count']}",
            "",
        ]
        if left_warnings or right_warnings:
            lines.extend(self._section_text("Warnings", left_warnings + right_warnings))
        lines.extend(self._section_text(f"Exists only in {self.left_name.get()}", [format_folder_mod(m, self.names_only.get()) for m in diff["only_left"]]))
        lines.extend(self._section_text(f"Exists only in {self.right_name.get()}", [format_folder_mod(m, self.names_only.get()) for m in diff["only_right"]]))
        lines.extend(self._section_text("Present in both, but different metadata/version", changed_to_text(diff["changed"])))
        if self.show_read_errors.get():
            lines.extend(self._section_text(f"Read/modDesc issues in {self.left_name.get()}", [format_folder_mod(m, self.names_only.get()) for m in diff["unreadable_left"]]))
            lines.extend(self._section_text(f"Read/modDesc issues in {self.right_name.get()}", [format_folder_mod(m, self.names_only.get()) for m in diff["unreadable_right"]]))

        self.last_payload = payload
        self.last_rows = rows
        self._populate_tree(rows)
        self._set_text("\n".join(lines))
        self.summary_var.set(f"Done. {len(diff['only_left'])} only in left, {len(diff['only_right'])} only in right, {len(diff['changed'])} changed.")

    @staticmethod
    def _section_text(title: str, rows: Iterable[str]) -> List[str]:
        rows = list(rows)
        output = [title, "-" * len(title)]
        if not rows:
            output.append("None")
        else:
            output.extend(f"- {row}" for row in rows)
        output.append("")
        return output

    def _populate_tree(self, rows: List[dict]) -> None:
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", tk.END, values=(row.get("category", ""), row.get("mod", ""), row.get("left", ""), row.get("right", ""), row.get("details", "")))

    def _set_text(self, value: str) -> None:
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", value)

    def clear_results(self) -> None:
        self.last_payload = None
        self.last_rows = []
        self.tree.delete(*self.tree.get_children())
        self._set_text("")
        self.summary_var.set("Results cleared.")

    def copy_results(self) -> None:
        value = self.text.get("1.0", tk.END).strip()
        if not value:
            messagebox.showinfo("Nothing to copy", "There are no text results to copy yet.")
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.summary_var.set("Results copied to clipboard.")

    def export_text(self) -> None:
        value = self.text.get("1.0", tk.END).strip()
        if not value:
            messagebox.showinfo("Nothing to export", "Run a comparison first.")
            return
        path = filedialog.asksaveasfilename(title="Export text results", defaultextension=".txt", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        Path(path).write_text(value + "\n", encoding="utf-8")
        self.summary_var.set(f"Exported text: {path}")

    def export_json(self) -> None:
        if self.last_payload is None:
            messagebox.showinfo("Nothing to export", "Run a comparison first.")
            return
        path = filedialog.asksaveasfilename(title="Export JSON results", defaultextension=".json", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not path:
            return
        Path(path).write_text(json.dumps(self.last_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.summary_var.set(f"Exported JSON: {path}")

    def export_csv(self) -> None:
        if not self.last_rows:
            messagebox.showinfo("Nothing to export", "Run a comparison first.")
            return
        path = filedialog.asksaveasfilename(title="Export CSV results", defaultextension=".csv", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        with Path(path).open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["category", "mod", "left", "right", "details"])
            writer.writeheader()
            writer.writerows(self.last_rows)
        self.summary_var.set(f"Exported CSV: {path}")


def main() -> int:
    app = ModCompareApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
