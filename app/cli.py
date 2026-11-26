"""Command line: `python -m app.cli reindex` rebuilds the search index (SPEC N10)."""

import argparse
import json

from app.config import get_settings
from app.db import init_db, make_engine
from app.store import reindex


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reindex", help="rebuild search_index from stored resources")
    parser.parse_args(argv)

    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    try:
        count = reindex(engine, settings.base_url)
    finally:
        engine.dispose()
    print(json.dumps({"reindexed": count}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
