import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from litellm import completion

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from shared.config import ACTIVE_MODEL, API_BASE, NUM_CTX

DB_PATH = PROJECT_DIR / "database" / "jobs.db"
PROFILE_PATH = Path(__file__).resolve().parent / "applicant_profile.txt"

DESCRIPTION_CHAR_LIMIT = 15000
HTTP_TIMEOUT = 30.0
HEADERS = {"User-Agent": "JobDaemonLinux/1.0"}

PROFILE_PROMPT = """Convert this editable applicant profile into a compact JSON object for job matching.

Return only a JSON object. Keep every factual constraint and preference stated in the profile. Do not invent facts.

PROFILE:
{profile_text}
"""

INITIAL_MATCH_PROMPT = """Decide whether this job is compatible with the applicant profile using only the job fields below.

Return only a JSON object with exactly these fields:
- "status": one of "incompatible" or "needs_description"
- "reason": a concise factual explanation

Mark "incompatible" only when the available information clearly conflicts with the profile. Otherwise mark "needs_description". Listing fields alone do not establish visa authorization, graduation timing, degree eligibility, or other material requirements.

APPLICANT PROFILE JSON:
{profile_json}

JOB:
{job_json}
"""

DESCRIPTION_MATCH_PROMPT = """Decide whether this job is compatible with the applicant profile using the job fields and job description below.

Return only a JSON object with exactly these fields:
- "status": one of "compatible", "incompatible", or "needs_details"
- "reason": a concise factual explanation based on the job description

Mark "compatible" only when the requirements support compatibility. Mark "incompatible" when they clearly conflict. Mark "needs_details" when the description still does not provide enough information.

APPLICANT PROFILE JSON:
{profile_json}

JOB:
{job_json}

JOB DESCRIPTION:
{description}
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_json_object(raw_text):
    cleaned = re.sub(r"^```(?:json)?", "", raw_text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"```$", "", cleaned.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise ValueError("Model response does not contain a JSON object")

    parsed = json.loads(cleaned[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Model response is not a JSON object")
    return parsed


def call_model(prompt):
    response = completion(
        model=ACTIVE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        api_base=API_BASE,
        num_ctx=NUM_CTX,
    )
    return parse_json_object(response.choices[0].message.content)


def load_profile():
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(f"Applicant profile not found: {PROFILE_PATH}")

    profile_text = PROFILE_PATH.read_text().strip()
    if not profile_text:
        raise ValueError("Applicant profile is empty")

    profile_json = call_model(PROFILE_PROMPT.format(profile_text=profile_text))
    profile_hash = hashlib.sha256(profile_text.encode("utf-8")).hexdigest()
    return profile_json, profile_hash


def fetch_job_description(url):
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, headers=HEADERS, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"    Could not fetch description: {exc}")
        return ""

    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type:
        return response.text[:DESCRIPTION_CHAR_LIMIT]

    soup = BeautifulSoup(response.text, "html.parser")
    for element in soup(["script", "style", "nav", "footer", "noscript"]):
        element.decompose()

    return " ".join(soup.stripped_strings)[:DESCRIPTION_CHAR_LIMIT]


def validate_decision(decision, allowed_statuses):
    status = decision.get("status")
    reason = decision.get("reason")
    if status not in allowed_statuses or not isinstance(reason, str) or not reason.strip():
        raise ValueError("Model response has an invalid compatibility decision")
    return status, reason.strip()


def evaluate_job(job, profile_json):
    job_json = json.dumps(job, ensure_ascii=False)
    profile_json_text = json.dumps(profile_json, ensure_ascii=False)
    if job["is_direct"]:
        description = fetch_job_description(job["link"])
        checked_at = now_iso()
        if not description:
            return "needs_details", "The direct job page did not provide an extractable description.", checked_at

        final = call_model(
            DESCRIPTION_MATCH_PROMPT.format(
                profile_json=profile_json_text,
                job_json=job_json,
                description=description,
            )
        )
        status, reason = validate_decision(final, {"compatible", "incompatible", "needs_details"})
        return status, reason, checked_at

    initial = call_model(INITIAL_MATCH_PROMPT.format(profile_json=profile_json_text, job_json=job_json))
    status, reason = validate_decision(initial, {"incompatible", "needs_description"})
    if status == "incompatible":
        return status, reason, None
    return "needs_direct_link", "The listing does not contain enough eligibility information and its link is indirect.", None


def pending_jobs(connection, profile_hash, limit, rerun):
    query = """
        SELECT j.id, j.company, j.title, j.location, j.date_posted, j.link, j.is_direct
        FROM jobs AS j
        LEFT JOIN job_matches AS m ON m.job_id = j.id
        WHERE j.company <> ''
          AND j.title <> ''
          AND j.link <> ''
        ORDER BY j.id
    """
    parameters = []
    if not rerun:
        query = query.replace("ORDER BY j.id", "AND (m.job_id IS NULL OR m.profile_hash <> ?)\n        ORDER BY j.id")
        parameters.append(profile_hash)
    if limit is not None:
        query += " LIMIT ?"
        parameters.append(limit)
    return connection.execute(query, parameters).fetchall()


def save_match(connection, job_id, profile_hash, status, reason, description_checked_at):
    connection.execute(
        """
        INSERT INTO job_matches
        (job_id, profile_hash, status, reason, description_checked_at, evaluated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            profile_hash = excluded.profile_hash,
            status = excluded.status,
            reason = excluded.reason,
            description_checked_at = excluded.description_checked_at,
            evaluated_at = excluded.evaluated_at
        """,
        (job_id, profile_hash, status, reason, description_checked_at, now_iso()),
    )
    connection.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Evaluate at most this many pending jobs")
    parser.add_argument("--rerun", action="store_true", help="Re-evaluate jobs even if this profile was already used")
    args = parser.parse_args()

    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}. Run init_db.py first.")

    profile_json, profile_hash = load_profile()
    print("Applicant profile converted to internal JSON for this matching run.")

    with sqlite3.connect(DB_PATH) as connection:
        jobs = pending_jobs(connection, profile_hash, args.limit, args.rerun)
        print(f"Evaluating {len(jobs)} job(s).")

        for index, row in enumerate(jobs, start=1):
            job = {
                "company": row[1],
                "title": row[2],
                "location": row[3],
                "date_posted": row[4],
                "link": row[5],
                "is_direct": bool(row[6]),
            }
            print(f"  [{index}/{len(jobs)}] {job['company']} — {job['title']}")

            try:
                status, reason, description_checked_at = evaluate_job(job, profile_json)
                save_match(connection, row[0], profile_hash, status, reason, description_checked_at)
                print(f"    {status}: {reason}")
            except (ValueError, json.JSONDecodeError) as exc:
                print(f"    Skipped: invalid model response ({exc})")
            except Exception as exc:
                print(f"    Skipped: {exc}")


if __name__ == "__main__":
    main()
