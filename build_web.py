#!/usr/bin/env python3
"""Build the web app: inject items.json into web/forge.src.html.

Writes docs/index.html (standalone page, for GitHub Pages) and web/forge.html
(the same content without the document wrapper).
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    items = json.load(open(os.path.join(HERE, "items.json"), encoding="utf-8"))
    rows = [[prefab, it["name"], it["category"], it["maxQuality"], it["idol"]["name"],
             it["idol"]["amount"], it["idol"]["amountPerLevel"],
             [[r["name"], r["amount"], r["amountPerLevel"]] for r in it["resources"] if r["recover"] and not r["upgrader"]]]
            for prefab, it in items.items()]
    src = open(os.path.join(HERE, "web", "forge.src.html"), encoding="utf-8").read()
    body = src.replace("__ITEMS__", json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    open(os.path.join(HERE, "web", "forge.html"), "w", encoding="utf-8").write(body)

    page = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
            'body{margin:0}[hidden]{display:none!important}</style>\n'
            '</head>\n<body>\n' + body + '\n</body>\n</html>\n')
    os.makedirs(os.path.join(HERE, "docs"), exist_ok=True)
    open(os.path.join(HERE, "docs", "index.html"), "w", encoding="utf-8").write(page)
    print("wrote web/forge.html and docs/index.html")


if __name__ == "__main__":
    main()
