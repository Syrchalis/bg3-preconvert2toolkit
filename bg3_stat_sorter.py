#!/usr/bin/env python3
"""
BG3 Pre-Converter
===============

Prepares legacy mods (created before Larian released the BG3 toolkit) for conversion with eclip5e's toolkit converter:
https://github.com/Eclip5eLP/bg3-convert2toolkit

Main Features:
- Reads lose .txt files or entire mod folders
- Sorts stat entries from mixed .txt files into the correct format for the converter/toolkit 
- Adds spell prefixes (Shout_, Target_, etc.) when needed
- Renames references to changed entries
- Reports issues and what was changed

Bonus Features:
In the settings.json additional features can be turned on.
- Swap a 'user prefix' with the spell prefix
Useful if your spells are named USER_Target_Spellname and want the result to be Target_USER_Spellname rather than Target_USER_Target_Spellname
- Replace mismatched spell prefixes
In case you named a spell 'Shout_' but its spelltype is "Target" it will be renamed, since the toolkit will not accept this mismatch.
- Convert status names into all uppercase, adhering to Larian naming conventions
Useful if you didn't follow Larian's naming convention and left your statuses lower-case. Converting them to upper case helps readability of your mod's code.
- Convert camelCase to underscore seperation
Only in combination with the above, if your statuses are written in camelCase they get (relatively) smartly converted to Larian's naming scheme for all uppercase names. So something like BurningHotD4Technical2 will become BURNING_HOT_D4_TECHNICAL_2 rather than BURNINGHOTD4TECHNICAL2.

Usage

Notes:
- The script will output a report - it is important to review it. It will list problems, renames, number of changes/references and more
- The script is case-sensitive and word wraps, so it will not confuse part of "Target_Fireball_Trap" with "Target_Fireball" or "BLADEWARD" with "BladeWard"
- The "icon" data line is ignored, since icons may have the same name and you may not wish to rename your images and atlas references
- Not all references are renamed, only those in commonly used files are. This is to prevent renaming in unwanted areas. These references are found and listed in the report however.

Where references are updated:
    *.txt stat entries      using lines and all data values (not Icon)
    *.lsx                   RootTemplates (not Icon attributes), Lists/SpellLists.lsx,
                            Shapeshift/Rulebook.lsx, Progressions/Progressions.lsx
    *.khn                   Scripts/thoth/helpers
    *.txt                   Mods/<mod>/Story/RawFiles/Goals
    *.xml                   Localization/<language> (incl. Mods/<mod>/Localization)

Entries that are not renamed (and whose references are not touched) but reported:
    * the same name is used by entries of different types (case-sensitive),
    * the new name already exists as another entry,
    * two entries would end up with the same new name.
"""

import codecs
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

SPELL_TYPES = [
    "Target", "Projectile", "ProjectileStrike", "Rush", "Shout",
    "Teleportation", "Throw", "Wall", "Zone",
]

STATUS_TYPES = [
    "BOOST", "DEACTIVATED", "DOWNED", "EFFECT", "FEAR", "HEAL",
    "INCAPACITATED", "INVISIBLE", "KNOCKED_DOWN", "POLYMORPHED", "SNEAKING",
]

# stat `type` value -> output file (without .txt)
TYPE_TO_FILE = {
    "Armor": "Armor",
    "BloodTypes": "BloodTypes",
    "Character": "Character",
    "CriticalHitTypes": "CriticalHitTypes",
    "Data": "Data",
    "InterruptData": "Interrupt",
    "ItemColor": "ItemColor",
    "ItemProgressionNames": "ItemProgressionNames",
    "ItemProgressionVisuals": "ItemProgressionVisuals",
    "Object": "Object",
    "PassiveData": "Passive",
    "Requirements": "Requirements",
    "Weapon": "Weapon",
    "XPData": "XPData",
}

DEFAULT_SETTINGS = {
    "userPrefix": "",
    "replaceMismatchedSpellPrefixes": False,
    "convertStatusNamesUpperCase": False,
    "convertStatusCamelCaseToUnderscores": False,
}

# data keys that hold asset names, never entry names: not rewritten
NEVER_REWRITE_KEYS = {"Icon"}

# Files whose references are rewritten: (folder run, file names or None, extensions).
# A folder run is a sequence of folder names (lower case, "*" = any one folder)
# that must appear somewhere in the path, including the mod folder itself.
REWRITE_RULES = [
    (("public", "*", "roottemplates"), None, (".lsx",)),
    (("public", "*", "lists"), ("spelllists.lsx",), (".lsx",)),
    (("public", "*", "shapeshift"), ("rulebook.lsx",), (".lsx",)),
    (("public", "*", "progressions"), ("progressions.lsx",), (".lsx",)),
    (("scripts", "thoth", "helpers"), None, (".khn",)),
    (("mods", "*", "story", "rawfiles", "goals"), None, (".txt",)),
    (("localization", "*"), None, (".xml",)),
]

BINARY_EXTS = {
    ".lsf", ".pak", ".dds", ".png", ".jpg", ".jpeg", ".gif", ".gr2", ".wem", ".wav",
    ".ogg", ".mp3", ".mp4", ".bik", ".gts", ".gtp", ".bin", ".dll", ".exe", ".zip",
    ".7z", ".rar", ".ttf", ".otf", ".psd", ".tga", ".bmp", ".webp", ".loca", ".lsbs",
    ".lsbc", ".lsfx", ".bk2", ".ico", ".fbx", ".blend",
}
MAX_SCAN_BYTES = 20 * 1024 * 1024
MAX_REPORT_HITS_PER_FILE = 25
LOOSE_NAME = "LooseFiles"

NEW_RE = re.compile(r'^\s*new\s+(\S+)\s+"([^"]*)"', re.IGNORECASE)
TYPE_RE = re.compile(r'^\s*type\s+"([^"]*)"')
USING_RE = re.compile(r'^(\s*using\s+")([^"]*)(")')
DATA_RE = re.compile(r'^\s*data\s+"([^"]*)"\s+"(.*)"\s*$')
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
DICE_RE = re.compile(r"[Dd](?:100|20|12|10|8|6|4)(?!\d)")
ICON_ATTR_RE = re.compile(r'<attribute\b[^>]*\bid="Icon"[^>]*>', re.IGNORECASE)


# ------------------------------------------------------------------ text io

def decode_raw(raw):
    """Returns (text, encoding); encoding writes the same bytes back (BOM kept)."""
    if raw[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    first = "utf-8-sig" if raw.startswith(codecs.BOM_UTF8) else "utf-8"
    for enc in (first, "cp1252", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue


def read_text(path):
    return decode_raw(path.read_bytes())[0]


def read_scannable(path):
    """Text of a readable file, None for binary or huge files."""
    if path.suffix.lower() in BINARY_EXTS:
        return None
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if raw[:2] not in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE) and b"\x00" in raw[:8192]:
        return None
    return decode_raw(raw)[0]


def load_settings(base_dir):
    settings = dict(DEFAULT_SETTINGS)
    warnings = []
    path = base_dir / "settings.json"
    if not path.exists():
        return settings, warnings
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as ex:
        sys.exit(f"settings.json is not valid JSON: {ex}")
    if not isinstance(data, dict):
        sys.exit('settings.json must contain a JSON object, e.g. {"userPrefix": ""}')
    for key, value in data.items():
        if key not in DEFAULT_SETTINGS:
            warnings.append(f'settings.json: unknown setting "{key}" ignored')
        elif not isinstance(value, type(DEFAULT_SETTINGS[key])):
            expected = "true/false" if isinstance(DEFAULT_SETTINGS[key], bool) else "text"
            sys.exit(f'settings.json: "{key}" must be {expected}')
        else:
            settings[key] = value
    return settings, warnings


# ---------------------------------------------------------- stat file parsing

class Block:
    def __init__(self, kind, name, src, line_no):
        self.kind = kind          # "entry" or "foreign"
        self.name = name
        self.new_name = name
        self.src = src            # path of the source file, relative to the mod folder
        self.line_no = line_no
        self.datadir = None       # Data folder the source file came from (None: loose file)
        self.lines = []           # entry: body lines without blanks; foreign: all lines
        self.lead = []            # comment lines that belong above the entry
        self.tail = []            # comment lines that belong below its last data line
        self.type = None
        self.using = None
        self.data = {}            # first occurrence of each data key
        self.dest = None          # output file name without .txt
        self.reason = None        # why it ended up in _Unsorted
        self.spell_type = None
        self.type_note = None     # how a missing SpellType was worked out
        self.cand = None          # wanted new name (before the clash checks)
        self.cand_notes = []
        self.mismatch = None

    def label(self):
        return f'"{self.name}" ({self.src}:{self.line_no})'

    def analyse(self):
        for line in self.lines:
            m = TYPE_RE.match(line)
            if m and self.type is None:
                self.type = m.group(1)
                continue
            m = USING_RE.match(line)
            if m and self.using is None:
                self.using = m.group(2)
                continue
            m = DATA_RE.match(line)
            if m:
                self.data.setdefault(m.group(1), m.group(2))


def is_comment(line):
    return line.strip().startswith("//")


def comment_groups(region):
    """region: lines that are only blanks and comments.
    -> [(comment lines, blank lines before it, blank lines after it)]"""
    spans = []
    pos, n = 0, len(region)
    while pos < n:
        if region[pos].strip():
            start = pos
            while pos < n and region[pos].strip():
                pos += 1
            spans.append((start, pos))
        else:
            pos += 1
    return [
        ([l.rstrip() for l in region[s:e]],
         sum(1 for l in region[:s] if not l.strip()),
         sum(1 for l in region[e:] if not l.strip()))
        for s, e in spans
    ]


def parse_file(path, label, report):
    lines = read_text(path).splitlines()
    headers = []                       # (line index, kind, name)
    in_foreign = False
    for idx, line in enumerate(lines):
        m = NEW_RE.match(line)
        if not m:
            continue
        if m.group(1).lower() == "entry":
            headers.append((idx, "entry", m.group(2)))
            in_foreign = False
        elif not in_foreign:
            headers.append((idx, "foreign", m.group(2)))
            in_foreign = True

    first = headers[0][0] if headers else len(lines)
    pending_lead = []
    for l in lines[:first]:
        if is_comment(l):
            pending_lead.append(l.rstrip())
        elif l.strip():
            report["stray"].append(f"{label}: {l.strip()[:80]}")

    blocks = []
    for k, (idx, kind, name) in enumerate(headers):
        end = headers[k + 1][0] if k + 1 < len(headers) else len(lines)
        next_kind = headers[k + 1][1] if k + 1 < len(headers) else None
        b = Block(kind, name, label, idx + 1)
        b.lead, pending_lead = pending_lead, []
        if kind == "foreign":
            seg = [l.rstrip() for l in lines[idx:end]]
            while seg and not seg[-1].strip():
                seg.pop()
            b.lines = seg
        else:
            body = lines[idx + 1:end]
            last_code = -1
            for i, l in enumerate(body):
                if l.strip() and not is_comment(l):
                    last_code = i
            b.lines = [l.rstrip() for l in body[:last_code + 1] if l.strip()]
            # comments after the last data line go to whichever entry is closer
            # (fewer blank lines in between); a tie goes to the entry after it
            for grp, before, after in comment_groups(body[last_code + 1:]):
                if next_kind == "entry" and before >= after:
                    pending_lead.extend(grp)
                else:
                    b.tail.extend(grp)
        blocks.append(b)
    return blocks


def entry_chunk(e):
    return "\n".join(e.lead + [f'new entry "{e.new_name}"'] + e.lines + e.tail) + "\n"


def foreign_chunk(b):
    return "\n".join(b.lead + b.lines) + "\n"


# ------------------------------------------------------------ type resolution

def resolve(entry, key, by_name):
    """Look up a data key on the entry, following the using chain."""
    seen = set()
    cur = entry
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if key in cur.data:
            return cur.data[key]
        cur = by_name.get(cur.using) if cur.using else None
    return None


def leading_spell_prefix(name):
    """'Target_Foo' -> 'Target', 'Foo' -> None."""
    for t in SPELL_TYPES:
        if name.startswith(t + "_"):
            return t
    return None


def resolve_spell_type(e, by_name):
    """Returns (spell_type, note). Follows using; ends at a base game parent -> its prefix."""
    if "SpellType" in e.data:
        return e.data["SpellType"], None
    seen = {id(e)}
    cur = e
    while cur.using:
        parent = by_name.get(cur.using)
        if parent is None:
            t = leading_spell_prefix(cur.using)
            if t:
                return t, f'no SpellType, taken from the prefix of parent "{cur.using}" (not in your files)'
            return None, None
        if id(parent) in seen:
            return None, None
        seen.add(id(parent))
        if "SpellType" in parent.data:
            return parent.data["SpellType"], f'no SpellType, inherited from parent "{parent.name}"'
        cur = parent
    return None, None


def classify(e, by_name):
    """Sets e.dest (and e.spell_type) or e.reason."""
    t = e.type
    if t is None:
        e.reason = "no type line"
    elif t == "SpellData":
        st, note = resolve_spell_type(e, by_name)
        if st is None:
            e.reason = "SpellData without SpellType (none found via using chain or parent prefix)"
        elif st not in SPELL_TYPES:
            e.reason = f'unknown SpellType "{st}"'
        else:
            e.dest = f"Spell_{st}"
            e.spell_type = st
            e.type_note = note
    elif t == "StatusData":
        st = resolve(e, "StatusType", by_name)
        if st is None:
            e.reason = "StatusData without StatusType (not found via using chain either)"
        elif st not in STATUS_TYPES:
            e.reason = f'unknown StatusType "{st}"'
        else:
            e.dest = f"Status_{st}"
    elif t in TYPE_TO_FILE:
        e.dest = TYPE_TO_FILE[t]
    else:
        e.reason = f'unknown type "{t}"'


# ---------------------------------------------------------------- spell names

def swap_user_prefix(name, user_prefix):
    """'USER_Shout_Foo' -> ('Shout_USER_Foo', True) if user_prefix is 'USER_'."""
    if user_prefix and name.startswith(user_prefix):
        rest = name[len(user_prefix):]
        t = leading_spell_prefix(rest)
        if t:
            return f"{t}_{user_prefix}{rest[len(t) + 1:]}", True
    return name, False


def final_spell_name(name, spell_type, user_prefix, replace_mismatch):
    """Returns (new_name, notes, mismatched_prefix_or_None)."""
    notes = []
    new, swapped = swap_user_prefix(name, user_prefix)
    if swapped:
        notes.append("user prefix swapped")
    found = leading_spell_prefix(new)
    mismatch = None
    if found is None:
        new = f"{spell_type}_{new}"
        notes.append("prefix added")
    elif found != spell_type:
        mismatch = found
        if replace_mismatch:
            new = f"{spell_type}_{new[len(found) + 1:]}"
            notes.append(f"prefix {found}_ replaced")
    return new, notes, mismatch


# --------------------------------------------------------------- status names

def split_words(run):
    """Splits a run of letters and digits into words: camelCase, acronyms,
    numbers, and the dice D4 D6 D8 D10 D12 D20 D100 (kept in one piece)."""
    words = []
    i, n = 0, len(run)
    while i < n:
        c = run[i]
        m = DICE_RE.match(run, i)
        if m and (i == 0 or run[i - 1].isdigit() or (c == "D" and run[i - 1].islower())):
            words.append(m.group(0))
            i = m.end()
            continue
        if c.isdigit():
            j = i + 1
            while j < n and run[j].isdigit():
                j += 1
        elif c.isupper():
            j = i + 1
            while j < n and run[j].isupper():
                j += 1
            if j < n and run[j].islower():
                if j - i > 1:
                    j -= 1                 # acronym: the last capital starts the next word
                else:
                    while j < n and run[j].islower():
                        j += 1
        else:
            j = i + 1
            while j < n and run[j].islower():
                j += 1
        words.append(run[i:j])
        i = j
    return words


def convert_status_name(name, underscores):
    if name == name.upper():
        return name                        # already follows the base game convention
    if not underscores:
        return name.upper()
    # odd items of the split are separators (underscores, anything else): kept as they are
    parts = re.split(r"([^0-9A-Za-z]+)", name)
    return "".join(p if i % 2 else "_".join(split_words(p)) for i, p in enumerate(parts)).upper()


# ----------------------------------------------------------- reference rewrites

def rewrite_text(text, renames, stats=None, key=None, protect_icons=False):
    """Replaces whole names (case-sensitive) according to renames."""
    if not renames:
        return text

    def sub_part(part):
        def f(m):
            new = renames.get(m.group(0))
            if new is None:
                return m.group(0)
            if stats is not None:
                stats[key] += 1
            return new
        return TOKEN_RE.sub(f, part)

    if not protect_icons:
        return sub_part(text)
    out, last = [], 0
    for m in ICON_ATTR_RE.finditer(text):
        out.append(sub_part(text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(sub_part(text[last:]))
    return "".join(out)


def find_old_names(text, old_names):
    hits = []
    if not (set(TOKEN_RE.findall(text)) & old_names):
        return hits
    for i, line in enumerate(text.splitlines(), 1):
        for t in dict.fromkeys(TOKEN_RE.findall(line)):
            if t in old_names:
                hits.append((i, t))
    return hits


# ------------------------------------------------------------------ file rules

def stat_data_dir(full_parts):
    """Stats/Generated/Data folder (relative to the mod folder) a file lies in, or None."""
    low = [p.lower() for p in full_parts]
    for i in range(len(low) - 3):          # at least the file name must follow "data"
        if low[i:i + 3] == ["stats", "generated", "data"]:
            return tuple(full_parts[1:i + 3])
    return None


def rewrite_rule(full_parts):
    low = [p.lower() for p in full_parts]
    dirs, fname = low[:-1], low[-1]
    ext = os.path.splitext(fname)[1]
    for run, names, exts in REWRITE_RULES:
        if ext not in exts or (names and fname not in names):
            continue
        for i in range(len(dirs) - len(run) + 1):
            if all(r == "*" or dirs[i + j] == r for j, r in enumerate(run)):
                return True
    return False


def known_output_names():
    names = {f"Spell_{t}.txt" for t in SPELL_TYPES}
    names |= {f"Status_{t}.txt" for t in STATUS_TYPES}
    names |= {f"{v}.txt" for v in TYPE_TO_FILE.values()}
    return names


def section(rep, title, items):
    rep.append("")
    rep.append(f"{title} ({len(items)}):")
    rep += [f"  {i}" for i in items]


# --------------------------------------------------------------------- project

def process_project(name, root, loose_files, settings, setting_warnings, out_dir):
    user_prefix = settings["userPrefix"]
    replace_mismatch = settings["replaceMismatchedSpellPrefixes"]
    status_upper = settings["convertStatusNamesUpperCase"]
    status_underscores = settings["convertStatusCamelCaseToUnderscores"]

    # ---- find the stat files ----
    stat_files = []                    # (path, label, data dir)
    consumed = set()
    if root is not None:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                if p.suffix.lower() != ".txt":
                    continue
                rel = p.relative_to(root)
                dd = stat_data_dir((root.name,) + rel.parts)
                if dd is not None:
                    stat_files.append((p, rel.as_posix(), dd))
                    consumed.add(p)
    else:
        stat_files = [(p, p.name, None) for p in loose_files]
    if not stat_files:
        return None

    report = {"stray": []}
    blocks = []
    for p, label, dd in stat_files:
        for b in parse_file(p, label, report):
            b.datadir = dd
            blocks.append(b)

    entries = [b for b in blocks if b.kind == "entry"]
    foreign = [b for b in blocks if b.kind == "foreign"]
    for e in entries:
        e.analyse()
    by_name = {}
    for e in entries:
        by_name.setdefault(e.name, e)
    for e in entries:
        classify(e, by_name)

    # ---- wanted renames ----
    for e in entries:
        if e.type == "SpellData" and e.spell_type:
            new, notes, e.mismatch = final_spell_name(e.name, e.spell_type, user_prefix, replace_mismatch)
            if new != e.name:
                e.cand, e.cand_notes = new, notes
        elif e.type == "StatusData" and status_upper:
            new = convert_status_name(e.name, status_underscores)
            if new != e.name:
                e.cand = new
                e.cand_notes = ["upper-cased, words separated" if status_underscores else "upper-cased"]

    # ---- clashes: these names are excluded from renaming and from reference rewriting ----
    entries_by_name = defaultdict(list)
    for e in entries:
        entries_by_name[e.name].append(e)
    clash_names = {n for n, es in entries_by_name.items()
                   if len({x.type or "(no type)" for x in es}) > 1}
    clashes = [
        f'"{n}": ' + ", ".join(f'{x.type or "(no type)"} ({x.src}:{x.line_no})' for x in es)
        for n, es in entries_by_name.items() if n in clash_names
    ]

    existing = set(entries_by_name)
    wanted, blocked = {}, []
    for old, es in entries_by_name.items():
        if not any(x.cand for x in es):
            continue
        shown = next(x.cand for x in es if x.cand)
        news = {x.cand or x.name for x in es}
        where = ", ".join(f"{x.src}:{x.line_no}" for x in es)
        if old in clash_names:
            blocked.append(f'"{old}" -> "{shown}": the same name is used by entries of different types (see above) [{where}]')
        elif len(news) > 1:
            blocked.append(f'"{old}": duplicate entries would get different names ({", ".join(sorted(news))}) [{where}]')
        else:
            new = news.pop()
            if new in existing:
                blocked.append(f'"{old}" -> "{new}": "{new}" already exists as another entry [{where}]')
            else:
                wanted[old] = new
    by_new = defaultdict(list)
    for old, new in wanted.items():
        by_new[new].append(old)
    renames = {}
    for new, olds in by_new.items():
        if len(olds) > 1:
            for old in olds:
                others = ", ".join(f'"{o}"' for o in olds if o != old)
                blocked.append(f'"{old}" -> "{new}": would end up with the same name as {others}')
        else:
            renames[olds[0]] = new
    for e in entries:
        e.new_name = renames.get(e.name, e.name)
    blocked_names = {e.name for e in entries if e.cand and e.name not in renames}

    mismatches = []
    for e in entries:
        if not e.mismatch:
            continue
        if e.new_name != e.name:
            action = f'now "{e.new_name}" ({", ".join(e.cand_notes)}'
            action += ")" if replace_mismatch else "; wrong prefix kept)"
        elif e.name in blocked_names:
            action = 'not renamed (see "Not renamed" above)'
        else:
            action = "left unchanged (replaceMismatchedSpellPrefixes is off)"
        mismatches.append(
            f'{e.label()}: name starts with {e.mismatch}_ but SpellType is {e.spell_type} -> {action}'
        )

    # ---- rewrite references inside the stat entries ----
    ref_counts = Counter()
    using_changes = 0
    for e in entries:
        for i, line in enumerate(e.lines):
            um = USING_RE.match(line)
            if um:
                new = renames.get(um.group(2))
                if new:
                    e.lines[i] = um.group(1) + new + line[um.end(2):]
                    using_changes += 1
                continue
            dm = DATA_RE.match(line)
            if not dm or dm.group(1) in NEVER_REWRITE_KEYS:
                continue
            value = rewrite_text(dm.group(2), renames, ref_counts, dm.group(1))
            if value != dm.group(2):
                e.lines[i] = line[:dm.start(2)] + value + line[dm.end(2):]
    for b in foreign:
        for i in range(1, len(b.lines)):       # line 0 is the block's own header
            if not is_comment(b.lines[i]):
                b.lines[i] = rewrite_text(b.lines[i], renames, ref_counts, "non-stat blocks")

    # ---- spells that use a parent with a different prefix ----
    dubious = []
    for e in entries:
        if e.type != "SpellData" or not e.spell_type or not e.using:
            continue
        parent_name = renames.get(e.using, e.using)
        child_p = leading_spell_prefix(e.new_name)
        parent_p = leading_spell_prefix(swap_user_prefix(parent_name, user_prefix)[0])
        if child_p and parent_p and child_p != parent_p:
            dubious.append(f'{e.label()}: "{e.new_name}" ({child_p}_) uses "{parent_name}" ({parent_p}_)')

    # ---- duplicates (same type, same final name) ----
    seen = defaultdict(list)
    for e in entries:
        seen[(e.type, e.new_name)].append(e)
    duplicates = [(k, v) for k, v in seen.items() if len(v) > 1]

    # ---- sorted output ----
    out_files = defaultdict(list)
    counts = Counter()
    unsorted_chunks = []
    for e in entries:
        if e.dest:
            key = (e.datadir, e.dest + ".txt")
            out_files[key].append(entry_chunk(e))
            counts[key] += 1
        else:
            unsorted_chunks.append(entry_chunk(e))
    for b in foreign:
        b.reason = "not a stat entry (left as it was, apart from renamed references)"
        unsorted_chunks.append(foreign_chunk(b))

    out_dir.mkdir(parents=True, exist_ok=True)
    out_proj = out_dir / name
    if root is not None:
        if out_dir.resolve() in out_proj.resolve().parents and out_proj.exists():
            shutil.rmtree(out_proj)        # the whole mod is rebuilt, don't keep stale files
        out_proj.mkdir(parents=True, exist_ok=True)
    else:
        for fname in known_output_names():
            (out_dir / fname).unlink(missing_ok=True)

    # ---- copy the mod, rewriting references where it is safe ----
    old_names = set(renames)
    changed_files, unchanged_refs = [], []
    if root is not None:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            rel_dir = Path(dirpath).relative_to(root)
            (out_proj / rel_dir).mkdir(parents=True, exist_ok=True)
            for fn in sorted(filenames):
                src = Path(dirpath) / fn
                if src in consumed:
                    continue
                rel = rel_dir / fn
                dst = out_proj / rel
                if rewrite_rule((root.name,) + rel.parts):
                    text, enc = decode_raw(src.read_bytes())
                    stats = Counter()
                    new_text = rewrite_text(text, renames, stats, "n", src.suffix.lower() == ".lsx")
                    if stats["n"]:
                        dst.write_bytes(new_text.encode(enc))
                        shutil.copystat(src, dst)
                        changed_files.append((rel.as_posix(), stats["n"]))
                    else:
                        shutil.copy2(src, dst)
                else:
                    shutil.copy2(src, dst)
                    if old_names:
                        text = read_scannable(src)
                        if text is not None:
                            hits = find_old_names(text, old_names)
                            if hits:
                                unchanged_refs.append((rel.as_posix(), hits))

    for (dd, fname), chunks in sorted(out_files.items(), key=lambda kv: (tuple(kv[0][0] or ()), kv[0][1])):
        target = out_proj.joinpath(*dd) if dd else out_dir
        target.mkdir(parents=True, exist_ok=True)
        # every chunk ends with a newline: joining gives exactly one blank line between entries
        (target / fname).write_text("\n".join(chunks), encoding="utf-8", newline="\n")

    unsorted_path = out_dir / f"_Unsorted_{name}.txt"
    if unsorted_chunks:
        unsorted_path.write_text("\n".join(unsorted_chunks), encoding="utf-8", newline="\n")
    else:
        unsorted_path.unlink(missing_ok=True)

    # ---- report: summary, issues, then what was changed ----
    rep = [f'Mod folder: {name}',
           f"Stat files: {len(stat_files)}   Entries: {len(entries)}   Non-stat blocks: {len(foreign)}",
           f'Settings: userPrefix = "{user_prefix}", replaceMismatchedSpellPrefixes = {str(replace_mismatch).lower()}, '
           f'convertStatusNamesUpperCase = {str(status_upper).lower()}, '
           f'convertStatusCamelCaseToUnderscores = {str(status_underscores).lower()}',
           "", "Entries per output file:"]
    for (dd, fname), n in sorted(counts.items(), key=lambda kv: (tuple(kv[0][0] or ()), kv[0][1])):
        rep.append(f"  {'/'.join(dd) + '/' if dd else ''}{fname}: {n}")
    if unsorted_chunks:
        rep.append(f"  _Unsorted_{name}.txt: {len(unsorted_chunks)}")

    section(rep, "Name clashes: same name, different entry types (not renamed, references not touched, fix these)", clashes)
    section(rep, "Not renamed", blocked)
    section(rep, "Duplicates (same type), all copies kept", [
        f'{t} "{n}": ' + ", ".join(f"{x.src}:{x.line_no}" for x in v) for (t, n), v in duplicates
    ])
    section(rep, f"In _Unsorted_{name}.txt", [
        f"{b.label()}: {b.reason}" for b in [e for e in entries if e.dest is None] + foreign
    ])
    section(rep, "Spell name / SpellType mismatches", mismatches)
    section(rep, "Spells using a parent with a different prefix (not changed)", dubious)
    section(rep, "Spells without their own SpellType", [
        f"{e.label()}: {e.type_note} -> {e.spell_type}" for e in entries if e.type_note
    ])

    ref_lines = []
    total_hits = 0
    for rel, hits in unchanged_refs:
        total_hits += len(hits)
        for line_no, tok in hits[:MAX_REPORT_HITS_PER_FILE]:
            ref_lines.append(f'{rel}:{line_no}: "{tok}" (now "{renames[tok]}")')
        if len(hits) > MAX_REPORT_HITS_PER_FILE:
            ref_lines.append(f"{rel}: ... and {len(hits) - MAX_REPORT_HITS_PER_FILE} more")
    section(rep, "References to renamed entries found in other files and NOT changed", ref_lines)
    section(rep, "Settings warnings", setting_warnings)
    if report["stray"]:
        section(rep, "Stray text before the first entry (ignored)", report["stray"])

    section(rep, "Files with updated references", [f"{rel}: {n} replacement(s)" for rel, n in changed_files])
    spells, statuses = [], []
    seen_old = set()
    for e in entries:
        if e.name in renames and e.name not in seen_old:
            seen_old.add(e.name)
            line = f"{e.name} -> {e.new_name}   [{', '.join(e.cand_notes)}]"
            (spells if e.type == "SpellData" else statuses).append(line)
    section(rep, "Renamed spells", spells)
    section(rep, "Renamed statuses", statuses)
    rep.append("")
    rep.append(f"`using` lines updated: {using_changes}")
    rep.append(f"Other references rewritten in stat entries: {sum(ref_counts.values())}")
    for key, n in sorted(ref_counts.items()):
        rep.append(f"  {key}: {n}")

    text = "\n".join(rep) + "\n"
    (out_dir / f"_Report_{name}.txt").write_text(text, encoding="utf-8", newline="\n")
    return text


def main():
    base_dir = Path(__file__).resolve().parent
    out_dir = base_dir / "convert"
    settings, setting_warnings = load_settings(base_dir)

    projects = []
    for p in sorted(base_dir.iterdir(), key=lambda x: x.name.lower()):
        if p.is_dir() and not p.name.startswith((".", "_")) and p.name.lower() not in ("convert", "__pycache__"):
            projects.append((p.name, p, None))
    loose = sorted(f for f in base_dir.glob("*.txt") if f.is_file())
    if loose:
        projects.append((LOOSE_NAME, None, loose))
    if not projects:
        sys.exit(f"Nothing to do: copy your mod folder next to the script ({base_dir}).")

    done = 0
    for name, root, loose_files in projects:
        text = process_project(name, root, loose_files, settings, setting_warnings, out_dir)
        if text is None:
            print(f'Skipped "{name}": no stat files found (expected .txt files in Stats/Generated/Data)\n')
            continue
        print(text)
        done += 1
    if not done:
        sys.exit("No stat files found.")
    print(f"Written to: {out_dir}")


if __name__ == "__main__":
    main()
