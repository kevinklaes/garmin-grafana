#!/usr/bin/env python3
"""Inventory the schema shape of a Garmin Connect data takeout (GDPR export).

This tool reads JSON files directly from an on-disk takeout export (a
directory tree such as ``~/Documents/garmintakeout/DI_CONNECT``) and reports,
for each distinct file-type group, the union of field paths and their
observed JSON value types (str/int/float/bool/null/list/dict).

It never prints or stores actual field *values* -- only field names, types,
and occurrence counts -- so its output is safe to read, share, or commit
without redacting personal data. It also never copies or writes the source
JSON files anywhere; it only opens them read-only to inspect their shape.

Usage:
    python inventory_takeout.py ~/Documents/garmintakeout/DI_CONNECT
    python inventory_takeout.py ~/Documents/garmintakeout/DI_CONNECT --samples 5
    python inventory_takeout.py ~/Documents/garmintakeout/DI_CONNECT --group AbnormalHrEvents
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict, Counter

# TLD is restricted to bare letters (no '_' or '-') so the match stops right
# after e.g. ".com" instead of swallowing a trailing "-comments"/"_gear"
# suffix that follows the TLD in filenames like "user@gmail.com-comments.json"
# or "user@gmail.com_gear.json".
_EMAIL_RE = re.compile(r"[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,6}", re.IGNORECASE)
_SEP_RE = re.compile(r"[_\-]+")
_PURE_DIGITS_RE = re.compile(r"^\d+$")

SKIP_EXTENSIONS = {".zip", ".png", ".jpg", ".jpeg", ".fit", ".pbf", ".gpx", ".tcx"}


def type_name(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def walk_schema(obj, path, schema, list_sample=5):
    """Recursively record {field_path: {observed types}} without keeping values."""
    schema[path].add(type_name(obj))
    if isinstance(obj, dict):
        for key, value in obj.items():
            # Some Garmin payloads use a dynamic id (e.g. activity/gear PK) as
            # a dict key rather than a fixed field name (e.g. gearActivityDTOs
            # in the gear export). Collapse those to one "<id>" placeholder so
            # the report shows one shape instead of one line per id.
            key_repr = "<id>" if key.isdigit() else key
            walk_schema(value, f"{path}.{key_repr}" if path else key_repr, schema, list_sample)
    elif isinstance(obj, list):
        # Sample a handful of elements -- enough to see the shape of
        # heterogeneous lists without scanning huge intraday arrays.
        for item in obj[:list_sample]:
            walk_schema(item, f"{path}[]", schema, list_sample)


def group_key(filename):
    """Collapse a filename to a stable group id shared by same-schema exports.

    E.g. 'ActivityVo2Max_20180712_20181020_7718497.json' and
    'ActivityVo2Max_20220512_20220820_7718497.json' both collapse to
    'ActivityVo2Max' so they're inventoried together as one schema.
    """
    stem = re.sub(r"\.json$", "", filename, flags=re.IGNORECASE)
    stem = _EMAIL_RE.sub("USER", stem)
    # Drop tokens that are purely numeric (dates split on '_'/'-', device/user
    # IDs, sequence numbers) -- they vary per export but don't change schema.
    tokens = [t for t in _SEP_RE.split(stem) if t and not _PURE_DIGITS_RE.match(t)]
    return "_".join(tokens) or filename


def collect_files(source_dir):
    """Group all .json files under source_dir by (relative_dir, group_key)."""
    groups = defaultdict(list)
    for root, _dirs, files in os.walk(source_dir):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext != ".json":
                continue
            rel_dir = os.path.relpath(root, source_dir)
            key = (rel_dir, group_key(fname))
            groups[key].append(os.path.join(root, fname))
    return groups


def pick_samples(paths, samples):
    """Evenly spaced picks across the sorted (i.e. chronological) file list.

    Early takeout files are often sparse (pre-dating a given watch/feature),
    so sampling only the first N under-represents the schema. Spreading picks
    across the full range -- always including the most recent file -- shows
    the fully evolved shape.
    """
    n = len(paths)
    if n <= samples:
        return paths
    indices = {round(i * (n - 1) / (samples - 1)) for i in range(samples)} if samples > 1 else {n - 1}
    return [paths[i] for i in sorted(indices)]


def inventory_group(paths, samples):
    """Sample a few files from a group and return (schema, sampled_count, errors)."""
    schema = defaultdict(set)
    sampled = pick_samples(paths, samples)
    errors = []
    for path in sampled:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as err:
            errors.append(f"{os.path.basename(path)}: {err}")
            continue
        walk_schema(data, "", schema)
    return schema, len(sampled), errors


def format_group_report(rel_dir, key, paths, schema, sampled_count, errors):
    lines = []
    lines.append(f"### {rel_dir}/{key}")
    lines.append(f"- files matching this schema: {len(paths)} (sampled {sampled_count})")
    if errors:
        lines.append(f"- parse errors: {len(errors)} (e.g. {errors[0]})")
    lines.append("- field paths (name: observed JSON types):")
    for field_path in sorted(schema):
        if field_path == "":
            continue
        types = "|".join(sorted(schema[field_path]))
        lines.append(f"    {field_path}: {types}")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source_dir", help="Path to takeout export directory (e.g. DI_CONNECT or DI_TACX)")
    parser.add_argument("--samples", type=int, default=3, help="Files to sample per schema group (default: 3)")
    parser.add_argument("--group", default=None, help="Only inventory groups whose key contains this substring")
    args = parser.parse_args()

    source_dir = os.path.expanduser(args.source_dir)
    if not os.path.isdir(source_dir):
        print(f"Not a directory: {source_dir}", file=sys.stderr)
        sys.exit(1)

    groups = collect_files(source_dir)
    counts = Counter({key: len(paths) for key, paths in groups.items()})

    print(f"# Takeout schema inventory for {source_dir}")
    print(f"# {len(groups)} distinct (dir, schema-group) combinations, {sum(counts.values())} total JSON files")
    print()

    for key in sorted(groups):
        rel_dir, group = key
        if args.group and args.group.lower() not in group.lower():
            continue
        paths = sorted(groups[key])
        schema, sampled_count, errors = inventory_group(paths, args.samples)
        print(format_group_report(rel_dir, group, paths, schema, sampled_count, errors))


if __name__ == "__main__":
    main()
