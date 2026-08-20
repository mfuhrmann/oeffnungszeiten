#!/usr/bin/env python3
"""
apply_global_settings.py — merge the managed global settings into a changedetection datastore.

changedetection has **no settings API**, reads `/datastore/changedetection.json` only at startup,
and overwrites it from memory on its next commit — so editing it while the app runs is invisible
and then lost. The only moment an edit survives is *before the app starts*. This runs there, as the
initContainer of the changedetection Deployment. (Watches are not affected: since 0.55.8 each is
its own `<uuid>/watch.json`, saved immediately and atomically.)

Merges only the keys in deploy/global-settings.json, leaving watches, tags and the API token
untouched. Idempotent — a second run reports "already current" and rewrites nothing.

  python3 scripts/apply_global_settings.py --datastore /datastore/changedetection.json
  python3 scripts/apply_global_settings.py --emit-values     # regenerate the Helm values

The emitted file is the ConfigMap the kustomization builds, so a regenerate is the whole change:
nothing is copied anywhere by hand.
"""
import argparse
import json
import os
import sys


def merge(ds_path, managed):
    cur = {}
    if os.path.exists(ds_path):
        try:
            with open(ds_path) as fh:
                cur = json.load(fh)
        except Exception:
            cur = {}
    cur.setdefault("settings", {})
    changed = []
    for section, values in managed.items():
        sec = cur["settings"].setdefault(section, {})
        for k, v in values.items():
            if sec.get(k) != v:
                sec[k] = v
                changed.append(f"{section}.{k}")
    if changed:
        tmp = ds_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(cur, fh, indent=2)
        os.replace(tmp, ds_path)
    return changed


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="Apply managed global settings")
    ap.add_argument("--settings", default=os.path.join(here, "deploy/global-settings.json"))
    ap.add_argument("--datastore", default="/datastore/changedetection.json")
    ap.add_argument("--emit-values", action="store_true",
                    help="regenerate apps/changedetection/global-settings.values.yaml for Helm")
    args = ap.parse_args()

    with open(args.settings) as fh:
        managed = json.load(fh)

    if args.emit_values:
        out = os.path.join(here, "apps/changedetection/global-settings.values.yaml")
        with open(out, "w") as fh:
            fh.write("# GENERATED from deploy/global-settings.json — do not edit by hand.\n"
                     "# Regenerate: python3 scripts/apply_global_settings.py --emit-values\n")
            json.dump({"globalSettings": managed}, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print(f"wrote {out}")
        return 0

    changed = merge(args.datastore, managed)
    print("applied: " + ", ".join(changed) if changed else "global settings already current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
