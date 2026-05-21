# FS25 Mod Compare

A small Python utility for comparing Farming Simulator 25 savegames and mod folders.

It is designed to help troubleshoot issues caused by different active mods, changed mod versions, or missing mod files between save copies or mod-folder backups.

## What it does

FS25 Mod Compare has two comparison modes:

| Mode | Purpose | Reads |
| --- | --- | --- |
| `savegames` | Compares which mods are activated in two savegames | `careerSavegame.xml` |
| `folders` | Compares which mod ZIPs/folders exist in two mod folders, including version metadata | FS25 `mods` folders |

This is useful when:

- A savegame has started behaving differently from an earlier copy.
- One save has a mod activated that another save does not.
- Both saves use the same mods folder, but their activated mod lists differ.
- You have two mod-folder backups and want to know what changed.
- You need to check whether a mod exists in both folders but has a different version.

## Included scripts

| Script | Description |
| --- | --- |
| `fs25_mod_compare.py` | Command-line comparison tool |
| `fs25_mod_compare_gui.py` | Desktop GUI version using Python `tkinter` |

Both scripts use only the Python standard library. No third-party packages are required.

## Requirements

- Python 3.10 or newer recommended.
- Windows, Linux, or macOS.
- For the GUI version, your Python install must include `tkinter`.

On Windows, the standard Python installer from python.org normally includes `tkinter`.

## Typical FS25 paths on Windows

Savegames and mods are usually stored under:

```text
Documents\My Games\FarmingSimulator2025\
```

Examples:

```text
Documents\My Games\FarmingSimulator2025\savegame1\careerSavegame.xml
Documents\My Games\FarmingSimulator2025\savegame2\careerSavegame.xml
Documents\My Games\FarmingSimulator2025\mods\
```

## Command-line usage

The command structure is:

```bash
python fs25_mod_compare.py <mode> <left> <right> [options]
```

Where `<mode>` is either:

```text
savegames
folders
```

The tool does not auto-detect the type of comparison. You explicitly choose the mode.

---

# Savegame comparison

Use `savegames` mode to compare the active mod list in two savegames.

The script accepts either savegame folders:

```bash
python fs25_mod_compare.py savegames "savegame1" "savegame2"
```

Or direct `careerSavegame.xml` files:

```bash
python fs25_mod_compare.py savegames "savegame1\careerSavegame.xml" "savegame2\careerSavegame.xml"
```

## Presence-only savegame diff

This is the most useful mode for troubleshooting activated mod differences:

```bash
python fs25_mod_compare.py savegames "working_save" "broken_save" --presence-only
```

Output sections:

```text
Activated only in LEFT
```

Mods activated only in the first savegame.

```text
Activated only in RIGHT
```

Mods activated only in the second savegame.

If you put the working save on the left and the broken save on the right, then:

```text
Activated only in RIGHT
```

is your main suspect list.

## Example: troubleshooting a broken settings screen

```bash
python fs25_mod_compare.py savegames ^
  "C:\Users\Jen\Documents\My Games\FarmingSimulator2025\savegame_working" ^
  "C:\Users\Jen\Documents\My Games\FarmingSimulator2025\savegame_broken" ^
  --presence-only
```

If a mod is affecting the game settings screen in the broken save but not the working save, look for it under:

```text
Activated only in RIGHT
```

## Ignore file hash differences

By default, the savegame comparison can report metadata differences such as version or file hash changes.

To ignore hash changes:

```bash
python fs25_mod_compare.py savegames "savegame1" "savegame2" --ignore-hash
```

## Names-only output

For a cleaner copy/paste list:

```bash
python fs25_mod_compare.py savegames "savegame1" "savegame2" --presence-only --names-only
```

## JSON output

```bash
python fs25_mod_compare.py savegames "savegame1" "savegame2" --presence-only --json
```

---

# Mod folder comparison

Use `folders` mode to compare the actual installed mod files in two FS25 mod folders.

This checks:

- Mods present only in the left folder.
- Mods present only in the right folder.
- Mods present in both folders but with different metadata.
- Mod version differences from `modDesc.xml`.
- Optional ZIP/modDesc read errors.

Example:

```bash
python fs25_mod_compare.py folders "mods_backup" "mods_current"
```

Or with full Windows paths:

```bash
python fs25_mod_compare.py folders ^
  "C:\Users\Jen\Documents\My Games\FarmingSimulator2025\mods_backup" ^
  "C:\Users\Jen\Documents\My Games\FarmingSimulator2025\mods"
```

## Version-only folder comparison

This focuses common mods down to version changes:

```bash
python fs25_mod_compare.py folders "mods_backup" "mods_current" --version-only
```

This is useful when both folders contain the same mod names, but one folder may contain newer or older ZIPs.

## Names-only folder output

```bash
python fs25_mod_compare.py folders "mods_backup" "mods_current" --names-only
```

## Show read errors

Some ZIPs may be badly packed, corrupted, or missing `modDesc.xml`.

To show those issues explicitly:

```bash
python fs25_mod_compare.py folders "mods_backup" "mods_current" --show-read-errors
```

## JSON folder output

```bash
python fs25_mod_compare.py folders "mods_backup" "mods_current" --json
```

---

# GUI usage

Run:

```bash
python fs25_mod_compare_gui.py
```

The GUI supports:

- Savegame comparison.
- Mod-folder comparison.
- Browse buttons for left and right paths.
- Presence-only savegame checks.
- Version-only mod folder checks.
- Names-only output.
- Table view of differences.
- Text output view.
- Copy results.
- Export results as TXT, CSV, or JSON.

<img width="2344" height="1418" alt="Screenshot 2026-05-21 125918" src="https://github.com/user-attachments/assets/d6f7a536-9c71-4ef6-818c-da63a8b056e5" />

## GUI troubleshooting workflow

For checking why one save behaves differently from another:

1. Open the GUI.
2. Select `Savegames` mode.
3. Set the left path to the last known working save.
4. Set the right path to the broken/current save.
5. Enable `Presence only`.
6. Run the comparison.
7. Focus on `Activated only in RIGHT`.

For checking whether installed mod files changed:

1. Select `Mod folders` mode.
2. Set the left path to the old/backup mods folder.
3. Set the right path to the current mods folder.
4. Enable `Version only` if you only care about version changes.
5. Run the comparison.

---

# How the tool identifies mods

## Savegame mode

Savegame mode reads active mods from:

```text
careerSavegame.xml
```

It looks for entries like:

```xml
<mod modName="FS25_ExampleMod" title="Example Mod" version="1.0.0.0" required="false" fileHash="..." />
```

The main identity field is:

```text
modName
```

## Folder mode

Folder mode scans a mods folder for:

```text
*.zip
```

and loose mod folders containing:

```text
modDesc.xml
```

The mod identity is based on the ZIP or folder name.

For example:

```text
FS25_ExampleMod.zip
```

is treated as:

```text
FS25_ExampleMod
```

The script then tries to read `modDesc.xml` to extract metadata such as:

- Title
- Version
- Author
- descVersion
- Multiplayer support

---

# Recommended troubleshooting process

## 1. Check activated mods first

If one savegame is broken and another is not, start with:

```bash
python fs25_mod_compare.py savegames "working_save" "broken_save" --presence-only
```

Look at:

```text
Activated only in RIGHT
```

Those mods are active only in the broken save.

## 2. Check mod-folder version differences

If both savegames have the same active mods, compare the actual mod folders:

```bash
python fs25_mod_compare.py folders "mods_backup" "mods_current" --version-only
```

This helps determine whether the issue came from a mod update rather than a different active mod list.

## 3. Use binary search if needed

If several suspect mods appear, disable roughly half of them in a test copy of the save and check whether the issue remains.

Repeat until the culprit is isolated.

Always test using a copied savegame, not your only working save.

---

# Output interpretation

## Savegame mode

| Section | Meaning |
| --- | --- |
| `Activated only in LEFT` | Active only in the first savegame |
| `Activated only in RIGHT` | Active only in the second savegame |
| `Present in both, but different metadata` | Same mod active in both, but title/version/hash/required metadata differs |

## Folder mode

| Section | Meaning |
| --- | --- |
| `Exists only in LEFT` | Mod file/folder exists only in the first mods folder |
| `Exists only in RIGHT` | Mod file/folder exists only in the second mods folder |
| `Present in both, but different metadata/version` | Same mod exists in both folders but metadata differs |
| `Read errors` | ZIP or `modDesc.xml` could not be read cleanly |

---

# Notes and limitations

- The tool does not modify savegames or mods.
- It only reads XML, ZIPs, and folders.
- Savegame mode compares activated mods, not whether the ZIP still exists in the mods folder.
- Folder mode compares installed mod files, not whether those mods are activated in a savegame.
- Folder mode uses ZIP/folder names as mod identities, so renamed ZIPs may appear as different mods.
- Some mods may have missing or non-standard `modDesc.xml` data.
- File hash differences in `careerSavegame.xml` may not always indicate a meaningful gameplay difference.

---

# Example commands

## Active mods only in one save

```bash
python fs25_mod_compare.py savegames "savegame1" "savegame2" --presence-only
```

## Active mods only in one save, names only

```bash
python fs25_mod_compare.py savegames "savegame1" "savegame2" --presence-only --names-only
```

## Compare two mod folders

```bash
python fs25_mod_compare.py folders "mods_old" "mods_new"
```

## Compare mod versions only

```bash
python fs25_mod_compare.py folders "mods_old" "mods_new" --version-only
```

## Export savegame comparison as JSON

```bash
python fs25_mod_compare.py savegames "savegame1" "savegame2" --presence-only --json > savegame_mod_diff.json
```

## Export folder comparison as JSON

```bash
python fs25_mod_compare.py folders "mods_old" "mods_new" --json > mod_folder_diff.json
```

---

# Safety recommendation

Before troubleshooting FS25 mods, make a copy of the savegame folder.

Do not test by repeatedly changing your only active save.

