from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from project_public_catalog import (
    DEFAULT_MINIMUM_SAMPLE_SIZE,
    PUBLIC_SCHEMA_VERSION,
    load_json,
    project_catalog,
)


DEFAULT_CATALOG = "output-prod/catalog"
DEFAULT_DESTINATION = "docs/data"


def _option_sources(catalog_dir, manifest):
    catalog_dir = Path(catalog_dir)
    options = list(manifest.get("options") or [])
    if not options:
        raise ValueError("Catalog manifest contains no options.")

    sources = []
    seen = set()
    for item in options:
        relative = Path(str(item.get("path") or ""))
        if relative.parent != Path("options") or relative.suffix != ".json":
            raise ValueError(f"Unexpected catalog option path: {relative}")
        source = catalog_dir / relative
        if source.resolve().parent != catalog_dir.resolve() / "options":
            raise ValueError(f"Catalog option path leaves the options directory: {relative}")
        if relative.as_posix().casefold() in seen:
            raise ValueError(f"Duplicate catalog option path: {relative}")
        seen.add(relative.as_posix().casefold())
        if not source.is_file():
            raise FileNotFoundError(f"Catalog option was not found: {source}")
        sources.append((relative, source))
    return sources


def _copy_public_catalog(catalog_dir, destination, manifest, *, replace=False):
    catalog_dir = Path(catalog_dir)
    destination = Path(destination)
    sources = _option_sources(catalog_dir, manifest)
    if destination.exists() and not replace:
        raise FileExistsError(f"Data directory already exists: {destination}. Use the reviewed promotion workflow.")
    staging = destination.with_name(destination.name + ".tmp")
    if staging.exists():
        raise FileExistsError(f"Temporary data directory already exists: {staging}")
    (staging / "options").mkdir(parents=True)

    shutil.copy2(catalog_dir / "manifest.json", staging / "manifest.json")
    for relative, source in sources:
        shutil.copy2(source, staging / relative)

    if destination.exists():
        shutil.rmtree(destination)
    staging.replace(destination)
    return {
        "catalogVersion": manifest.get("catalogVersion"),
        "configurationAsOfDate": manifest.get("configurationAsOfDate"),
        "options": len(sources),
        "destination": str(destination),
        "minimumSampleSize": manifest.get("historyMinimumSampleSize"),
    }


def publish_catalog(
    catalog_dir,
    destination,
    *,
    minimum_sample_size=DEFAULT_MINIMUM_SAMPLE_SIZE,
    replace=False,
):
    catalog_dir = Path(catalog_dir)
    destination = Path(destination)
    source_root, target_root = catalog_dir.resolve(), destination.resolve()
    if source_root.is_relative_to(target_root) or target_root.is_relative_to(source_root):
        raise ValueError("Catalog source and destination directories must not overlap.")
    manifest_path = catalog_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Catalog manifest was not found: {manifest_path}"
        )

    manifest = load_json(manifest_path)
    _option_sources(catalog_dir, manifest)
    if manifest.get("schemaVersion") == PUBLIC_SCHEMA_VERSION:
        return _copy_public_catalog(catalog_dir, destination, manifest, replace=replace)
    return project_catalog(
        catalog_dir,
        destination,
        minimum_sample_size=minimum_sample_size,
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Build and publish the reduced estimator data package into the "
            "tracked static-site directory."
        )
    )
    parser.add_argument(
        "catalog_dir",
        nargs="?",
        default=DEFAULT_CATALOG,
    )
    parser.add_argument(
        "--destination",
        default=DEFAULT_DESTINATION,
    )
    parser.add_argument(
        "--minimum-sample-size",
        type=int,
        default=DEFAULT_MINIMUM_SAMPLE_SIZE,
    )
    args = parser.parse_args()

    result = publish_catalog(
        args.catalog_dir,
        args.destination,
        minimum_sample_size=args.minimum_sample_size,
    )
    print("PUBLIC ESTIMATOR DATA PUBLISHED")
    print(f"Catalog version: {result['catalogVersion']}")
    print(
        "Configuration snapshot: "
        f"{result['configurationAsOfDate']}"
    )
    print(f"Options: {result['options']}")
    print(f"Minimum history sample: {result['minimumSampleSize']}")
    print(f"Destination: {result['destination']}")


if __name__ == "__main__":
    main()
