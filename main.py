import asyncio

from dotenv import load_dotenv

from db import get_connection, init_db
from scraper import run_scrapers
from matcher import run_matcher
from notify import run_notifier

load_dotenv()


async def main() -> None:
    conn = get_connection()
    init_db(conn)

    print("=== Step 1: Scraping ===")
    await run_scrapers(conn)

    print("\n=== Step 2: Matching ===")
    run_matcher(conn)

    print("\n=== Step 3: Notifying ===")
    run_notifier(conn)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
