"""
scraper.py — Job board scrapers using Playwright.

Each scraper's scrape() method returns basic listings (title, company, url).
fetch_description(url) visits the individual job page for the full text.
main.py calls scrape() first, filters out already-known URLs, then calls
fetch_description() only for new jobs to avoid redundant page loads.
"""

import asyncio
import os
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import quote

import yaml
from playwright.async_api import async_playwright, Page, Browser

WATCHLIST_PATH = Path(__file__).parent / "watchlist.yaml"
PAGE_DELAY_MS = 2000   # ms to wait after navigation for JS to settle
MAX_PAGES = 5          # safety cap on pagination loops

# ---------------------------------------------------------------------------
# Relevance filter — applied before inserting to DB
# ---------------------------------------------------------------------------

_RELEVANT = {
    "engineer", "engineering", "developer", "programmer", "scientist",
    "researcher", "algorithm", "signal", "dsp", "radar", "sonar",
    "embedded", "firmware", "hardware", "software", "system", "systems",
    "data", "machine learning", "deep learning", "computer vision",
    "cyber", "security", "architect", "technical", "r&d",
    "rf", "fpga", "asic", "vlsi", "pcb", "electronics", "optical",
    "sensor", "detection", "tracking", "devops", "cloud", "platform",
    "מהנדס", "פיתוח", "מפתח", "מדען", "תוכנה", "חומרה", "אלגוריתם",
}

_IRRELEVANT = {
    "sales", "salesperson", "account executive", "account manager",
    "personal assistant", "office manager", "administrative", "admin",
    "marketing", "content writer", "copywriter", "social media",
    "human resources", " hr ", "recruiter", "talent acquisition",
    "accountant", "bookkeeper", "finance", "financial controller",
    "lawyer", "legal", "paralegal", "driver", "delivery", "cleaner",
}


def is_relevant_title(title: str) -> bool:
    """Return True only if the title looks like a tech/engineering role."""
    lower = f" {title.lower()} "
    if any(kw in lower for kw in _IRRELEVANT):
        return False
    return any(kw in lower for kw in _RELEVANT)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class JobScraper(ABC):
    NAME: str = ""
    BASE_URL: str = ""

    @abstractmethod
    async def scrape(self, page: Page) -> list[dict]:
        """Return list of {"title": str, "company": str, "url": str}."""

    async def fetch_description(self, page: Page, url: str) -> str:
        """
        Navigate to a job page and return its full text.
        Override in subclasses for site-specific selectors.
        """
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(PAGE_DELAY_MS)
            for selector in ["[class*='description']", "[class*='content']",
                             "[class*='job-detail']", "article", "main"]:
                el = await page.query_selector(selector)
                if el:
                    text = (await el.inner_text()).strip()
                    if len(text) > 150:
                        return text
            return (await page.inner_text("body")).strip()
        except Exception as e:
            return f"[Description fetch failed: {e}]"


# ---------------------------------------------------------------------------
# Goozali  (https://en.goozali.com)
# ---------------------------------------------------------------------------

class GoozaliScraper(JobScraper):
    NAME = "Goozali"
    BASE_URL = "https://en.goozali.com"
    # Direct Airtable share view for job openings (discovered from page source)
    AIRTABLE_JOBS_URL = "https://airtable.com/shrQBuWjXd0YgPqV6"

    # JS to extract visible jobs from Airtable's virtual-scroll grid.
    _EXTRACT_JS = """
    () => {
        function colCells(headerText) {
            const all = [...document.querySelectorAll("[class*='cell']")];
            const header = all.find(c => c.innerText.trim() === headerText);
            if (!header) return [];
            const left = header.style.left;
            return all
                .filter(c => c.style.left === left && c !== header)
                .map(c => c.innerText.trim())
                .filter(t => t && t !== 'Summary');
        }
        return {
            titles:    colCells('Job Title'),
            companies: colCells('Company'),
            urls:      colCells('Position Link'),
        };
    }
    """

    async def scrape(self, page: Page) -> list[dict]:
        """
        Navigate directly to the Airtable share URL and scroll through the
        virtual-scroll grid, extracting visible rows on each pass.
        Caps at 300 jobs to keep run time reasonable.
        """
        MAX_JOBS = 300
        SCROLL_STEP = 2000   # px per step
        SCROLL_PAUSE = 1500  # ms for new rows to render

        try:
            await page.goto(self.AIRTABLE_JOBS_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            pass

        await page.wait_for_timeout(2000)
        try:
            await page.click("#onetrust-accept-btn-handler", timeout=4000)
            print(f"  [{self.NAME}] Accepted OneTrust consent")
        except Exception:
            pass
        await page.wait_for_timeout(8000)   # wait for grid to fully render

        seen_urls: set[str] = set()
        jobs: list[dict] = []

        scroll_pos = 0
        no_new_streak = 0

        while len(jobs) < MAX_JOBS:
            data = await page.evaluate(self._EXTRACT_JS)
            titles    = data.get("titles", [])
            companies = data.get("companies", [])
            urls      = data.get("urls", [])

            new_this_pass = 0
            for i, url in enumerate(urls):
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    jobs.append({
                        "title":   titles[i]    if i < len(titles)    else "",
                        "company": companies[i] if i < len(companies) else "",
                        "url":     url,
                    })
                    new_this_pass += 1

            if new_this_pass == 0:
                no_new_streak += 1
                if no_new_streak >= 3:
                    break
            else:
                no_new_streak = 0

            scroll_pos += SCROLL_STEP
            scrolled = await page.evaluate(
                f"() => {{ const el = document.querySelector('.antiscroll-inner');"
                f" if (el) el.scrollTop = {scroll_pos}; return el ? el.scrollTop : -1; }}"
            )
            if scrolled < 0:
                break
            await page.wait_for_timeout(SCROLL_PAUSE)

        print(f"[{self.NAME}] Found {len(jobs)} listings.")
        return jobs

    async def fetch_description(self, page: Page, url: str) -> str:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(PAGE_DELAY_MS)
        for selector in ["[class*='description']", "[class*='details']", "main", "article"]:
            el = await page.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                if len(text) > 150:
                    return text
        return (await page.inner_text("body")).strip()


# ---------------------------------------------------------------------------
# Drushim  (https://www.drushim.co.il)
# ---------------------------------------------------------------------------

class DrushimScraper(JobScraper):
    NAME = "Drushim"
    BASE_URL = "https://www.drushim.co.il"
    CATEGORY_URLS = [
        "https://www.drushim.co.il/jobs/cat19/",  # High-tech — Software
        "https://www.drushim.co.il/jobs/cat20/",  # High-tech — Hardware
    ]

    async def scrape(self, page: Page) -> list[dict]:
        jobs = []
        for cat_url in self.CATEGORY_URLS:
            await page.goto(cat_url, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(PAGE_DELAY_MS)
            page_jobs = await self._extract_from_nuxt(page)
            if not page_jobs:
                page_jobs = await self._extract_from_dom(page)
            jobs.extend(page_jobs)
        print(f"[{self.NAME}] Found {len(jobs)} listings.")
        return jobs

    async def _extract_from_nuxt(self, page: Page) -> list[dict]:
        """Extract job data from the window.__NUXT__ pre-rendered state."""
        try:
            raw = await page.evaluate("""
                () => {
                    const n = window.__NUXT__;
                    if (!n) return [];
                    const fetchState = n.fetch || {};
                    for (const key of Object.keys(fetchState)) {
                        if (fetchState[key] && fetchState[key].searchRes) {
                            return fetchState[key].searchRes;
                        }
                    }
                    // Fallback: walk data array
                    const data = n.data || [];
                    for (const d of data) {
                        if (d && d.searchRes) return d.searchRes;
                    }
                    return [];
                }
            """)
        except Exception:
            return []

        jobs = []
        for item in (raw or []):
            try:
                content = item.get("JobContent") or {}
                company = item.get("Company") or {}
                cv_model = item.get("SendCVButtonModel") or {}

                title = content.get("Name") or content.get("FullName") or ""
                company_name = company.get("CompanyDisplayName") or ""
                job_code = content.get("JobCode") or ""
                link = cv_model.get("ButtonLink") or cv_model.get("ExternalLink") or ""

                if not link and job_code:
                    link = f"/job/{job_code}/"

                if title and link:
                    url = link if link.startswith("http") else f"{self.BASE_URL}{link}"
                    jobs.append({"title": title.strip(), "company": company_name.strip(), "url": url})
            except Exception:
                continue
        return jobs

    async def _extract_from_dom(self, page: Page) -> list[dict]:
        """DOM fallback if __NUXT__ extraction fails."""
        jobs = []
        cards = await page.query_selector_all(".job-item, [class*='job-item']")
        for card in cards:
            try:
                title_el = await card.query_selector(".job-title a, [class*='job-title'] a, h3 a")
                company_el = await card.query_selector("[class*='company'], [class*='employer']")
                link_el = await card.query_selector("a[href*='/job/']")

                title = (await title_el.inner_text()).strip() if title_el else ""
                company = (await company_el.inner_text()).strip() if company_el else ""
                href = await link_el.get_attribute("href") if link_el else ""

                if title and href:
                    url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                    jobs.append({"title": title, "company": company, "url": url})
            except Exception:
                continue
        return jobs

    async def fetch_description(self, page: Page, url: str) -> str:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(PAGE_DELAY_MS)
        for selector in [
            ".job-description", "[class*='job-description']",
            "[class*='description']", "[class*='job-content']", "article",
        ]:
            el = await page.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                if len(text) > 150:
                    return text
        return (await page.inner_text("body")).strip()


# ---------------------------------------------------------------------------
# JobMaster  (https://www.jobmaster.co.il)
# ---------------------------------------------------------------------------

class JobMasterScraper(JobScraper):
    NAME = "JobMaster"
    BASE_URL = "https://www.jobmaster.co.il"
    CATEGORY_URLS = [
        "https://www.jobmaster.co.il/jobs/?CatId=48",  # Computers / Software
        "https://www.jobmaster.co.il/jobs/?CatId=23",  # Electronics / Hardware
    ]

    async def scrape(self, page: Page) -> list[dict]:
        jobs = []
        for base_url in self.CATEGORY_URLS:
            for page_num in range(1, MAX_PAGES + 1):
                url = f"{base_url}&page={page_num}" if page_num > 1 else base_url
                await page.goto(url, wait_until="networkidle", timeout=60_000)
                await page.wait_for_timeout(PAGE_DELAY_MS)

                page_jobs = await self._extract_jobs(page)
                if not page_jobs:
                    break
                jobs.extend(page_jobs)

                # Stop if no "next page" indicator
                has_next = await page.query_selector(
                    "a.next, [class*='next-page'], [aria-label='Next page'], [rel='next']"
                )
                if not has_next:
                    break

        print(f"[{self.NAME}] Found {len(jobs)} listings.")
        return jobs

    async def _extract_jobs(self, page: Page) -> list[dict]:
        jobs = []
        try:
            await page.wait_for_selector(
                "[class*='jobCard'], [class*='job-card'], [class*='JobCard'], [class*='job_card'], "
                "a[href*='/job/']",
                timeout=10_000,
            )
        except Exception:
            return jobs

        # Try structured cards first
        cards = await page.query_selector_all(
            "[class*='jobCard'], [class*='job-card'], [class*='JobCard'], [class*='job_card']"
        )

        if cards:
            for card in cards:
                try:
                    title_el = await card.query_selector(
                        "h2, h3, [class*='title'], [class*='Title'], a[href*='/job/']"
                    )
                    company_el = await card.query_selector(
                        "[class*='company'], [class*='Company'], [class*='employer']"
                    )
                    link_el = await card.query_selector("a[href*='/job/']")

                    title = (await title_el.inner_text()).strip() if title_el else ""
                    company = (await company_el.inner_text()).strip() if company_el else ""
                    href = await link_el.get_attribute("href") if link_el else ""

                    if title and href:
                        url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                        jobs.append({"title": title, "company": company, "url": url})
                except Exception:
                    continue
        else:
            # Fallback: collect all job links directly
            links = await page.query_selector_all("a[href*='/job/']")
            seen = set()
            for link in links:
                href = await link.get_attribute("href")
                text = (await link.inner_text()).strip()
                if href and text and href not in seen:
                    seen.add(href)
                    url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                    jobs.append({"title": text, "company": "", "url": url})

        return jobs

    async def fetch_description(self, page: Page, url: str) -> str:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(PAGE_DELAY_MS)
        for selector in [
            "[class*='job-description']", "[class*='jobDescription']",
            "[class*='description']", "[class*='content']", "article", "main",
        ]:
            el = await page.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                if len(text) > 150:
                    return text
        return (await page.inner_text("body")).strip()


# ---------------------------------------------------------------------------
# AllJobs  (https://www.alljobs.co.il)
# ---------------------------------------------------------------------------

class AllJobsScraper(JobScraper):
    NAME = "AllJobs"
    BASE_URL = "https://www.alljobs.co.il"
    # Keywords relevant to Eyal's profile. Playwright handles the Radware challenge.
    SEARCH_TERMS = [
        "algorithm",
        "signal processing",
        "RADAR",
        "DSP",
        "embedded systems",
        "אלגוריתם",
        "עיבוד אותות",
    ]

    async def scrape(self, page: Page) -> list[dict]:
        seen_urls: set[str] = set()
        jobs: list[dict] = []

        for term in self.SEARCH_TERMS:
            encoded = quote(term)
            url = (
                f"{self.BASE_URL}/SearchResultsGuest.aspx"
                f"?source=0&type=1&region=0&position={encoded}&sector=0"
            )
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            # Extra wait for Radware bot challenge
            await page.wait_for_timeout(4000)

            page_jobs = await self._extract_jobs(page)
            for job in page_jobs:
                if job["url"] not in seen_urls:
                    seen_urls.add(job["url"])
                    jobs.append(job)

        print(f"[{self.NAME}] Found {len(jobs)} unique listings.")
        return jobs

    async def _extract_jobs(self, page: Page) -> list[dict]:
        jobs = []
        try:
            await page.wait_for_selector(
                "[class*='job'], [class*='Job'], a[href*='SingleJob']",
                timeout=15_000,
            )
        except Exception:
            return jobs

        # AllJobs job links typically point to /SingleJob.aspx or similar
        cards = await page.query_selector_all(
            "[class*='job-item'], [class*='JobItem'], [class*='single-job'], "
            "[class*='job_item'], li[class*='job']"
        )

        if cards:
            for card in cards:
                try:
                    title_el = await card.query_selector(
                        "h2, h3, [class*='title'], [class*='Title']"
                    )
                    company_el = await card.query_selector(
                        "[class*='company'], [class*='employer'], [class*='Company']"
                    )
                    link_el = await card.query_selector("a[href]")

                    title = (await title_el.inner_text()).strip() if title_el else ""
                    company = (await company_el.inner_text()).strip() if company_el else ""
                    href = await link_el.get_attribute("href") if link_el else ""

                    if title and href:
                        url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                        jobs.append({"title": title, "company": company, "url": url})
                except Exception:
                    continue
        else:
            # Fallback: all links that look like job pages
            links = await page.query_selector_all(
                "a[href*='SingleJob'], a[href*='/job/'], a[href*='JobId=']"
            )
            seen = set()
            for link in links:
                href = await link.get_attribute("href")
                text = (await link.inner_text()).strip()
                if href and text and href not in seen and len(text) > 3:
                    seen.add(href)
                    url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                    jobs.append({"title": text, "company": "", "url": url})

        return jobs

    async def fetch_description(self, page: Page, url: str) -> str:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(PAGE_DELAY_MS)
        for selector in [
            "[class*='job-description']", "[class*='description']",
            "[class*='content']", "article", "main",
        ]:
            el = await page.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                if len(text) > 150:
                    return text
        return (await page.inner_text("body")).strip()


# ---------------------------------------------------------------------------
# Wellfound  (https://wellfound.com)
# ---------------------------------------------------------------------------

class WellfoundScraper(JobScraper):
    NAME = "Wellfound"
    BASE_URL = "https://wellfound.com"
    JOBS_URL = "https://wellfound.com/location/israel"

    async def _login(self, page: Page) -> bool:
        email = os.environ.get("WELLFOUND_EMAIL", "")
        password = os.environ.get("WELLFOUND_PASSWORD", "")
        if not email or not password:
            print(f"[{self.NAME}] No credentials found in .env, skipping login.")
            return False

        await page.goto("https://wellfound.com/login", wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(PAGE_DELAY_MS)

        try:
            await page.fill("input[name='user[email]'], input[type='email']", email)
            await page.fill("input[name='user[password]'], input[type='password']", password)
            await page.click("input[type='submit'], button[type='submit']")
            await page.wait_for_timeout(3000)
            return "login" not in page.url
        except Exception as e:
            print(f"[{self.NAME}] Login error: {e}")
            return False

    async def scrape(self, page: Page) -> list[dict]:
        await self._login(page)

        await page.goto(self.JOBS_URL, wait_until="networkidle", timeout=60_000)
        await page.wait_for_timeout(PAGE_DELAY_MS)

        jobs = await self._extract_jobs(page)
        print(f"[{self.NAME}] Found {len(jobs)} listings.")
        return jobs

    async def _extract_jobs(self, page: Page) -> list[dict]:
        jobs = []
        try:
            await page.wait_for_selector(
                "[class*='JobCard'], [class*='job-card'], [data-test*='Job'], "
                "a[href*='/jobs/']",
                timeout=15_000,
            )
        except Exception:
            return jobs

        cards = await page.query_selector_all(
            "[class*='JobCard'], [class*='job-card'], [data-test='JobCard']"
        )

        if not cards:
            # Fallback to direct job links
            links = await page.query_selector_all("a[href*='/jobs/']")
            seen = set()
            for link in links:
                href = await link.get_attribute("href")
                text = (await link.inner_text()).strip()
                if href and text and href not in seen:
                    seen.add(href)
                    url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                    jobs.append({"title": text, "company": "", "url": url})
            return jobs

        for card in cards:
            try:
                title_el = await card.query_selector(
                    "h2, h3, [class*='title'], [class*='Title'], [class*='role']"
                )
                company_el = await card.query_selector(
                    "[class*='company'], [class*='startup'], [class*='Company']"
                )
                link_el = await card.query_selector("a[href*='/jobs/']")

                title = (await title_el.inner_text()).strip() if title_el else ""
                company = (await company_el.inner_text()).strip() if company_el else ""
                href = await link_el.get_attribute("href") if link_el else ""

                if title and href:
                    url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                    jobs.append({"title": title, "company": company, "url": url})
            except Exception:
                continue

        return jobs

    async def fetch_description(self, page: Page, url: str) -> str:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(PAGE_DELAY_MS)
        for selector in [
            "[class*='description']", "[class*='job-detail']",
            "[class*='content']", "article", "main",
        ]:
            el = await page.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                if len(text) > 150:
                    return text
        return (await page.inner_text("body")).strip()


# ---------------------------------------------------------------------------
# WatchlistScraper  (reads watchlist.yaml)
# ---------------------------------------------------------------------------

class WatchlistScraper(JobScraper):
    NAME = "Watchlist"
    BASE_URL = ""

    async def scrape(self, page: Page) -> list[dict]:
        if not WATCHLIST_PATH.exists():
            print(f"[{self.NAME}] watchlist.yaml not found.")
            return []

        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            watchlist = yaml.safe_load(f) or {}

        companies = watchlist.get("companies", [])
        jobs: list[dict] = []

        for entry in companies:
            name = (entry.get("name") or "").strip()
            url = (entry.get("job_board_url") or "").strip()
            if not name:
                continue

            if url:
                company_jobs = await self._scrape_board(page, name, url)
            else:
                company_jobs = await self._search_linkedin(page, name)

            for j in company_jobs:
                j.setdefault("source", name)
            print(f"  [{self.NAME}] {name}: {len(company_jobs)} listings")
            jobs.extend(company_jobs)

        print(f"[{self.NAME}] Total: {len(jobs)} listings.")
        return jobs

    @staticmethod
    def _resolve_url(href: str, base: str) -> str:
        """
        Resolve a possibly-relative href against base.
        Absolute-path hrefs (starting with /) are resolved against the origin only,
        not the full base path — preventing double-path bugs like /jobs/jobs/...
        """
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            from urllib.parse import urlparse
            p = urlparse(base)
            return f"{p.scheme}://{p.netloc}{href}"
        return f"{base.rstrip('/')}/{href.lstrip('/')}"

    @staticmethod
    def _looks_like_job_page(href: str) -> bool:
        """
        Return True only if a URL looks like an individual job page,
        not a category filter, nav link, or careers homepage.
        Individual job URLs typically contain a job ID (numeric, UUID, or slug)
        at a depth of at least 2 path segments.
        """
        import re
        from urllib.parse import urlparse
        parsed = urlparse(href)
        path = parsed.path.rstrip("/")
        segments = [s for s in path.split("/") if s]
        # Must be at least 2 path segments deep (e.g. /jobs/senior-engineer, not just /jobs)
        if len(segments) < 2:
            return False
        last = segments[-1]
        # Reject pure filter/category paths (only letters/hyphens, no digits or UUID)
        if re.fullmatch(r"[a-zA-Z\-]+", last) and len(last) < 20:
            return False
        # Accept if last segment has digits, UUID pattern, or is long (slug)
        return bool(re.search(r"\d", last) or re.fullmatch(r"[0-9a-f\-]{20,}", last) or len(last) >= 20)

    async def _scrape_google_careers(self, page: Page, company: str) -> list[dict]:
        """
        Google Careers server-renders job cards into the HTML.
        Each card contains a <div jsdata="Aiqs8c;{job_id};$N"> attribute
        from which we extract the job ID and title via DOM queries.
        """
        jobs: list[dict] = []
        seen: set[str] = set()
        _BASE = "https://www.google.com/about/careers/applications"

        landing = (
            f"{_BASE}/jobs/results?location=Israel"
            "&target_level=ADVANCED&target_level=MID&target_level=EARLY"
            "&degree=BACHELORS&employment_type=FULL_TIME"
        )
        try:
            await page.goto(landing, wait_until="networkidle", timeout=60_000)
        except Exception:
            pass

        # ---------------------------------------------------------------
        # Extract jobs from server-rendered DOM.
        # Google renders job cards as <div jsdata="Aiqs8c;{job_id};$N">.
        # ---------------------------------------------------------------
        async def _extract_page_jobs() -> int:
            items = await page.evaluate("""
                () => {
                    const results = [];
                    const seenIds = new Set();
                    document.querySelectorAll('[jsdata*="Aiqs8c;"]').forEach(card => {
                        const jsdata = card.getAttribute('jsdata') || '';
                        const m = jsdata.match(/Aiqs8c;(\\d+)/);
                        if (!m) return;
                        const jobId = m[1];
                        if (seenIds.has(jobId)) return;
                        seenIds.add(jobId);
                        const link = card.querySelector('a[href*="/jobs/results/"]');
                        let title = link ? link.innerText.trim() : '';
                        if (!title) {
                            const h = card.querySelector('h3, h2, [role="heading"]');
                            title = h ? h.innerText.trim() : '';
                        }
                        if (!title)
                            title = card.innerText.trim().split('\\n')[0].trim();
                        if (jobId && title) results.push({jobId, title});
                    });
                    return results;
                }
            """)
            added = 0
            for item in items:
                job_id = item.get("jobId", "")
                title = item.get("title", "").strip()
                if not title or not job_id:
                    continue
                url = f"{_BASE}/jobs/results/{job_id}"
                if url not in seen:
                    seen.add(url)
                    jobs.append({"title": title, "company": company, "url": url})
                    added += 1
            return added

        await _extract_page_jobs()

        # Paginate via URL ?page=N — each page is server-rendered independently
        for page_num in range(2, 10):
            try:
                await page.goto(
                    landing + f"&page={page_num}",
                    wait_until="networkidle", timeout=40_000,
                )
            except Exception:
                pass
            if await _extract_page_jobs() == 0:
                break

        return jobs

    async def _scrape_board(self, page: Page, company: str, url: str) -> list[dict]:
        """
        Strategy (in order):
        1. Google Careers dedicated handler.
        2. Intercept JSON API responses — works for custom backends (Mobileye) and most modern ATS.
        3. ATS-specific DOM selectors (Greenhouse, Workday, Lever, SmartRecruiters).
        4. All links that pass the individual-job-page URL heuristic.
        """
        if "google.com/about/careers" in url:
            return await self._scrape_google_careers(page, company)
        jobs: list[dict] = []
        api_jobs: list[dict] = []

        async def _on_response(response):
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            # Skip tiny config/analytics payloads
            try:
                data = await response.json()
            except Exception:
                return
            candidates = data if isinstance(data, list) else []
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, list) and len(v) >= 3 and isinstance((v or [{}])[0], dict):
                        candidates = v
                        break
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                lk = {k.lower(): v for k, v in item.items()}
                title = str(lk.get("title") or lk.get("text") or lk.get("name") or
                            lk.get("position") or lk.get("job_title") or "").strip()
                job_url = str(lk.get("url") or lk.get("link") or lk.get("apply_url") or
                              lk.get("job_url") or "").strip()
                job_id = str(lk.get("id") or lk.get("jobid") or lk.get("job_id") or "").strip()
                if title and not job_url and job_id:
                    base = url.rstrip("/")
                    job_url = f"{base}/{job_id}"
                if title and job_url:
                    api_jobs.append({"title": title, "company": company, "url": job_url})

        page.on("response", _on_response)
        try:
            await page.goto(url, wait_until="load", timeout=30_000)
        except Exception:
            pass  # timeout ok — data calls fire during load
        await page.wait_for_timeout(5000)

        # Scroll to bottom incrementally to trigger lazy-loading (e.g. Google Careers SPA)
        last_height = await page.evaluate("document.body.scrollHeight")
        for _ in range(10):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)
            new_height = await page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

        page.remove_listener("response", _on_response)

        if api_jobs:
            print(f"  [{self.NAME}] {company}: {len(api_jobs)} jobs via API interception")
            return api_jobs

        # ATS-specific DOM selectors
        seen: set[str] = set()
        ats_selectors = [
            "div.opening a", ".opening a[href*='/jobs/']",   # Greenhouse
            "[data-automation-id='jobTitle']",                # Workday
            ".js-jobs-list-item a",                           # SmartRecruiters
            ".posting-title a", ".postings-group .posting a", # Lever
            "a[href*='/jobs/results/']",                       # Google Careers
        ]
        for selector in ats_selectors:
            for link in await page.query_selector_all(selector):
                text = (await link.inner_text()).strip()
                href = (await link.get_attribute("href") or "").strip()
                if text and href and href not in seen:
                    seen.add(href)
                    full_url = self._resolve_url(href, url)
                    jobs.append({"title": text, "company": company, "url": full_url})

        if jobs:
            return jobs

        # Card-based extraction: title lives in h2/h3 inside the card, URL in a sibling link.
        # Handles sites like Mobileye where the clickable link contains only an icon/image.
        card_jobs: list[dict] = []
        card_selectors = (
            ".jobItem, [class*='jobItem'], [class*='job-card'], [class*='JobCard'], "
            "[class*='job-listing'], [class*='position-item'], [class*='opening']"
        )
        for card in await page.query_selector_all(card_selectors):
            title_el = await card.query_selector("h2, h3, [class*='title']")
            title = (await title_el.inner_text()).strip() if title_el else ""
            if not title:
                continue
            # Find any link inside the card that leads to an individual job page
            for link in await card.query_selector_all("a[href]"):
                href = (await link.get_attribute("href") or "").strip()
                full_url = self._resolve_url(href, url)
                if self._looks_like_job_page(full_url) and full_url not in seen:
                    seen.add(full_url)
                    card_jobs.append({"title": title, "company": company, "url": full_url})
                    break  # one URL per card is enough

        if card_jobs:
            return card_jobs

        # Final fallback: only links that pass the individual-job-page heuristic
        for link in await page.query_selector_all("a[href]"):
            text = (await link.inner_text()).strip()
            href = (await link.get_attribute("href") or "").strip()
            if not text or not href or len(text) < 5 or len(text) > 150:
                continue
            full_url = self._resolve_url(href, url)
            if self._looks_like_job_page(full_url) and full_url not in seen:
                seen.add(full_url)
                jobs.append({"title": text, "company": company, "url": full_url})

        return jobs

    async def _search_linkedin(self, page: Page, company: str) -> list[dict]:
        """Search LinkedIn Jobs Israel for a company with no known careers URL."""
        jobs = []
        search_url = (
            f"https://www.linkedin.com/jobs/search/"
            f"?keywords={quote(company)}&location=Israel"
        )
        try:
            await page.goto(search_url, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(3000)
        except Exception as e:
            print(f"  [{self.NAME}] LinkedIn search failed for {company}: {e}")
            return jobs

        cards = await page.query_selector_all(
            ".job-search-card, .jobs-search__results-list li, [class*='job-card']"
        )
        for card in cards:
            try:
                title_el = await card.query_selector(
                    "h3, .job-search-card__title, [class*='title']"
                )
                link_el = await card.query_selector("a[href*='/jobs/']")

                title = (await title_el.inner_text()).strip() if title_el else ""
                href = await link_el.get_attribute("href") if link_el else ""

                if title and href:
                    jobs.append({"title": title, "company": company, "url": href})
            except Exception:
                continue

        return jobs

    async def fetch_description(self, page: Page, url: str) -> str:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(PAGE_DELAY_MS)
        for selector in [
            "[class*='description']", "[class*='job-content']",
            "[class*='details']", "article", "main",
        ]:
            el = await page.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                if len(text) > 150:
                    return text
        return (await page.inner_text("body")).strip()


# ---------------------------------------------------------------------------
# Runner — called from main.py
# ---------------------------------------------------------------------------

# Excluded scrapers (kept for potential future use):
#   JobMasterScraper  — triggered bot-detection
#   WellfoundScraper  — triggered bot-detection
#   AllJobsScraper    — hard-blocked by Radware WAF even with real browser
#   DrushimScraper   — only a few jobs and often not relevent
ALL_SCRAPERS: list[type[JobScraper]] = [
    GoozaliScraper,
    WatchlistScraper,
]


async def run_scrapers(conn) -> None:
    """
    Run all scrapers, insert new jobs into the DB, and fetch descriptions
    only for URLs not already present.

    Import db functions here (not at module top) to keep scraper.py usable
    standalone for testing.
    """
    from db import job_exists, insert_job

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = await context.new_page()

        for scraper_cls in ALL_SCRAPERS:
            scraper = scraper_cls()
            print(f"\n=== {scraper.NAME} ===")
            try:
                listings = await scraper.scrape(page)
            except Exception as e:
                print(f"[{scraper.NAME}] Scrape failed: {e}")
                continue

            new_count = 0
            for job in listings:
                url = job.get("url", "")
                title = job.get("title", "")
                if not url or job_exists(conn, url):
                    continue
                if not is_relevant_title(title):
                    continue

                # Fetch full description only for new jobs
                try:
                    description = await scraper.fetch_description(page, url)
                except Exception as e:
                    description = f"[Description fetch failed: {e}]"

                source = job.get("source") or scraper.NAME
                insert_job(
                    conn,
                    title=title,
                    company=job.get("company", ""),
                    url=url,
                    description=description,
                    source=source,
                )
                new_count += 1

            print(f"[{scraper.NAME}] {new_count} new jobs added to DB.")

        await browser.close()


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import load_dotenv
    from db import get_connection, init_db

    load_dotenv()
    conn = get_connection()
    init_db(conn)
    asyncio.run(run_scrapers(conn))
    conn.close()
    print("\nDone. Check jobs.db for results.")
