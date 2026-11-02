"""
CLI entry point for the flood data pipeline scrapers.

Usage:
    # From api/ directory with DATABASE_SYNC_URL set:
    python -m scrapers.run_scrapers --source official
    python -m scrapers.run_scrapers --source news
    python -m scrapers.run_scrapers --source all
    python -m scrapers.run_scrapers --source news --max-pages 3

The scrape_log table must exist (run `alembic upgrade head` first).
Each run creates a scrape_log row: started_at, finished_at, records_added, errors.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from api.scrapers.news import ALL_SOURCES, scrape_news_source
from api.scrapers.official import scrape_official

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _check_anthropic_key_if_needed(source: str) -> None:
    """Fail fast with a helpful message if ANTHROPIC_API_KEY is missing for news scraper."""
    if source in ("news", "all") and not os.environ.get("ANTHROPIC_API_KEY"):
        if os.environ.get("FLOOD_SCRAPER_MOCK_LLM"):
            return  # mock mode — no real key needed
        raise RuntimeError(
            "ANTHROPIC_API_KEY is required for --source news/all.\n"
            "  Get a key: https://console.anthropic.com/\n"
            "  Add to .env: ANTHROPIC_API_KEY=sk-ant-...\n"
            "  Or skip LLM calls with: FLOOD_SCRAPER_MOCK_LLM=1"
        )


def _get_db_url() -> str:
    url = os.environ.get("DATABASE_SYNC_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_SYNC_URL environment variable not set. "
            "Example: postgresql+psycopg2://flood:flood@localhost:5432/flooddb"
        )
    return url


def _check_scrape_log_exists(conn) -> None:
    result = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'scrape_log'"
        )
    ).fetchone()
    if not result:
        raise RuntimeError(
            "scrape_log table not found. Run `alembic upgrade head` first."
        )


def _log_run(
    conn,
    source: str,
    records_added: int,
    errors: list[str],
    started_at: datetime,
) -> None:
    status = "success" if not errors else ("partial" if records_added > 0 else "failed")
    conn.execute(
        text("""
            INSERT INTO scrape_log (source, started_at, finished_at, records_added, errors, status)
            VALUES (:source, :started_at, NOW(), :records_added, :errors, :status)
        """),
        {
            "source": source,
            "started_at": started_at,
            "records_added": records_added,
            "errors": json.dumps(errors) if errors else None,
            "status": status,
        },
    )


def run_official(conn, started_at: datetime, dry_run: bool = False, dump_html: str | None = None) -> int:
    logger.info("Starting official scraper (thoatnuochanoi.vn)%s...", " [DRY RUN]" if dry_run else "")
    records, errors = scrape_official(conn, dump_html=dump_html)
    if not dry_run:
        _log_run(conn, "official", records, errors, started_at)
    logger.info("Official scraper done: %d inserted, %d errors", records, len(errors))
    for err in errors:
        logger.warning("  ERROR: %s", err)
    return records


def run_news(conn, started_at: datetime, max_pages: int, dry_run: bool = False) -> int:
    total = 0
    for source in ALL_SOURCES:
        logger.info(
            "Starting %s scraper%s...", source.name, " [DRY RUN]" if dry_run else ""
        )
        src_started = datetime.now(tz=timezone.utc)
        records, errors = scrape_news_source(source, conn, max_pages=max_pages, dry_run=dry_run)
        if not dry_run:
            _log_run(conn, source.name, records, errors, src_started)
        logger.info("%s done: %d inserted, %d errors", source.name, records, len(errors))
        for err in errors:
            logger.warning("  ERROR: %s", err)
        total += records
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Flood data pipeline scrapers")
    parser.add_argument(
        "--source",
        choices=["official", "news", "all"],
        required=True,
        help="Which scraper(s) to run",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="Max list pages to fetch per news source (default: 10)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Fetch and parse without writing to DB or calling LLM. "
            "Prints what would be inserted. Safe to run without ANTHROPIC_API_KEY."
        ),
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Set log level to DEBUG (shows per-article progress, ward lookups, etc.)",
    )
    parser.add_argument(
        "--dump-html",
        metavar="PATH",
        help=(
            "Dump the raw fetched HTML to PATH (for offline JS-rendering diagnosis). "
            "Only applies to --source official. Useful when the scraper returns 0 rows "
            "and you suspect AJAX rendering."
        ),
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        _check_anthropic_key_if_needed(args.source)
        db_url = _get_db_url()
    except RuntimeError as exc:
        logger.error(str(exc))
        sys.exit(1)

    engine = create_engine(db_url)
    started_at = datetime.now(tz=timezone.utc)
    total_inserted = 0

    try:
        with engine.begin() as conn:
            _check_scrape_log_exists(conn)

            if args.source in ("official", "all"):
                total_inserted += run_official(
                    conn, started_at, dry_run=args.dry_run,
                    dump_html=getattr(args, "dump_html", None),
                )

            if args.source in ("news", "all"):
                total_inserted += run_news(conn, started_at, args.max_pages, dry_run=args.dry_run)

    except RuntimeError as exc:
        logger.error(str(exc))
        sys.exit(1)
    finally:
        engine.dispose()

    logger.info("Pipeline complete. Total inserted: %d", total_inserted)


if __name__ == "__main__":
    main()
