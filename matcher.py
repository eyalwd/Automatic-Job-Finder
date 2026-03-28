import json
import os
import re
import time

import google.generativeai as genai
from dotenv import load_dotenv

from db import get_connection, init_db, update_match

load_dotenv()

CV_PATH = "CV.md"
MODEL = "gemini-2.5-flash"

PROMPT_TEMPLATE = """\
You are an expert technical recruiter evaluating a candidate for a job opening.

--- CANDIDATE CV ---
{cv}

--- JOB POSTING ---
Title: {title}
Company: {company}

{description}
--- END ---

Evaluate the candidate against this job in two ways:
1. cv_score (0-100): Probability their CV passes the initial screening (ATS/HR filter)
2. job_score (0-100): Realistic probability they get the job (accounting for competition, seniority, cultural fit)

Return ONLY valid JSON, no markdown fences:
{{"cv_score": <int>, "job_score": <int>, "rationale_cv": "<1 sentence>", "rationale_job": "<1 sentence>"}}
"""


def load_cv() -> str:
    with open(CV_PATH, encoding="utf-8") as f:
        return f.read()


def get_unscored_jobs(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT url, title, company, description FROM jobs WHERE cv_score IS NULL"
    ).fetchall()
    return [dict(row) for row in rows]


def score_job(model, cv: str, job: dict) -> dict | None:
    prompt = PROMPT_TEMPLATE.format(
        cv=cv,
        title=job["title"] or "",
        company=job["company"] or "",
        description=job["description"] or "",
    )
    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        # Strip markdown code fences if the model wraps the JSON anyway
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        return json.loads(text)
    except Exception as e:
        print(f"  Error scoring {job['url']}: {e}")
        return None


def run_matcher(conn) -> None:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set in environment")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(MODEL)

    cv = load_cv()
    jobs = get_unscored_jobs(conn)

    if not jobs:
        print("No unscored jobs found.")
        return

    print(f"Scoring {len(jobs)} job(s) with Gemini ({MODEL})...")

    for i, job in enumerate(jobs, 1):
        label = f"{job['title']} @ {job['company']}"
        print(f"  [{i}/{len(jobs)}] {label}")

        result = score_job(model, cv, job)
        if result is None:
            continue

        try:
            update_match(
                conn,
                url=job["url"],
                cv_score=int(result["cv_score"]),
                job_score=int(result["job_score"]),
                rationale_cv=result["rationale_cv"],
                rationale_job=result["rationale_job"],
            )
            print(f"         cv={result['cv_score']} job={result['job_score']}")
        except (KeyError, ValueError) as e:
            print(f"  Bad response for {job['url']}: {e} — raw: {result}")

        # Stay well within the free-tier rate limit (2 RPM for 2.5 Pro, 10 RPM for Flash)
        if i < len(jobs):
            time.sleep(6)

    print("Matching complete.")


if __name__ == "__main__":
    conn = get_connection()
    init_db(conn)
    run_matcher(conn)
    conn.close()
