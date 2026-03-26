# Job Finder — Implementation Plan

## Context
Eyal Wodner (Algorithm/EE Engineer, 5+ yrs Israeli Navy) needs an automated job-hunting pipeline that scrapes Israeli tech job boards + a personal watchlist, scores each role against his CV using Gemini, and emails a clean digest of high-match opportunities. Runs manually for now (no scheduler).

---

## Tech Decisions
| Concern | Decision |
|---|---|
| LLM | **Google Gemini free tier** — Google AI Pro is a separate consumer product; get a free API key from Google AI Studio (ai.google.dev). Free tier: 100 req/day (Gemini 2.5 Pro) — sufficient for this use case. |
| Email | Gmail SMTP + App Password — **dedicated Gmail account** for the pipeline |
| Scheduling | Manual (`python main.py`) for now |
| Wellfound auth | Dedicated scraper account; credentials in `.env` |

---

## Directory Structure
```
Job Finder/
├── .env                  # GOOGLE_API_KEY, GMAIL_USER, GMAIL_APP_PASSWORD, GMAIL_TO,
│                         # WELLFOUND_EMAIL, WELLFOUND_PASSWORD, MOCK_EMAIL
├── .gitignore            # .env, jobs.db, __pycache__, CV.md, Eyal Wodner CV.pdf
├── requirements.txt
├── CV.md                 # Eyal's master CV (gitignored for privacy)
├── Eyal Wodner CV.pdf    # (gitignored for privacy)
├── watchlist.yaml        # Companies/positions to always monitor (human-editable)
├── db.py
├── scraper.py
├── matcher.py
├── notify.py
└── main.py
```

---

## `watchlist.yaml` — Company Watchlist
Human-readable YAML that Eyal edits directly. Each entry can optionally include a direct jobs board URL (if known), otherwise the scraper will search generically.

```yaml
# Add companies you always want to monitor.
# job_board_url is optional — add it if you know it, otherwise leave blank.
companies:
  - name: Mobileye
    job_board_url: https://www.mobileye.com/careers/
  - name: Apple
    job_board_url: https://jobs.apple.com/en-us/search?location=israel
  - name: Rafael
    job_board_url:   # leave blank to use generic search
```

A `WatchlistScraper` class in `scraper.py` will iterate these entries: if a URL is provided, Playwright fetches that page directly and extracts jobs; if blank, it performs a targeted search on a general board (e.g., LinkedIn Israel) for that company name.

---

## Module Plans

### 1. `db.py` — State Management
- `get_connection() -> sqlite3.Connection`
- `init_db(conn)` — creates `jobs` table if not exists
- `job_exists(conn, url) -> bool`
- `insert_job(conn, title, company, url, description)`
- `update_match(conn, url, cv_score, job_score, rationale_cv, rationale_job)`
- `get_new_matches(conn, threshold=75) -> list[dict]` — where `job_score >= threshold AND status='new'`
- `mark_notified(conn, url)`

Schema:
```sql
CREATE TABLE IF NOT EXISTS jobs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  url             TEXT UNIQUE NOT NULL,
  title           TEXT,
  company         TEXT,
  description     TEXT,
  date_found      TEXT,
  cv_score        INTEGER DEFAULT NULL,   -- likelihood of passing CV screening
  job_score       INTEGER DEFAULT NULL,   -- realistic chance of getting the job
  rationale_cv    TEXT,
  rationale_job   TEXT,
  status          TEXT DEFAULT 'new'      -- 'new' | 'notified'
);
```

### 2. `notify.py` — HTML Email Notifier
- `build_html(jobs: list[dict]) -> str` — HTML table with columns: Title, Company, CV Screen, Job Chance, Rationale, Link
- `send_email(html: str)` — `smtplib.SMTP_SSL('smtp.gmail.com', 465)` using `GMAIL_USER` + `GMAIL_APP_PASSWORD`; recipient = `GMAIL_TO`
- `run_notifier(conn)` — query new matches → build HTML → send (or write file) → mark notified

**Mock mode**: `MOCK_EMAIL=true` in `.env` → writes `email_preview.html` instead of sending.

### 3. `scraper.py` — Data Ingestion
Base class `JobScraper` with abstract `scrape() -> list[dict]`.

| Scraper | Method | Notes |
|---|---|---|
| `GoozaliScraper` | BeautifulSoup4 | Public static/Airtable embed |
| `StartupNationScraper` | Playwright | Dynamic JS page |
| `WellfoundScraper` | Playwright | Login with `WELLFOUND_EMAIL`/`WELLFOUND_PASSWORD`, filter Israel |
| `WatchlistScraper` | Playwright | Reads `watchlist.yaml`; fetches provided URLs or searches LinkedIn |

Each returns `[{"title", "company", "url", "description"}, ...]`.

### 4. `matcher.py` — Two-Part Gemini Scoring
Load `CV.md`. For each unscored job, make **one API call** with a prompt that requests two evaluations in a single JSON response:

```
You are an expert technical recruiter...
Evaluate this candidate's CV against the job description in two ways:
1. cv_score (0-100): Probability their CV passes the initial screening (ATS/HR filter)
2. job_score (0-100): Realistic probability they get the job (accounting for competition, seniority, fit)
Return ONLY: {"cv_score": int, "job_score": int, "rationale_cv": "1 sentence", "rationale_job": "1 sentence"}
```

Uses `google-generativeai` (`gemini-2.5-pro` or `gemini-2.5-flash`) with `GOOGLE_API_KEY`.

### 5. `main.py` — Orchestration
```
conn = get_connection() → init_db(conn)
→ run all scrapers → insert new jobs
→ run matcher on unscored jobs
→ run notifier
→ conn.close()
```

---

## Implementation Order
1. **`db.py` + mock `notify.py`** — validate schema and email HTML visually
2. **`scraper.py`** — build scrapers to get real job data into the DB
3. **`matcher.py`** — run Gemini against real scraped jobs
4. **`main.py`** — wire everything together end-to-end

---

## Verification
1. `python db.py` (with `__main__` block) → `sqlite3 jobs.db ".schema"` — confirm table created
2. `MOCK_EMAIL=true python notify.py` → open `email_preview.html` in browser
3. `python scraper.py` → check rows in `jobs.db`
4. `python matcher.py` → check `cv_score`, `job_score` populated in DB
5. `python main.py` → full end-to-end run, check inbox
