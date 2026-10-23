r"""Run a SQL file against EnerGov and optionally cache the result as CSV.

Examples:
    env\Scripts\python.exe scripts\run_query.py queries\example.sql
    env\Scripts\python.exe scripts\run_query.py queries\example.sql --overwrite
    env\Scripts\python.exe scripts\run_query.py queries\example.sql --output data\custom.csv
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from db_connect import TrustedSQLServerConnection


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "local-data"
DEFAULT_DOTENV = ROOT / ".env"


@dataclass(frozen=True)
class ExplorationQuery:
    name: str
    description: str
    sql: str


def project_path(path: Path) -> Path:
    """Resolve relative command-line paths from the project root."""
    return path if path.is_absolute() else ROOT / path


def load_env(env_file: Path = DEFAULT_DOTENV) -> tuple[str, str]:
    """Load the current or legacy SQL connection names without displaying them."""
    if env_file.is_file():
        load_dotenv(dotenv_path=env_file, override=False)

    server = (
        os.environ.get("ENERGOV_SQL_SERVER", "").strip()
        or os.environ.get("ENERGOVDB_SERVER", "").strip()
    )
    database = (
        os.environ.get("ENERGOV_SQL_DATABASE", "").strip()
        or os.environ.get("ENERGOVDB_DATABASE", "").strip()
    )
    missing = [
        name
        for name, value in (
            ("ENERGOV_SQL_SERVER", server),
            ("ENERGOV_SQL_DATABASE", database),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing SQL connection environment variable(s): "
            + ", ".join(missing)
        )
    return server, database


def load_query(sql_path: Path, description: str | None = None) -> ExplorationQuery:
    """Read a SQL file into an ExplorationQuery."""
    sql_path = project_path(sql_path)
    if not sql_path.is_file():
        raise FileNotFoundError(f"SQL file not found: {sql_path}")

    sql = sql_path.read_text(encoding="utf-8-sig").strip()
    if not sql:
        raise ValueError(f"SQL file is empty: {sql_path}")

    return ExplorationQuery(
        name=sql_path.stem,
        description=description or f"Query loaded from {sql_path}",
        sql=sql,
    )


def run_query(
    db: TrustedSQLServerConnection, query: ExplorationQuery
) -> pd.DataFrame:
    """Execute one exploration query and require a tabular result set."""
    print(f"\n{query.name}: {query.description}")
    df = db.execute_query(query.sql)
    if df is None:
        raise RuntimeError(f"Query returned no result set: {query.name}")
    print(f"Retrieved {len(df):,} rows")
    return df


def run_query_sets(
    db: TrustedSQLServerConnection, query: ExplorationQuery
) -> list[pd.DataFrame]:
    """Execute one SQL file and return every tabular result set."""
    print(f"\n{query.name}: {query.description}")
    result_sets = db.execute_query_sets(query.sql)
    if not result_sets:
        raise RuntimeError(f"Query returned no result sets: {query.name}")
    print(f"Retrieved {len(result_sets):,} result set(s)")
    for index, df in enumerate(result_sets, start=1):
        print(f"  Result {index}: {len(df):,} row(s), {len(df.columns):,} column(s)")
    return result_sets


def write_result_sets(
    result_sets: list[pd.DataFrame],
    output_dir: Path,
) -> list[Path]:
    """Write each result set to a numbered CSV and return the paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, df in enumerate(result_sets, start=1):
        path = output_dir / f"result-{index:02d}.csv"
        df.to_csv(path, index=False)
        paths.append(path)
        print(f"Saved result {index}: {path}")
    return paths


def get_data(
    query: ExplorationQuery, cache_path: Path, overwrite: bool
) -> pd.DataFrame:
    """Read a cached result or run the query and write a new cache."""
    if cache_path.is_file() and not overwrite:
        print(f"Using cached data: {cache_path}")
        df = pd.read_csv(cache_path)
        print(f"Loaded {len(df):,} rows")
        return df

    server, database = load_env()
    db = TrustedSQLServerConnection(server=server, database=database)
    try:
        db.connect()
        if db.connection is None:
            raise ConnectionError("Database connection could not be established")
        df = run_query(db, query)
    finally:
        db.close()

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False)
    print(f"Cached results: {cache_path}")
    return df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an EnerGov SQL file and cache its result as CSV."
    )
    parser.add_argument(
        "sql_file",
        type=Path,
        help="SQL file to run (relative paths are resolved from the project root)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="CSV cache path (default: local-data/<SQL filename>.csv)",
    )
    parser.add_argument(
        "--all-results",
        action="store_true",
        help=(
            "Preserve every tabular result set instead of only the first. "
            "Each result set is written to a numbered CSV."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Directory for --all-results output "
            "(default: local-data/<SQL filename>-results)"
        ),
    )
    parser.add_argument(
        "--overwrite",
        "--refresh",
        action="store_true",
        help="Run the query even when the cache already exists and replace it",
    )
    parser.add_argument(
        "--description",
        help="Optional description printed before the query runs",
    )
    parser.add_argument(
        "--preview-rows",
        type=int,
        default=5,
        metavar="N",
        help="Rows to print after loading (default: 5; use 0 to disable)",
    )
    args = parser.parse_args()
    if args.preview_rows < 0:
        parser.error("--preview-rows must be zero or greater")
    if args.output_dir is not None and not args.all_results:
        parser.error("--output-dir requires --all-results")
    if args.output is not None and args.all_results:
        parser.error("--output cannot be combined with --all-results; use --output-dir")
    return args


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")

    args = parse_args()
    query = load_query(args.sql_file, args.description)

    if args.all_results:
        server, database = load_env()
        db = TrustedSQLServerConnection(server=server, database=database)
        try:
            db.connect()
            if db.connection is None:
                raise ConnectionError(
                    "Database connection could not be established"
                )
            result_sets = run_query_sets(db, query)
        finally:
            db.close()

        output_dir = project_path(
            args.output_dir
            or (DEFAULT_DATA_DIR / f"{query.name}-results")
        )
        write_result_sets(result_sets, output_dir)

        if args.preview_rows:
            for index, df in enumerate(result_sets, start=1):
                print(f"\nResult {index} preview:")
                if df.empty:
                    print("(0 rows)")
                else:
                    print(
                        df.head(args.preview_rows).to_string(index=False)
                    )
        return

    cache_path = project_path(
        args.output or (DEFAULT_DATA_DIR / f"{query.name}.csv")
    )
    df = get_data(query, cache_path, args.overwrite)

    if args.preview_rows:
        print("\nPreview:")
        print(df.head(args.preview_rows).to_string(index=False))


if __name__ == "__main__":
    main()
