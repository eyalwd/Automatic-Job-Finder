Project Objective: > Build an automated, scheduled job-hunting pipeline in Python. The system will scrape specific Israeli tech/startup job boards, use an LLM to evaluate the user's eligibility based on their Markdown CV, and send a clean HTML email with high-matching roles.

Technical Stack:

Language: Python 3.11+

Scraping: Playwright (for dynamic pages) and BeautifulSoup4 (for static HTML).

Matching: openai or google-generativeai library for LLM API integration.

Database: SQLite (local jobs.db file).

Notifications: smtplib and email.mime (for HTML emails).

System Architecture & Modules:

1. Data Ingestion Module (scraper.py)

Create a base class JobScraper.

Implement scrapers targeting Israeli startup ecosystems:

Target 1: Wellfound (Filter: Location = Israel).

Target 2: Goozali's open tech jobs database.

Target 3: Startup Nation Central (or similar local boards).

Extract: Job Title, Company, URL, and the full Job Description.

2. State Management (db.py)

Initialize an SQLite database with a jobs table.

Columns: id, url (UNIQUE), title, company, description, date_found, match_score, status (e.g., 'new', 'notified').

Rule: Check if the job URL exists in the database before scraping the full description to save time and prevent duplicate LLM API calls.

3. The Matching Engine (matcher.py)

Load the user's master CV from a local cv.md file.

Construct a prompt combining the CV text and the scraped Job Description.

Call the LLM API to act as an expert technical recruiter evaluating the fit for an Algorithm/Electrical Engineering role.

Constraint: Force the LLM to output ONLY a JSON object: {"score": integer_between_0_and_100, "rationale": "1 sentence explanation"}

Update the database with the score and rationale.

4. The Notifier (notify.py)

Query the database for all jobs found where match_score >= 75 and status == 'new'.

Use email.mime to construct a clean, readable HTML table containing the Title, Company, Score, Rationale, and a clickable URL.

Send via smtplib and update the job status to 'notified'.

5. Orchestration (main.py)

Create a master script that sequentially runs the scrapers, passes new entries to the matcher, triggers the email notifier, and closes the database.

Agent Instructions: > Please begin by generating the directory structure and writing the code for db.py and a mock version of notify.py (with a dummy HTML email template) so we can ensure the email formatting looks good before building the scrapers.