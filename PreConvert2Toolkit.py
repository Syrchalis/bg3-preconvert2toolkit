#!/usr/bin/env python3
"""
BG3 Pre-Converter
===============
Prepares legacy mods (created before Larian released the BG3 toolkit) for conversion with eclip5e's toolkit converter:
https://github.com/Eclip5eLP/bg3-convert2toolkit

Features & Usage:
https://github.com/Syrchalis/bg3-preconvert2toolkit

"""

import codecs
import json
import os
import re
import shutil
import sys
import traceback
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
    "checkUuids": True,
}

ORIGINALS_NAME = "Original_Input_Files"

# data keys that hold asset names, never entry names: not rewritten
NEVER_REWRITE_KEYS = {"Icon"}

# Files whose references are rewritten: (folder run, file names or None, extensions, report group).
# A folder run is a sequence of folder names (lower case, "*" = any one folder)
# that must appear somewhere in the path, including the mod folder itself.
REWRITE_RULES = [
    (("public", "*", "roottemplates"), None, (".lsx",), ".lsx files"),
    (("public", "*", "lists"), ("spelllists.lsx",), (".lsx",), ".lsx files"),
    (("public", "*", "shapeshift"), ("rulebook.lsx",), (".lsx",), ".lsx files"),
    (("public", "*", "progressions"), ("progressions.lsx",), (".lsx",), ".lsx files"),
    (("scripts", "thoth", "helpers"), None, (".khn",), "Scripts"),
    (("mods", "*", "story", "rawfiles", "goals"), None, (".txt",), "Scripts"),
    (("localization", "*"), None, (".xml",), "Localization"),
]

BINARY_EXTS = {
    ".lsf", ".pak", ".dds", ".png", ".jpg", ".jpeg", ".gif", ".gr2", ".wem", ".wav",
    ".ogg", ".mp3", ".mp4", ".bik", ".gts", ".gtp", ".bin", ".dll", ".exe", ".zip",
    ".7z", ".rar", ".ttf", ".otf", ".psd", ".tga", ".bmp", ".webp", ".loca", ".lsbs",
    ".lsbc", ".lsfx", ".bk2", ".ico", ".fbx", ".blend",
}
MAX_SCAN_BYTES = 20 * 1024 * 1024
MAX_REPORT_HITS_PER_FILE = 25
MAX_REPORT_LOCATIONS = 50
LOOSE_NAME = "LooseFiles"
REPORT_NAME = "_Report.txt"
MERGED_NAME = "_MergedStatEntries.txt"

NEW_RE = re.compile(r'^\s*new\s+(\S+)\s+"([^"]*)"', re.IGNORECASE)
TYPE_RE = re.compile(r'^\s*type\s+"([^"]*)"')
USING_RE = re.compile(r'^(\s*using\s+")([^"]*)(")')
DATA_RE = re.compile(r'^\s*data\s+"([^"]*)"\s+"(.*)"\s*$')
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
DICE_RE = re.compile(r"[Dd](?:100|20|12|10|8|6|4)(?!\d)")
ICON_ATTR_RE = re.compile(r'<attribute\b[^>]*\bid="Icon"[^>]*>', re.IGNORECASE)
# LevelMapValue(<name>) points to a level map that may be defined in the base game: never touched
LEVELMAP_RE = re.compile(r"LevelMapValue\(\s*[^()]*\)")
# something shaped like a UUID (8-4-4-4-12 letters/digits), possibly with a typo in it
UUID_SHAPE_RE = re.compile(
    r"(?<![0-9A-Za-z-])[0-9A-Za-z]{8}-[0-9A-Za-z]{4}-[0-9A-Za-z]{4}-[0-9A-Za-z]{4}-[0-9A-Za-z]{12}(?![0-9A-Za-z-])")
# five groups of hex digits that are not 8-4-4-4-12, but about as long as a UUID
UUID_GROUPS_RE = re.compile(r"(?<![0-9A-Za-z-])[0-9A-Fa-f]+(?:-[0-9A-Fa-f]+){4}(?![0-9A-Za-z-])")
HEX_DIGITS = set("0123456789abcdefABCDEF")


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
        sys.exit('settings.json must contain a JSON object, e.g. {"userPrefix": "SYR_"}')
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
        self.out_pos = None       # 'Spell_Target.txt:12' of its header line in the sorted output

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
    """'SYR_Shout_Foo' -> ('Shout_SYR_Foo', True) if user_prefix is 'SYR_'."""
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

    spans = [m.span() for m in LEVELMAP_RE.finditer(text)]
    if protect_icons:
        spans += [m.span() for m in ICON_ATTR_RE.finditer(text)]
    if not spans:
        return sub_part(text)
    out, last = [], 0
    for start, end in sorted(spans):          # these stay exactly as they are
        if start < last:
            continue
        out.append(sub_part(text[last:start]))
        out.append(text[start:end])
        last = end
    out.append(sub_part(text[last:]))
    return "".join(out)


def find_old_names(text, old_names):
    hits = []
    if not (set(TOKEN_RE.findall(text)) & old_names):
        return hits
    for i, line in enumerate(text.splitlines(), 1):
        for t in dict.fromkeys(TOKEN_RE.findall(LEVELMAP_RE.sub("", line))):
            if t in old_names:
                hits.append((i, t))
    return hits


def find_bad_uuids(text):
    """[(line, value, reason)] for values that look like a UUID but are malformed.
    A well-formed UUID (8-4-4-4-12 hex digits, any version) is never reported."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if "-" not in line:
            continue
        for m in UUID_SHAPE_RE.finditer(line):
            bad = sorted({c for c in m.group(0) if c != "-" and c not in HEX_DIGITS})
            if bad:
                hits.append((n, m.group(0), "contains non-hex character(s): " + " ".join(bad)))
        for m in UUID_GROUPS_RE.finditer(line):
            lens = tuple(len(g) for g in m.group(0).split("-"))
            if lens != (8, 4, 4, 4, 12) and 28 <= sum(lens) <= 36:
                hits.append((n, m.group(0), "group lengths " + "-".join(map(str, lens)) + ", expected 8-4-4-4-12"))
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
    """Report group (".lsx files", "Scripts", "Localization") of a file whose references are
    rewritten, None for every other file."""
    low = [p.lower() for p in full_parts]
    dirs, fname = low[:-1], low[-1]
    ext = os.path.splitext(fname)[1]
    for run, names, exts, group in REWRITE_RULES:
        if ext not in exts or (names and fname not in names):
            continue
        for i in range(len(dirs) - len(run) + 1):
            if all(r == "*" or dirs[i + j] == r for j, r in enumerate(run)):
                return group
    return None


def has_stat_blocks(path):
    return any(NEW_RE.match(l) for l in read_text(path).splitlines())


def block(title, items, indent=2):
    """Report sub-block: '  Title (n):' with the items indented two more spaces, then a blank line."""
    pad = " " * indent
    if not items:
        return [f"{pad}{title} (0):"]
    return [f"{pad}{title} ({len(items)}):"] + [f"{pad}  {i}" for i in items] + [""]


# --------------------------------------------------------------------- project

def process_project(name, root, loose_files, settings, out_dir):
    user_prefix = settings["userPrefix"]
    replace_mismatch = settings["replaceMismatchedSpellPrefixes"]
    status_upper = settings["convertStatusNamesUpperCase"]
    status_underscores = settings["convertStatusCamelCaseToUnderscores"]
    check_uuids = settings["checkUuids"]

    # ---- the files of this mod (or of the loose group); results go to dest_root ----
    dest_root = out_dir / name if root is not None else out_dir
    files = []                         # (source path, path relative to the mod folder)
    if root is not None:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            rel_dir = Path(dirpath).relative_to(root)
            (dest_root / rel_dir).mkdir(parents=True, exist_ok=True)
            files += [(Path(dirpath) / fn, rel_dir / fn) for fn in sorted(filenames)]
    else:
        files = [(p, Path(p.name)) for p in loose_files]

    # ---- find the stat files ----
    stat_files = []                    # (path, label, data dir)
    consumed = set()
    for src, rel in files:
        if src.suffix.lower() != ".txt":
            continue
        if root is not None:
            dd = stat_data_dir((root.name,) + rel.parts)
            if dd is None:
                continue
        elif has_stat_blocks(src):
            dd = None                  # loose file: sorted files go straight to convert/
        else:
            continue
        stat_files.append((src, rel.as_posix(), dd))
        consumed.add(src)

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
            continue                   # reported under "Duplicate Names"
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
            dubious.append((e, parent_name, child_p, parent_p))

    # ---- duplicates (same type, same final name) ----
    seen = defaultdict(list)
    for e in entries:
        seen[(e.type, e.new_name)].append(e)
    duplicates = [(k, v) for k, v in seen.items() if len(v) > 1]

    # ---- sorted output ----
    out_files = defaultdict(list)
    out_entries = defaultdict(list)
    counts = Counter()
    unsorted_chunks = []
    unsorted_entry_chunks = []
    for e in entries:
        if e.dest:
            key = (e.datadir, e.dest + ".txt")
            out_files[key].append(entry_chunk(e))
            out_entries[key].append(e)
            counts[key] += 1
        else:
            unsorted_chunks.append(entry_chunk(e))
            unsorted_entry_chunks.append(entry_chunk(e))
    for b in foreign:
        b.reason = "not a stat entry (left as it was, apart from renamed references)"
        unsorted_chunks.append(foreign_chunk(b))

    # ---- copy the files, rewriting references where it is safe; look for UUIDs and old names ----
    old_names = set(renames)
    changed_files, unchanged_refs, uuid_hits = [], [], []
    for src, rel in files:
        text = read_scannable(src)     # None for binary and huge files
        if check_uuids and text is not None:
            uuid_hits += [(rel.as_posix(), *h) for h in find_bad_uuids(text)]
        if src in consumed:
            continue                   # replaced by the sorted files
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        kind = rewrite_rule((root.name,) + rel.parts) if root is not None else None
        if kind:
            decoded, enc = decode_raw(src.read_bytes())
            stats = Counter()
            new_text = rewrite_text(decoded, renames, stats, "n", src.suffix.lower() == ".lsx")
            if stats["n"]:
                dst.write_bytes(new_text.encode(enc))
                shutil.copystat(src, dst)
                changed_files.append((kind, rel.as_posix(), stats["n"]))
            else:
                shutil.copy2(src, dst)
        else:
            shutil.copy2(src, dst)
            if old_names and text is not None:
                hits = find_old_names(text, old_names)
                if hits:
                    unchanged_refs.append((rel.as_posix(), hits))

    for (dd, fname), chunks in sorted(out_files.items(), key=lambda kv: (tuple(kv[0][0] or ()), kv[0][1])):
        target = dest_root.joinpath(*dd) if dd else dest_root
        target.mkdir(parents=True, exist_ok=True)
        line = 1                       # position of each entry in the written file, for the report
        for e, c in zip(out_entries[(dd, fname)], chunks):
            e.out_pos = f"{fname}:{line + len(e.lead)}"
            line += c.count("\n") + 1
        # every chunk ends with a newline: joining gives exactly one blank line between entries
        (target / fname).write_text("\n".join(chunks), encoding="utf-8", newline="\n")

    # all stat entries in one file for the validator: the sorted ones in file order, then the unsorted ones
    merged = [c for _, chunks in sorted(out_files.items(), key=lambda kv: (tuple(kv[0][0] or ()), kv[0][1]))
              for c in chunks] + unsorted_entry_chunks

    # ---- report: counts, potential issues, what was changed ----
    rep = ["=" * 70,
           f"Mod folder: {name}" if root is not None else "Loose Files",
           "=" * 70,
           f"Stat files: {len(stat_files)}   Entries: {len(entries)}   Non-stat blocks: {len(foreign)}",
           "", "Entries per output file:"]
    per_file = Counter()
    for (dd, fname), n in counts.items():
        per_file[fname] += n
    for fname, n in sorted(per_file.items(), key=lambda kv: kv[0].lower()):
        rep.append(f"  {fname}: {n}")
    if not stat_files:
        rep.append("  none: no stat files found (expected .txt files in Stats/Generated/Data), "
                   "everything was copied unchanged")
    rep.append("")

    # potential issues
    dup_names = []
    for n, es in entries_by_name.items():
        if n in clash_names:
            dup_names.append(f"'{n}': " + ", ".join(f"{x.type or '(no type)'} ({x.src}:{x.line_no})" for x in es) +
                             " - same name, different types: not renamed, references not touched")
    for (t, n), v in duplicates:
        dup_names.append(f"'{n}' ({t}): " + ", ".join(f"{x.src}:{x.line_no}" for x in v) +
                         " - same type: all copies kept")
    skipped = [x.replace('"', "'") for x in blocked]

    mismatch_items = []
    for e in entries:
        if not e.mismatch:
            continue
        what = f"SpellType is {e.spell_type}"
        if e.new_name != e.name and replace_mismatch:
            text = f"'{e.name}' -> '{e.new_name}': {what}"
        elif e.new_name != e.name:
            text = f"'{e.name}' -> '{e.new_name}': {what}, wrong prefix kept"
        elif e.name in blocked_names:
            text = f"'{e.name}': {what}, rename skipped (see Renames Skipped)"
        else:
            text = f"'{e.name}': {what}, prefix kept (replaceMismatchedSpellPrefixes is off)"
        mismatch_items.append(f"{text} [{e.out_pos}]")
    parent_items = [f"'{e.new_name}' ({cp}_) inherits from '{pn}' ({pp}_) [{e.out_pos}]"
                    for e, pn, cp, pp in dubious]
    no_type_items = [f"'{e.new_name}' has no SpellType defined, {e.type_note.replace("no SpellType, ", "", 1).replace(chr(34), chr(39))} -> {e.spell_type} [{e.out_pos}]"
                     for e in entries if e.type_note]
    type_issues = []
    for title, items in (("Prefix does not match SpellType:", mismatch_items),
                         ("Parent has different prefix:", parent_items),
                         ("No own SpellType:", no_type_items)):
        if items:
            type_issues.append(title)
            type_issues += [f"  {i}" for i in items]
    n_type_issues = len(mismatch_items) + len(parent_items) + len(no_type_items)
    unsorted_items = [f"'{b.name}' ({b.src}:{b.line_no}): {b.reason}"
                      for b in [e for e in entries if e.dest is None] + foreign]

    uuid_lines = []
    if check_uuids:
        by_file = defaultdict(list)
        for rel, line_no, value, reason in uuid_hits:
            by_file[rel].append((line_no, value, reason))
        for rel, hits in by_file.items():
            for line_no, value, reason in hits[:MAX_REPORT_HITS_PER_FILE]:
                uuid_lines.append(f'{rel}:{line_no}: "{value}" ({reason})')
            if len(hits) > MAX_REPORT_HITS_PER_FILE:
                uuid_lines.append(f"{rel}: ... and {len(hits) - MAX_REPORT_HITS_PER_FILE} more")

    n_issues = len(dup_names) + len(skipped) + n_type_issues + len(unsorted_items) + len(uuid_lines)
    rep.append(f"Potential Issues ({n_issues}):")
    rep += block("Duplicate Names", dup_names)
    rep += block("Renames Skipped", skipped)
    if type_issues:
        rep.append(f"  Spell Type Issues ({n_type_issues}):")
        rep += [f"    {l}" for l in type_issues]
        rep.append("")
    else:
        rep.append("  Spell Type Issues (0):")
    rep += block("Unsorted Entries", unsorted_items)
    if check_uuids:
        rep += block("Malformed UUIDs", uuid_lines)
    if rep[-1] != "":
        rep.append("")

    # references in other files that were not changed, grouped by the old name
    by_tok = defaultdict(set)
    for rel, hits in unchanged_refs:
        for line_no, tok in hits:
            by_tok[tok].add((rel, line_no))
    if by_tok:
        rep.append("Unchanged references to renamed entries (update manually):")
        for tok in sorted(by_tok, key=lambda t: (renames[t].lower(), t)):
            rep.append(f'  "{renames[tok]}" (was "{tok}")')
            locs = sorted(by_tok[tok], key=lambda x: (x[0].lower(), x[1]))
            rep += [f"    {rel}:{line_no}" for rel, line_no in locs[:MAX_REPORT_LOCATIONS]]
            if len(locs) > MAX_REPORT_LOCATIONS:
                rep.append(f"    ... and {len(locs) - MAX_REPORT_LOCATIONS} more")
            rep.append("")

    # what was updated
    total_updates = sum(n for _, _, n in changed_files) + using_changes + sum(ref_counts.values())
    if total_updates:
        rep.append(f"Updated references ({total_updates}):")
        for group in (".lsx files", "Localization", "Scripts"):
            rows = sorted(((rel, n) for g, rel, n in changed_files if g == group), key=lambda x: x[0].lower())
            if rows:
                rep.append(f"  {group}")
                rep += [f"    {rel}: {n} replacement(s)" for rel, n in rows]
        if using_changes or ref_counts:
            rep.append("  Stats")
            if using_changes:
                rep.append(f'    "using" lines updated: {using_changes}')
            for key in sorted(ref_counts, key=str.lower):
                label = key if key == "non-stat blocks" else f'data "{key}"'
                rep.append(f"    {label}: {ref_counts[key]}")
        rep.append("")

    # what was renamed
    spells, statuses = defaultdict(list), defaultdict(list)
    seen_old = set()
    for e in entries:
        if e.name in renames and e.name not in seen_old:
            seen_old.add(e.name)
            action = ", ".join(e.cand_notes)
            action = action[:1].upper() + action[1:]
            (spells if e.type == "SpellData" else statuses)[action].append(
                f"{e.name} -> '{e.new_name}'" if e.type == "SpellData" else f"{e.name} -> {e.new_name}")
    for title, groups in (("Renamed Spells", spells), ("Renamed Statuses", statuses)):
        if groups:
            rep.append(f"  {title} ({sum(len(v) for v in groups.values())}):")
            for action, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
                rep.append(f"    {action} ({len(items)})")
                rep += [f"      {i}" for i in items]
            rep.append("")

    if unsorted_chunks:                # full text last, so the report stays readable
        rep.append("-" * 70)
        rep.append("Full text of the unsorted entries:")
        rep.append("-" * 70)
        rep.append("")
        rep.append("\n".join(unsorted_chunks))

    return {"text": "\n".join(rep).rstrip("\n") + "\n", "merged": merged}


def is_ignored(path):
    """Items in convert/ that are never treated as input."""
    n = path.name
    return (n == ORIGINALS_NAME or n in (REPORT_NAME, MERGED_NAME) or
            n.startswith(("_Report_", "_MergedStatEntries_", "_Unsorted_", ".")) or
            n.lower() in ("desktop.ini", "thumbs.db"))


def main():
    base_dir = Path(__file__).resolve().parent
    convert_dir = base_dir / "convert"
    settings, setting_warnings = load_settings(base_dir)

    if not convert_dir.is_dir():
        convert_dir.mkdir()
        print(f"Created the folder {convert_dir}\n"
              "Put your loose stat .txt files and/or your mod folders into it and run the script again.")
        return
    items = [p for p in sorted(convert_dir.iterdir(), key=lambda x: x.name.lower()) if not is_ignored(p)]
    if not items:
        print(f"Nothing to do: put your loose stat .txt files and/or your mod folders into {convert_dir} first.")
        return

    # move everything out of the way first: the results then can't be mistaken for input
    originals_dir = base_dir / ORIGINALS_NAME
    originals_dir.mkdir(exist_ok=True)
    moved, failed = [], []
    for p in items:
        dst = originals_dir / p.name
        try:
            if dst.is_dir() and not dst.is_symlink():
                shutil.rmtree(dst)
            elif dst.exists() or dst.is_symlink():
                dst.unlink()
            shutil.move(str(p), str(dst))
        except OSError as ex:
            failed.append(f'"{p.name}": {ex}')
            continue
        moved.append(dst)

    projects = [(d.name, d, None) for d in moved if d.is_dir()]
    loose = [d for d in moved if d.is_file()]
    if loose:
        projects.append((LOOSE_NAME, None, loose))
    head = ["Settings:"] + [f"  {k}: {json.dumps(settings[k])}" for k in DEFAULT_SETTINGS]
    if setting_warnings:
        head += ["", f"Settings warnings ({len(setting_warnings)}):"] + [f"  {w}" for w in setting_warnings]
    texts, merged = [], []
    for name, root, loose_files in projects:           # mods first, loose files last
        result = process_project(name, root, loose_files, settings, convert_dir)
        texts.append(result["text"])
        merged += result["merged"]
    report = "\n".join(head) + "\n\n" + "\n".join(texts)
    (convert_dir / REPORT_NAME).write_text(report, encoding="utf-8", newline="\n")
    merged_path = convert_dir / MERGED_NAME
    if merged:
        merged_path.write_text("\n".join(merged), encoding="utf-8", newline="\n")
    elif merged_path.exists():
        merged_path.unlink()
    print(report)
    if failed:
        print("Could not move these into " + ORIGINALS_NAME + " (close programs that use them and try again), "
              "they were skipped:")
        for f in failed:
            print("  " + f)
        print()
    print(f"Originals: {originals_dir}\nResults:   {convert_dir}")


def wait_for_key():
    """Keeps a console window that was opened by double-click open until a key is pressed."""
    if "--no-pause" in sys.argv or not sys.stdin or not sys.stdin.isatty():
        return
    try:
        import msvcrt                  # Windows: any key
        print("\nPress any key to exit...", end="", flush=True)
        msvcrt.getch()
    except ImportError:
        input("\nPress Enter to exit...")
    print()


if __name__ == "__main__":
    exit_code = 0
    try:
        main()
    except SystemExit as ex:           # sys.exit("message") somewhere above
        exit_code = ex.code
        if isinstance(exit_code, str):
            print(exit_code, file=sys.stderr)
            exit_code = 1
    except Exception:                  # show the error instead of a window that vanishes
        traceback.print_exc()
        exit_code = 1
    wait_for_key()
    sys.exit(exit_code)
