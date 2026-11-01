#!/usr/bin/env python3
"""Predict Forge of Potential refinement outcomes in Valheim.

The forge roll is not random per attempt: it is seeded from
hash(character name) + hash(item prefab) + target level, and each attempt
stores the next seed in the character profile. This tool replays that chain.

  forge.py --save yourfate               # all refinable items in inventory
  forge.py "Staff of Fracturing" --save yourfate
  forge.py StaffClusterbomb --name Yourfate --from-level 5 --levels 4
"""
import argparse, glob, json, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SAVE_DIRS = [
    "~/.local/share/Steam/userdata/*/892970/remote/characters",
    "~/.config/unity3d/IronGate/Valheim/characters_local",
    "~/.config/unity3d/IronGate/Valheim/characters",
]
DEFAULT_UPGRADE_CHANCE = 0.65
DEFAULT_BREAK_CHANCE = 1.0


# ---------------------------------------------------------------- game math

def i32(x):
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x >> 31 else x


def stable_hash(s):
    """Valheim's StringExtensionMethods.GetStableHashCode."""
    a = b = 5381
    i = 0
    while i < len(s) and s[i] != "\0":
        a = i32(((a << 5) + a) ^ ord(s[i]))
        if i == len(s) - 1 or s[i + 1] == "\0":
            break
        b = i32(((b << 5) + b) ^ ord(s[i + 1]))
        i += 2
    return i32(a + b * 1566083941)


class UnityRandom:
    """UnityEngine.Random: xorshift128 with MT-style state init."""

    def __init__(self, seed):
        m = 0xFFFFFFFF
        self.x = seed & m
        self.y = (1812433253 * self.x + 1) & m
        self.z = (1812433253 * self.y + 1) & m
        self.w = (1812433253 * self.z + 1) & m

    def next(self):
        t = (self.x ^ (self.x << 11)) & 0xFFFFFFFF
        self.x, self.y, self.z = self.y, self.z, self.w
        self.w = (self.w ^ (self.w >> 19) ^ t ^ (t >> 8)) & 0xFFFFFFFF
        return self.w

    def value(self):
        return (self.next() & 0x7FFFFF) / 8388607.0

    def range_int(self, lo, hi):
        return lo + self.next() % (hi - lo)


def roll(seed):
    """Player.GetItemUpgradeChance: returns (roll, next seed)."""
    r = UnityRandom(seed)
    v = r.value()
    return v, i32(r.range_int(0, 99999999))


def upgrade_key(prefab, target_level):
    return i32(stable_hash(prefab) + target_level)


def initial_seed(player_name, key):
    return i32(stable_hash(player_name) + key)


def outcome(v, up, brk):
    if up >= v:
        return "success"
    if brk >= 1.0 - v:
        return "BREAK"
    return "downgrade"


# ---------------------------------------------------------------- save file

class Reader:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def _u(self, fmt):
        v = struct.unpack_from(fmt, self.d, self.p)[0]
        self.p += struct.calcsize(fmt)
        return v

    def int(self): return self._u("<i")
    def long(self): return self._u("<q")
    def float(self): return self._u("<f")
    def bool(self): return self._u("<?")
    def vec3(self): self.p += 12
    def bytes(self): n = self.int(); self.p += n

    def str(self):
        n = shift = 0
        while True:
            b = self.d[self.p]; self.p += 1
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        s = self.d[self.p:self.p + n].decode("utf-8", "replace")
        self.p += n
        return s

    def str_float_dict(self):
        for _ in range(self.int()):
            self.str(); self.float()


class Character:
    def __init__(self, path):
        raw = open(path, "rb").read()
        n = struct.unpack_from("<i", raw, 0)[0]
        self.data = raw[4:4 + n]
        self.path = path
        self.name = self._read_name()
        self.seeds = self._read_seed_table()

    def _read_name(self):
        """Walk PlayerProfile.LoadPlayerFromDisk up to the name field."""
        r = Reader(self.data)
        ver = r.int()
        if ver < 44:
            raise ValueError(f"profile version {ver} too old; load and save the character in-game first")
        stat_count, difficulties = r.int(), r.int()
        for _ in range(difficulties):
            r.p += 4 * stat_count
            for _ in range(3):  # known worlds, world keys, commands
                r.str_float_dict()
            for _ in range(r.int()):  # enemy stats
                r.str_float_dict()
            for _ in range(5):  # pickups, crafts, pickables, food, pieces
                r.str_float_dict()
        r.bool()  # first spawn
        for _ in range(r.int()):  # per-world data
            r.long(); r.bool(); r.vec3(); r.bool(); r.vec3(); r.bool(); r.vec3(); r.vec3()
            if r.bool():
                r.bytes()
        return r.str()

    def _read_seed_table(self):
        """m_itemUpgradeSeeds is the last thing in the player data, which ends the profile.

        Walk backwards: it is preceded by CharacterDrop.s_pseudoCounter, another
        int->int dict, so require both counts to line up.
        """
        d, end = self.data, len(self.data)
        for n in range(0, 10000):
            p = end - 8 * n - 4
            if p < 0:
                break
            if struct.unpack_from("<i", d, p)[0] != n:
                continue
            for m in range(0, 10000):
                q = p - 8 * m - 4
                if q < 0:
                    break
                if struct.unpack_from("<i", d, q)[0] == m:
                    return {k: v for k, v in struct.iter_unpack("<ii", d[p + 4:end])}
        raise ValueError("could not locate the upgrade seed table in the save")

    def find_items(self, prefab):
        """Inventory entries of a prefab -> list of (quality, x, y).

        Items are saved as: durability int, x, y, worldLevel, flags, [quality],
        [stack], [variant], [crafterID, crafterName], prefab hash, ...
        Locate the hash, then find a start offset that parses forward onto it.
        """
        h = struct.pack("<i", stable_hash(prefab))
        found, start = [], 0
        while (p := self.data.find(h, start)) != -1:
            start = p + 1
            for st in range(max(0, p - 300), p - 7):
                try:
                    r = Reader(self.data, st + 4)
                    x, y, _wl, fl = self.data[r.p:r.p + 4]; r.p += 4
                    if not fl & 0x40 or fl & ~0xFF:
                        continue
                    q = r._u("<H") if fl & 4 else 1
                    if fl & 8: r.p += 2
                    if fl & 16: r.p += 4
                    if fl & 32: r.long(); r.str()
                    if r.p == p and 1 <= q <= 100:
                        found.append((q, x, y))
                        break
                except (IndexError, struct.error):
                    pass
        return found


def find_save(arg):
    if os.path.isfile(arg):
        return arg
    for d in SAVE_DIRS:
        hits = glob.glob(os.path.join(os.path.expanduser(d), arg.lower() + ".fch"))
        if hits:
            return max(hits, key=os.path.getmtime)
    sys.exit(f"no character file found for '{arg}' (looked in {', '.join(SAVE_DIRS)})")


# ---------------------------------------------------------------- item lookup

def load_items():
    path = os.path.join(HERE, "items.json")
    if not os.path.exists(path):
        return {}
    return json.load(open(path))


def resolve_item(query, items):
    if query in items:
        return query
    q = query.lower()
    for prefab, it in items.items():
        if prefab.lower() == q or it["name"].lower() == q:
            return prefab
    partial = [p for p, it in items.items() if q in it["name"].lower() or q in p.lower()]
    if len(partial) == 1:
        return partial[0]
    if partial:
        sys.exit("ambiguous item, did you mean:\n" + "\n".join(f"  {p:28} {items[p]['name']}" for p in partial))
    print(f"note: '{query}' not in items.json, treating it as a raw prefab name", file=sys.stderr)
    return query


# ---------------------------------------------------------------- reports

def chances(prefab, items):
    idol = items.get(prefab, {}).get("idol")
    if not idol:
        return DEFAULT_UPGRADE_CHANCE, DEFAULT_BREAK_CHANCE
    return idol["upgradeChance"], idol["breakChance"]


def attempts_made(player_name, key, stored, limit=500):
    """How many attempts produced the stored seed, or None if it can't be reproduced."""
    seed = initial_seed(player_name, key)
    for n in range(1, limit + 1):
        seed = roll(seed)[1]
        if seed == stored:
            return n
    return None


def level_sequence(prefab, level, player_name, seeds, count, items):
    key = upgrade_key(prefab, level)
    seed = seeds.get(key, initial_seed(player_name, key))
    up, brk = chances(prefab, items)
    out = []
    for _ in range(count):
        v, seed = roll(seed)
        out.append((v, outcome(v, up, brk)))
    return out


def print_item(prefab, items, player_name, seeds, from_level, levels, attempts, current=None):
    it = items.get(prefab)
    title = f"{it['name']} ({prefab})" if it else prefab
    if it:
        title += f", max normal quality {it['maxQuality']}, uses {it['idol']['name']}"
    print(title)
    if current:
        print("  in inventory at quality " + ", ".join(str(q) for q, _, _ in current))

    print(f"  {'target':>6}  {'done':>4}  rolls (next attempt first; <=0.65 succeeds)")
    for lvl in range(from_level, from_level + levels):
        key = upgrade_key(prefab, lvl)
        done = "0"
        if key in seeds:
            n = attempts_made(player_name, key, seeds[key])
            done = str(n) if n is not None else "?"
        seq = level_sequence(prefab, lvl, player_name, seeds, attempts, items)
        cells = "  ".join(f"{'✓' if o == 'success' else '✗' if o == 'BREAK' else '↓'} {v:.3f}" for v, o in seq)
        print(f"  {lvl:>6}  {done:>4}  {cells}")

    # Straight run: keep refining the same copy until it breaks.
    path, lvl = [], from_level
    for _ in range(levels):
        v, o = level_sequence(prefab, lvl, player_name, seeds, 1, items)[0]
        if o != "success":
            path.append(f"→{lvl} {o}")
            break
        path.append(f"→{lvl} ok")
        lvl += 1
    print("  refining one copy now: " + ", ".join(path))
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("item", nargs="?", help="prefab or display name (partial match ok); omit with --save to scan inventory")
    ap.add_argument("--save", help="character name or path to .fch; supplies name, stored seeds and item levels")
    ap.add_argument("--name", help="character name as shown in game (overrides the save's)")
    ap.add_argument("--from-level", type=int, help="first target level (default: current quality + 1, else 2)")
    ap.add_argument("--levels", type=int, default=6, help="how many target levels to show (default 6)")
    ap.add_argument("--attempts", type=int, default=5, help="rolls to show per level (default 5)")
    args = ap.parse_args()

    items = load_items()
    if not items:
        print("warning: items.json missing, run build_itemdb.py; using default chances", file=sys.stderr)

    seeds, char = {}, None
    if args.save:
        char = Character(find_save(args.save))
        seeds = char.seeds
        print(f"save: {char.path}\ncharacter: {char.name}, {len(seeds)} stored forge seeds\n")
    name = args.name or (char and char.name)
    if not name:
        sys.exit("need --name or --save")
    if args.name and char and args.name != char.name:
        seeds = {}  # stored seeds belong to the save's character

    if args.item:
        prefab = resolve_item(args.item, items)
        current = char.find_items(prefab) if char else []
        start = args.from_level or (max(q for q, _, _ in current) + 1 if current else 2)
        print_item(prefab, items, name, seeds, start, args.levels, args.attempts, current)
        return

    if not char:
        sys.exit("give an item, or --save to scan the inventory")
    any_found = False
    for prefab in items:
        current = char.find_items(prefab)
        if current:
            any_found = True
            start = args.from_level or max(q for q, _, _ in current) + 1
            print_item(prefab, items, name, seeds, start, args.levels, args.attempts, current)
    if not any_found:
        print("no forge-refinable items found in inventory")


if __name__ == "__main__":
    main()
