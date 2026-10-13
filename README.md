# BG3 Pre-Converter
Prepares legacy mods (created before Larian released the BG3 toolkit) for conversion with [Eclip5e's Toolkit Converter](https://github.com/Eclip5eLP/bg3-convert2toolkit).<br><br>

## Main Features

- Reads lose .txt files or entire mod folders
- Sorts stat entries from mixed .txt files into the correct format for the converter/toolkit 
- Adds spell prefixes (`Shout_`, `Target_`, etc.) when needed
- Changes status names to Larian's naming convention if desired
- Renames references to changed entries
- Outputs a `_Report.txt` with potential issues and what it changed
- Outputs a `_MergedStatEntries.txt` to be used in [Norbyte's Stat Validator](https://bg3.norbyte.dev/stats-validator)
<br><br>

## Bonus Features

In the `settings.json` additional features can be turned on. I recommend setting these all to true. Add a user prefix if you used one in your mod.

<br>

`"userPrefix": ""`

- Disabled if empty string
- Otherwise tries to swap a set prefix with the spell prefix if both exist

Example:

- `"userPrefix": "USER"` `USER_Target_Spellname` → `Target_USER_Spellname`
- `"userPrefix": ""` `USER_Target_Spellname` → `Target_USER_Target_Spellname`

---

<br>

`"replaceMismatchedSpellPrefixes": true`

- In case you named a spell `Shout_` but its spelltype is `"Target"` it will be renamed, since the converter/toolkit will not accept this mismatch.<br>

---

<br>

`"convertStatusNamesUpperCase": true`

- Convert status names into all uppercase, adhering to Larian naming conventions
- Improves readability of your functors.<br>

---

<br>

`"convertStatusCamelCaseToUnderscores": true`

Only in combination with the above. If your statuses are written in camelCase they get (relatively) smartly converted to Larian's naming scheme. It will seperate numbers and upper/lower case, keep full uppercase words together and respect dice types (D4, D6, D8, D10, D12, D20, D100).<br><br>
Example:<br>
`BurningHotD4Technical2` → `BURNING_HOT_D4_TECHNICAL_2` NOT `BURNINGHOTD4TECHNICAL2`

---

<br>

`"checkUuids": true`

Checks if used UUIDs are valid. This does not validate if they correspond to a specific version of UUID, just that their format is correct and only hexadecimal symbols are used. Malformed UUIDs can cause the toolkit to crash without an error.
<br>

## Usage
- Place a mod folder or loose .txt files within the `convert` folder
- Output will be placed into the `convert` folder, along with a `_Report.txt` and a `_MergedStatEntries.txt`
- Input files will be moved out of `convert` and into a folder `Original_Input_Files` next to it
- Read the report to learn of potential issues and as a log of what the script did
- Copy the content of `_MergedStatEntries.txt` into [Norbyte's Stat Validator](https://bg3.norbyte.dev/stats-validator)
- Fix any reported issues, as the toolkit will truncate your stat entries when it encounters an error
- Make sure to delete old files before using the script again

<br>

I'm planning to change the usage to mirror [Eclip5e's Toolkit Converter](https://github.com/Eclip5eLP/bg3-convert2toolkit) for ease of use.

<br>

## Notes:
- The script will output a report - it is important to review it. It will list problems, renames, number of changes/references and more.
- The script is case-sensitive and word wraps, so it will not confuse part of `"Target_Fireball_Trap"` with `"Target_Fireball"` or `"BLADEWARD"` with `"BladeWard"`.
- The "icon" data line is ignored, since icons may have the same name and you may not wish to rename your images and atlas references.
- Similarly "LevelMapValue" is ignored, as the referenced LevelMap may have the same name as one of your entries, this causes no issues for the toolkit or converter, while renaming them would break your LevelMap
- Not all references are renamed, only those in commonly used files are. This is to prevent renaming in unwanted areas. All references found that aren't changed are listed in the report however.
- The report is optimized a little to be viewed in [Visual Studio Code](https://code.visualstudio.com/) with the [BG3 syntax highlighting plugin](https://marketplace.visualstudio.com/items?itemName=chromosome16.bg3-text-support)

| Location | What Is Targeted |
| -------- | ------- |
| `Public/<mod>/Stats/Generated/Data/` | Stat entries |
| `Public/<mod>/<specificFolders>` | SpellLists, Rulebook, Progressions, RootTemplates |
| `Scripts/thoth/helpers/` | .khn scripts |
| `Mods/<mod>/Story/RawFiles/Goals/` | Osiris scripts |
| `Localization/<language>/` | Localization |

Entries that are not renamed (and whose references are not touched) but reported:
- the same name is used by entries of different types (case-sensitive),
- the new name already exists as another entry,
- two entries would end up with the same new name.

<br>

## Bonus Script

Use `ReplaceUUID.py` to replace a mods UUID after conversion with its original one.

- Place `ReplaceUUID.py` inside the folder with [Eclip5e's Toolkit Converter](https://github.com/Eclip5eLP/bg3-convert2toolkit) (not inside the `convert` folder, but rather next to the `Convert2Toolkit.exe`)
- Convert your mod with [Eclip5e's Toolkit Converter](https://github.com/Eclip5eLP/bg3-convert2toolkit)
- Run `ReplaceUUID.py`
- If there is a pair of folders inside `convert`, one named `<YourModName>` and one named `<YourModName>_UUID` the script will automatically grab the original UUID of your mod (from `<YourModName>/Mods/<YourModName>/meta.lsx`) and apply it across the converted mod
- If run without a matching folder pair, the script will prompt you to provide a UUID

---

This script was mainly created with the help of Claude. It has been tested extensively by me converting many of my mods with heavily unsorted and sometimes error-prone stat files to the toolkit. Please feel free to create issues on GitHub to request features or report problems.
