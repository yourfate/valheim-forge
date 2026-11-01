# Valheim Forge Roll Predictor

Valheim's Forge of Potential doesn't roll randomly. Each refinement roll is seeded from
`hash(character name) + hash(item prefab) + target level`, and every attempt saves the next
seed in the character profile. This project replays that sequence so you can see results in advance.

- **Web app:** `docs/index.html` (served by GitHub Pages). Pick an item, enter your character
  name, or load your `.fch` save. The save is parsed locally in the browser.
- **CLI:** `forge.py`, Python 3 standard library only.

```
./forge.py --save yourfate                       # every refinable item in your inventory
./forge.py "Staff of Fracturing" --save yourfate
./forge.py StaffClusterbomb --name Yourfate --from-level 5 --levels 4
```

## The rules (from the game code)

- Idols: 65% success. Their break chance is 100%, so every failure destroys the item. The
  "downgrade" outcome exists in the code but can't happen with the current idols.
- Only the idol is consumed. On a break you get back `ceil(0.35 × (base materials + previous
  upgrade step's materials))`. The idol isn't refunded.
- The forge rolls on every attempt, even below the item's normal max level.
- The sequence is keyed per item type and target level, so all copies of an item share it.

## After a game patch

`items.json` is extracted from the game files. To rebuild it:

```
pip install UnityPy TypeTreeGeneratorAPI
./build_itemdb.py          # reads ~/.local/share/Steam/steamapps/common/Valheim
./build_web.py             # writes docs/index.html and web/forge.html
```

The web source is `web/forge.src.html`. `build_web.py` inserts the item data into it.
