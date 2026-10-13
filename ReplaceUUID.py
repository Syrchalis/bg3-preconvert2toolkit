#!/usr/bin/env python3
"""Restore BG3 mod UUIDs after conversion to the Larian Toolkit.

Place this script beside the converter executable, next to the ``convert`` folder.
The script only scans direct subfolders of ``convert``.

For each converted folder named ``ModName_<generated UUID>``, it uses the UUID from
``convert/ModName/Mods/ModName/meta.lsx`` when there is exactly one unambiguous
original/converted pair. Otherwise it asks for a UUID. A lone folder without a UUID
suffix is also offered as a target, but can be skipped by pressing Enter.

Backups of edited meta.lsx files are written outside ``convert`` to a timestamped
``uuid_swap_backups`` folder beside this script.
"""

from __future__ import annotations

import re
import sys
import uuid
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
SUFFIX_RE = re.compile(rf"^(?P<base>.+)_(?P<uuid>{UUID_PATTERN})$")

# Parent folders in the converted project that contain a folder named ModName_<UUID>.
NESTED_FOLDER_PARENTS = (
    ("Editor", "Mods"),
    ("Generated", "Public"),
    ("Mods",),
    ("Projects",),
    ("Public",),
)


def normalize_uuid(value: str) -> str:
    """Validate and return a canonical, lowercase UUID string."""
    return str(uuid.UUID(value.strip().strip("{}")))


def read_text(path: Path) -> tuple[str, bool]:
    """Read UTF-8 LSX text and remember whether it had a UTF-8 BOM."""
    raw = path.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    return raw.decode("utf-8-sig"), has_bom


def encoded_text(text: str, has_bom: bool) -> bytes:
    raw = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + raw) if has_bom else raw


def direct_child(parent: Path, name: str) -> Optional[Path]:
    """Find one direct child by name, case-insensitively; return None if ambiguous/missing."""
    if not parent.is_dir():
        return None
    matches = [p for p in parent.iterdir() if p.is_dir() and p.name.casefold() == name.casefold()]
    return matches[0] if len(matches) == 1 else None


def module_info_uuid(meta_path: Path) -> str:
    """Read ModuleInfo/UUID from an original mod's meta.lsx."""
    text, _ = read_text(meta_path)
    root = ET.fromstring(text)
    modules = [node for node in root.iter("node") if node.attrib.get("id") == "ModuleInfo"]
    if len(modules) != 1:
        raise ValueError("expected exactly one node id=\"ModuleInfo\"")
    attrs = [
        attr for attr in list(modules[0])
        if attr.tag == "attribute"
        and attr.attrib.get("id") == "UUID"
        and attr.attrib.get("type") == "FixedString"
    ]
    if len(attrs) != 1:
        raise ValueError("expected exactly one ModuleInfo UUID attribute")
    value = attrs[0].attrib.get("value", "").strip()
    return normalize_uuid(value)


def original_uuid_for(convert_dir: Path, base_name: str) -> str:
    """Get the original UUID at ModName/Mods/ModName/meta.lsx."""
    source_matches = [
        p for p in convert_dir.iterdir()
        if p.is_dir() and p.name.casefold() == base_name.casefold()
    ]
    if len(source_matches) != 1:
        raise ValueError("the matching original mod folder is missing or ambiguous")
    source = source_matches[0]
    mods_dir = direct_child(source, "Mods")
    if mods_dir is None:
        raise ValueError("the original mod has no unambiguous Mods folder")
    module_dir = direct_child(mods_dir, base_name)
    if module_dir is None:
        raise ValueError(f"could not find the original Mods/{base_name} folder")
    meta_path = module_dir / "meta.lsx"
    if not meta_path.is_file():
        raise ValueError(f"original meta.lsx was not found at {meta_path}")
    return module_info_uuid(meta_path)


def set_tag_value(tag: str, value: str) -> str:
    """Change only the value= field of one XML attribute tag."""
    matches = list(re.finditer(r"\bvalue\s*=\s*([\"'])(.*?)\1", tag, flags=re.DOTALL))
    if len(matches) != 1:
        raise ValueError("an attribute tag did not contain exactly one value= field")
    match = matches[0]
    quote = match.group(1)
    escaped = escape(value, {'"': "&quot;"} if quote == '"' else {"'": "&apos;"})
    return tag[:match.start(2)] + escaped + tag[match.end(2):]


def module_info_span(text: str) -> tuple[int, int]:
    """Return the character span of the ModuleInfo node, including its closing tag."""
    matches = list(re.finditer(
        r"<node\b(?=[^>]*\bid\s*=\s*['\"]ModuleInfo['\"])[^>]*>", text, flags=re.DOTALL
    ))
    if len(matches) != 1:
        raise ValueError("expected exactly one node id=\"ModuleInfo\"")
    opening = matches[0]
    if opening.group(0).rstrip().endswith("/>"):
        raise ValueError("ModuleInfo node is unexpectedly self-closing")

    token_re = re.compile(r"</?node\b[^>]*>", flags=re.DOTALL)
    depth = 0
    for token in token_re.finditer(text, opening.start()):
        tag = token.group(0)
        if tag.startswith("</"):
            depth -= 1
            if depth == 0:
                return opening.start(), token.end()
        elif not tag.rstrip().endswith("/>"):
            depth += 1
    raise ValueError("could not find the closing tag for ModuleInfo")


def direct_attribute_spans(node_block: str, attr_id: str, attr_type: str) -> list[tuple[int, int]]:
    """Find direct-child attribute tags on a node, returning their spans in node_block."""
    token_re = re.compile(r"<node\b[^>]*>|</node\s*>|<attribute\b[^>]*>", flags=re.DOTALL)
    depth = 0
    found: list[tuple[int, int]] = []
    for match in token_re.finditer(node_block):
        tag = match.group(0)
        if tag.startswith("</node"):
            depth -= 1
        elif tag.startswith("<node"):
            if not tag.rstrip().endswith("/>"):
                depth += 1
        elif depth == 1:
            id_match = re.search(r"\bid\s*=\s*(['\"])(.*?)\1", tag, flags=re.DOTALL)
            type_match = re.search(r"\btype\s*=\s*(['\"])(.*?)\1", tag, flags=re.DOTALL)
            if id_match and type_match and id_match.group(2) == attr_id and type_match.group(2) == attr_type:
                found.append((match.start(), match.end()))
    return found


def patch_module_meta(text: str, base_name: str, new_uuid: str) -> str:
    """Update Folder and UUID directly inside ModuleInfo; preserve all other text."""
    # Parse first so malformed XML or an unexpected tree stops the operation safely.
    root = ET.fromstring(text)
    modules = [node for node in root.iter("node") if node.attrib.get("id") == "ModuleInfo"]
    if len(modules) != 1:
        raise ValueError("expected exactly one node id=\"ModuleInfo\" in converted Mods/meta.lsx")
    for field, field_type in (("Folder", "LSString"), ("UUID", "FixedString")):
        matches = [
            attr for attr in list(modules[0])
            if attr.tag == "attribute" and attr.attrib.get("id") == field and attr.attrib.get("type") == field_type
        ]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one ModuleInfo attribute id=\"{field}\" type=\"{field_type}\"")

    start, end = module_info_span(text)
    block = text[start:end]
    replacements = []
    for attr_id, attr_type, value in (
        ("Folder", "LSString", f"{base_name}_{new_uuid}"),
        ("UUID", "FixedString", new_uuid),
    ):
        spans = direct_attribute_spans(block, attr_id, attr_type)
        if len(spans) != 1:
            raise ValueError(f"could not safely locate the ModuleInfo {attr_id} attribute in the text")
        a, b = spans[0]
        replacements.append((a, b, set_tag_value(block[a:b], value)))

    for a, b, replacement in sorted(replacements, reverse=True):
        block = block[:a] + replacement + block[b:]
    updated = text[:start] + block + text[end:]
    ET.fromstring(updated)  # confirm the result remains well-formed XML
    return updated


def patch_project_meta(text: str, new_uuid: str) -> str:
    """Update only the project Module attribute; leave all UUID attributes alone."""
    root = ET.fromstring(text)
    module_attrs = [
        attr for attr in root.iter("attribute")
        if attr.attrib.get("id") == "Module" and attr.attrib.get("type") == "LSString"
    ]
    if len(module_attrs) != 1:
        raise ValueError("expected exactly one attribute id=\"Module\" type=\"LSString\" in Projects/meta.lsx")
    before_uuid_values = [
        attr.attrib.get("value") for attr in root.iter("attribute") if attr.attrib.get("id") == "UUID"
    ]

    tag_re = re.compile(
        r"<attribute\b(?=[^>]*\bid\s*=\s*['\"]Module['\"])(?=[^>]*\btype\s*=\s*['\"]LSString['\"])[^>]*>",
        flags=re.DOTALL,
    )
    matches = list(tag_re.finditer(text))
    if len(matches) != 1:
        raise ValueError("could not safely locate the Module attribute in the project meta.lsx text")
    match = matches[0]
    updated = text[:match.start()] + set_tag_value(match.group(0), new_uuid) + text[match.end():]
    updated_root = ET.fromstring(updated)
    after_uuid_values = [
        attr.attrib.get("value") for attr in updated_root.iter("attribute") if attr.attrib.get("id") == "UUID"
    ]
    if before_uuid_values != after_uuid_values:
        raise ValueError("internal safety check failed: a project UUID attribute would have changed")
    return updated


def is_relevant_inner_folder(name: str, base_name: str) -> bool:
    """Return whether a child is named ModName or ModName_<valid UUID>."""
    if name.casefold() == base_name.casefold():
        return True
    prefix = base_name + "_"
    if not name.casefold().startswith(prefix.casefold()):
        return False
    suffix = name[len(prefix):]
    return re.fullmatch(UUID_PATTERN, suffix, flags=re.IGNORECASE) is not None


def choose_inner_folder(parent: Path, base_name: str, outer_uuid: Optional[str], new_uuid: str) -> tuple[Optional[Path], bool]:
    """Return (selected child, ambiguous). None selected with ambiguous=False means missing."""
    if not parent.is_dir():
        return None, False
    candidates = [
        p for p in parent.iterdir()
        if p.is_dir() and is_relevant_inner_folder(p.name, base_name)
    ]
    if len(candidates) > 1:
        return None, True
    if not candidates:
        return None, False
    candidate = candidates[0]
    if outer_uuid is not None:
        expected_names = {f"{base_name}_{outer_uuid}".casefold(), f"{base_name}_{new_uuid}".casefold()}
        if candidate.name.casefold() not in expected_names:
            return None, True
    return candidate, False


def next_backup_dir(script_dir: Path) -> Path:
    base = script_dir / "uuid_swap_backups" / datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = base
    counter = 1
    while candidate.exists():
        candidate = base.with_name(base.name + f"_{counter}")
        counter += 1
    return candidate


def prompt_uuid(folder_name: str, reason: str, standalone_without_suffix: bool = False) -> Optional[str]:
    print(f"\nFolder: {folder_name}")
    print(f"Reason: {reason}")
    if standalone_without_suffix:
        print(f"This folder will be renamed to '{folder_name}_<UUID>' if you provide a UUID.")
        print("If this is only an original/source folder, press Enter to skip it.")
    while True:
        raw = input("Enter the target mod UUID (or press Enter to skip): ").strip()
        if not raw:
            return None
        try:
            return normalize_uuid(raw)
        except (ValueError, AttributeError):
            print("That does not look like a valid UUID. Expected format: 12345678-1234-1234-1234-123456789abc")


def backup_file(source: Path, target_root: Path, backup_root: Path) -> None:
    relative = source.relative_to(target_root)
    backup_path = backup_root / target_root.name / relative
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, backup_path)


def process_target(
    target: Path,
    base_name: str,
    outer_uuid: Optional[str],
    new_uuid: str,
    backup_root: Path,
) -> bool:
    """Validate, back up, and update one converted/standalone project."""
    desired_name = f"{base_name}_{new_uuid}"
    outer_destination = target.parent / desired_name
    if outer_destination != target and outer_destination.exists():
        print(f"  ERROR: Cannot rename '{target.name}': '{desired_name}' already exists.")
        return False

    selected: dict[tuple[str, ...], Optional[Path]] = {}
    rename_pairs: list[tuple[Path, Path]] = []
    for parent_parts in NESTED_FOLDER_PARENTS:
        parent = target.joinpath(*parent_parts)
        child, ambiguous = choose_inner_folder(parent, base_name, outer_uuid, new_uuid)
        if ambiguous:
            print(f"  ERROR: Ambiguous folder(s) under '{Path(*parent_parts)}'; leaving '{target.name}' unchanged.")
            return False
        selected[parent_parts] = child
        if child is None:
            print(f"  Note: expected inner folder not found under '{Path(*parent_parts)}'.")
            continue
        destination = child.parent / desired_name
        if child != destination and destination.exists():
            print(f"  ERROR: '{destination}' already exists; leaving '{target.name}' unchanged.")
            return False
        if child != destination:
            rename_pairs.append((child, destination))

    mods_child = selected.get(("Mods",))
    projects_child = selected.get(("Projects",))
    if mods_child is None or projects_child is None:
        print("  ERROR: The required Mods or Projects folder could not be identified; skipping this target.")
        return False

    mods_meta = mods_child / "meta.lsx"
    projects_meta = projects_child / "meta.lsx"
    if not mods_meta.is_file() or not projects_meta.is_file():
        missing = [str(p) for p in (mods_meta, projects_meta) if not p.is_file()]
        print("  ERROR: Required meta.lsx file(s) missing: " + ", ".join(missing))
        return False

    try:
        mods_text, mods_bom = read_text(mods_meta)
        projects_text, projects_bom = read_text(projects_meta)
        patched_mods = patch_module_meta(mods_text, base_name, new_uuid)
        patched_projects = patch_project_meta(projects_text, new_uuid)
        patched_mods_bytes = encoded_text(patched_mods, mods_bom)
        patched_projects_bytes = encoded_text(patched_projects, projects_bom)
    except (OSError, UnicodeError, ET.ParseError, ValueError) as exc:
        print(f"  ERROR: Could not safely update meta.lsx: {exc}")
        return False

    # All validation is complete. Save originals outside convert before changing anything.
    """try:
        backup_file(mods_meta, target, backup_root)
        backup_file(projects_meta, target, backup_root)
    except OSError as exc:
        print(f"  ERROR: Could not create backups: {exc}")
        return False"""

    original_mods_bytes = mods_meta.read_bytes()
    original_projects_bytes = projects_meta.read_bytes()
    completed_renames: list[tuple[Path, Path]] = []
    outer_renamed_to: Optional[Path] = None
    try:
        for source, destination in rename_pairs:
            source.rename(destination)
            completed_renames.append((source, destination))

        # The child folders now all use desired_name; write the two changed LSX files.
        (target / "Mods" / desired_name / "meta.lsx").write_bytes(patched_mods_bytes)
        (target / "Projects" / desired_name / "meta.lsx").write_bytes(patched_projects_bytes)

        if outer_destination != target:
            target.rename(outer_destination)
            outer_renamed_to = outer_destination
    except OSError as exc:
        print(f"  ERROR: Update failed: {exc}. Attempting to roll back this target...")
        try:
            if outer_renamed_to is not None and outer_renamed_to.exists() and not target.exists():
                outer_renamed_to.rename(target)
            for source, destination in reversed(completed_renames):
                if destination.exists() and not source.exists():
                    destination.rename(source)
            if mods_meta.parent.exists():
                mods_meta.write_bytes(original_mods_bytes)
            if projects_meta.parent.exists():
                projects_meta.write_bytes(original_projects_bytes)
        except OSError as rollback_exc:
            print(f"  WARNING: Automatic rollback was incomplete: {rollback_exc}")
        return False

    print(f"  Updated: {target.name} -> {desired_name}")
    print("  Updated Folders and meta.lsx files")
    return True


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    convert_dir = script_dir / "convert"
    if not convert_dir.is_dir():
        print(f"ERROR: Could not find the 'convert' folder next to this script:\n  {convert_dir}")
        return 1

    folders = sorted((p for p in convert_dir.iterdir() if p.is_dir()), key=lambda p: p.name.casefold())
    if not folders:
        print(f"No mod folders found in: {convert_dir}")
        return 0

    uuid_folders: dict[str, list[tuple[Path, str, str]]] = {}
    plain_folders: dict[str, list[Path]] = {}
    for folder in folders:
        match = SUFFIX_RE.fullmatch(folder.name)
        if match:
            base = match.group("base")
            outer_uuid = normalize_uuid(match.group("uuid"))
            uuid_folders.setdefault(base.casefold(), []).append((folder, base, outer_uuid))
        else:
            plain_folders.setdefault(folder.name.casefold(), []).append(folder)

    # Plain folders that correspond to any UUID-suffixed folder are source candidates,
    # not standalone targets. A one-to-one pair can be resolved automatically.
    source_paths: set[Path] = set()
    targets: list[tuple[Path, str, Optional[str], Optional[str], str, bool]] = []
    # Tuple fields: target path, base name, current outer UUID, auto UUID, reason, plain standalone
    for group in uuid_folders.values():
        for folder, base, outer_uuid in group:
            source_matches = plain_folders.get(base.casefold(), [])
            if source_matches:
                source_paths.update(source_matches)
            if len(group) == 1 and len(source_matches) == 1:
                try:
                    auto_uuid = original_uuid_for(convert_dir, base)
                    reason = f"found the matching original folder '{source_matches[0].name}'"
                except (OSError, UnicodeError, ET.ParseError, ValueError) as exc:
                    auto_uuid = None
                    reason = f"matching original folder could not provide a valid UUID ({exc})"
            elif source_matches:
                auto_uuid = None
                reason = "more than one converted folder matches this original folder; please choose a UUID"
            else:
                auto_uuid = None
                reason = "no matching original folder was found"
            targets.append((folder, base, outer_uuid, auto_uuid, reason, False))

    # Per the requested workflow, unmatched folders are also offered as standalone targets.
    # The user can press Enter to avoid touching a folder that is only an original/source mod.
    for folder in folders:
        if SUFFIX_RE.fullmatch(folder.name):
            continue
        if folder in source_paths:
            continue
        targets.append((
            folder,
            folder.name,
            None,
            None,
            "this folder has no matching UUID-suffixed converted folder",
            True,
        ))

    print("BG3 Mod UUID Replacer")
    print(f"Converter root: {script_dir}")
    print(f"Scanning:       {convert_dir}")
    print(f"Targets found:  {len(targets)}")

    backup_root = next_backup_dir(script_dir)
    successes = 0
    skipped = 0
    failures = 0

    for target, base, outer_uuid, auto_uuid, reason, standalone in targets:
        if auto_uuid:
            new_uuid = auto_uuid
            print(f"\nFolder: {target.name}")
            print(f"  Original UUID found automatically: {new_uuid}")
        else:
            new_uuid = prompt_uuid(target.name, reason, standalone_without_suffix=standalone)
            if new_uuid is None:
                print("  Skipped.")
                skipped += 1
                continue

        if process_target(target, base, outer_uuid, new_uuid, backup_root):
            successes += 1
        else:
            failures += 1

    # Keep backups even when an update failed and rollback was attempted.
    if backup_root.exists():
        print(f"\nBackups: {backup_root}")
        print("Keep these backups until you have checked the converted projects in the Toolkit.")

    print("\nFinished.")
    print(f"Updated: {successes}  |  Skipped: {skipped}  |  Failed: {failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    try:
        exit_code = main()
    except KeyboardInterrupt:
        print("\nCancelled by user.")
        exit_code = 130
    except Exception as exc:
        print(f"\nUnexpected error: {exc}")
        exit_code = 1
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
    raise SystemExit(exit_code)
