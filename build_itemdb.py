#!/usr/bin/env python3
"""Extract forge-refinable items from the Valheim install into items.json.

Needs UnityPy + TypeTreeGeneratorAPI (pip install UnityPy TypeTreeGeneratorAPI).
Only has to be re-run after a game patch.
"""
import argparse, json, re, os
import UnityPy
from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator

DEFAULT_GAME = os.path.expanduser("~/.local/share/Steam/steamapps/common/Valheim")


def english_names(data_dir):
    raw = open(os.path.join(data_dir, "resources.assets"), "rb").read().decode("utf-8", "replace")
    return {m.group(1): m.group(2) for m in re.finditer(r'"([a-z0-9_]+)","([^"]*)"', raw)}


ITEM_TYPE_CATEGORY = {5: "Shields", 6: "Helmets", 7: "Chest armor", 11: "Leg armor", 12: "Gloves",
                      17: "Capes", 18: "Utility", 24: "Trinkets", 19: "Tools", 15: "Tools"}
SKILL_CATEGORY = {1: "Swords", 2: "Knives", 3: "Clubs", 4: "Polearms", 5: "Spears", 6: "Shields",
                  7: "Axes", 8: "Bows", 9: "Elemental magic", 10: "Blood magic", 11: "Fists",
                  12: "Tools", 13: "Tools", 14: "Crossbows"}


def category(shared):
    if shared["m_itemType"] in ITEM_TYPE_CATEGORY:
        return ITEM_TYPE_CATEGORY[shared["m_itemType"]]
    return SKILL_CATEGORY.get(shared["m_skillType"], "Tools")


def localize(token, loc):
    # "$item_upgrader_tier3 $item_upgrader_weapon $item_upgrader_name" -> "Silver Battle Idol"
    return re.sub(r"\$([a-z0-9_]+)", lambda m: loc.get(m.group(1), m.group(0)), token)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game", default=DEFAULT_GAME)
    ap.add_argument("-o", "--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "items.json"))
    args = ap.parse_args()
    data_dir = os.path.join(args.game, "valheim_Data")

    env = UnityPy.load(os.path.join(data_dir, "StreamingAssets/SoftRef/Bundles/c4210710"))
    gen = TypeTreeGenerator(env.objects[0].assets_file.unity_version)
    gen.load_local_dll_folder(os.path.join(data_dir, "Managed"))
    env.typetree_generator = gen
    loc = english_names(data_dir)

    def tree(pptr):
        try:
            return pptr.deref().read_typetree()
        except Exception:
            return None

    item_cache = {}

    def item_info(pptr):
        """ItemDrop pptr (points at the GameObject's ItemDrop component) -> (prefab name, shared data)."""
        key = (pptr.m_FileID, pptr.m_PathID)
        if key not in item_cache:
            obj = pptr.deref()
            mb = obj.read()
            go = mb.m_GameObject.deref().read()
            item_cache[key] = (go.m_Name, obj.read_typetree()["m_itemData"]["m_shared"])
        return item_cache[key]

    items = {}
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            t = obj.read_typetree()
        except Exception:
            continue
        if "m_resources" not in t or "m_item" not in t or not t.get("m_enabled", True):
            continue
        if not any(r.get("m_upgraderResource") for r in t["m_resources"]):
            continue
        mb = obj.read()
        prefab, shared = item_info(mb.m_item)
        resources = []
        idol = None
        for r, rr in zip(t["m_resources"], mb.m_resources):
            rname, rshared = item_info(rr.m_resItem)
            entry = {"prefab": rname, "name": localize(rshared["m_name"], loc),
                     "amount": r["m_amount"], "amountPerLevel": r["m_amountPerLevel"],
                     "recover": bool(r["m_recover"]), "upgrader": bool(r["m_upgraderResource"])}
            if entry["upgrader"]:
                idol = entry | {"upgradeChance": rshared["m_upgradeChance"],
                                "breakChance": rshared["m_breakChance"],
                                "breakReturn": rshared["m_breakReturnIngreientsAmount"]}
            resources.append(entry)
        items[prefab] = {"name": localize(shared["m_name"], loc), "maxQuality": shared["m_maxQuality"],
                         "category": category(shared),
                         "idol": idol, "resources": resources}
        print(f"{prefab:28} {items[prefab]['name']:32} {items[prefab]['category']:16} {idol['name']}")

    json.dump(dict(sorted(items.items())), open(args.out, "w"), indent=1)
    print(f"wrote {len(items)} items to {args.out}")


if __name__ == "__main__":
    main()
