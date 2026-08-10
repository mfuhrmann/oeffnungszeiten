# changedetection

[changedetection.io](https://changedetection.io) watching the websites of businesses in Fulda for
**opening-hours changes**, so a mapper learns that a shop changed its hours and can update
OpenStreetMap. Watches are not configured in the UI: each one is a file in `entries/` in this repository, and a
pull request adds, changes or removes one.

```
apps/changedetection/        # HelmRelease + base-values ConfigMap + namespace
charts/changedetection/      # Chart: changedetection, headless browser, PVC, Service, sync CronJob
```

The datastore is one JSON file rewritten in place plus thousands of small snapshot files, so it
needs block or local storage rather than a network share. No backup: snapshot history is
disposable — a re-created watch re-establishes its baseline without alerting. What must survive is
in git, plus the API token.

**No Ingress, on purpose.** changedetection has a single shared password and no user model, and an
authenticated user can point a watch at any URL — an exposed instance is an SSRF pivot into the
cluster.

```bash
kubectl -n changedetection port-forward svc/changedetection 5000:5000
```

**One replica.** Two processes on one datastore corrupt it; `strategy: Recreate` keeps the old pod
from overlapping the new one.

## The sync CronJob

It clones the watch repository and reconciles changedetection against the entry files. It pulls
rather than being pushed, because nothing is exposed inbound and CI therefore cannot reach the app.

Its checkout is thrown away after every run, so the slug-to-uuid mapping cannot be a file in it:
`entries_sync.py` derives the mapping instead — URL first, name against title where one URL carries
two businesses — which is what makes an hourly job safe. A stale or missing `entries/.lock.json`
is adopted, not duplicated.

## Notifications

A relay next to changedetection owns the Matrix session, because Apprise cannot hold one against a
MAS homeserver. See [notifications.md](./notifications.md).

## Managed global settings

`deploy/global-settings.json` is the source for the noise-suppression patterns and the recheck
interval. An initContainer merges it into the datastore before the app starts, because
changedetection reads that file only at startup and overwrites it from memory afterwards.

The chart receives the same values through the base-values ConfigMap, so there are two copies.
CI compares them on every change; regenerate with

```bash
python3 scripts/apply_global_settings.py --emit-values
```

and copy the `globalSettings` block across.

## Secrets

The API token is mounted into the sync CronJob. Create it in-cluster, never in git:

```bash
kubectl -n changedetection create secret generic changedetection-api \
    --from-literal=api-key='<token from the UI: Settings → API>'
```
