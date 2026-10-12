# BG3 Pre-Converter
Prepares legacy mods (created before Larian released the BG3 toolkit) for conversion with eclip5e's toolkit converter:
https://github.com/Eclip5eLP/bg3-convert2toolkit

Main Features:
- Reads lose .txt files or entire mod folders
- Sorts stat entries from mixed .txt files into the correct format for the converter/toolkit 
- Adds spell prefixes (Shout_, Target_, etc.) when needed
- Renames references to changed entries

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

Usage:
- WIP (detects .txt files and mod folders next to it in the same folder)

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
