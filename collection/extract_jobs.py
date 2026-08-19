import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from litellm import completion
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from shared.config import ACTIVE_MODEL, API_BASE, NUM_CTX

RAW_DIR = PROJECT_DIR / "collection" / "data" / "raw"
DB_PATH = PROJECT_DIR / "database" / "jobs.db"
FAILED_PARSE_DIR = PROJECT_DIR / "collection" / "data" / "failed_parses"

CHUNK_CHAR_LIMIT = 3000

PROMPT_TEMPLATE = """You are extracting job postings from the source text below. The source may be a Markdown table, a bullet list, prose, or text rendered from a webpage.

Return ONLY a JSON array. No explanation, no markdown code fences, nothing else.

Each item in the array must be an object with exactly these fields:
- "company": string, the company or organization name
- "title": string, the job or program title
- "location": string, location if mentioned, otherwise empty string
- "date_posted": string, exactly as written in the source text (could be a relative age like "3d", an absolute date, or empty string if not present)
- "link": string, the actual URL to apply or learn more, extracted from markdown links or HTML anchor tags
- "is_direct": boolean, true if the link goes straight to the company's own careers page or a known applicant tracking system (like Greenhouse, Lever, Workday), false if it goes through an aggregator or third-party site like Simplify or LinkedIn

Only return genuine job or research-program postings. Do not return headings, explanatory text, or duplicate rows. If a posting does not provide a company, title, and URL, do not return it.

For date_posted, extract the posting date exactly as shown whenever one is present. Do not substitute the current date, source-fetch date, or extraction date. Use an empty string only when the posting has no date.

If you cannot find any job postings in this text, return an empty array: []

TEXT:
{content}
"""


def load_hash_record(source_name):
    hash_path = RAW_DIR / f"{source_name}.hash.json"
    if not hash_path.exists():
        return None
    return json.loads(hash_path.read_text())


def find_current_snapshots(single_file=None):
    if single_file:
        path = RAW_DIR / single_file
        if not path.exists():
            print(f"File not found: {path}")
            return []
        return [path]

    return sorted(RAW_DIR.glob("*_current.*"))


def chunk_content(content):
    lines = content.splitlines()
    chunks = []
    current_chunk_lines = []
    current_length = 0

    for line in lines:
        line_length = len(line) + 1
        if current_length + line_length > CHUNK_CHAR_LIMIT and current_chunk_lines:
            chunks.append("\n".join(current_chunk_lines))
            current_chunk_lines = []
            current_length = 0
        current_chunk_lines.append(line)
        current_length += line_length

    if current_chunk_lines:
        chunks.append("\n".join(current_chunk_lines))

    return chunks


def render_webpage(url):
    page_url = url if url.startswith(("http://", "https://")) else f"https://{url}"
    rendered_parts = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            for frame in page.frames:
                frame_domain = urlparse(frame.url).netloc.lower()
                is_job_frame = frame is page.main_frame or frame_domain.endswith(("airtable.com", "jobright.ai"))
                if not is_job_frame:
                    continue

                try:
                    text = frame.locator("body").inner_text(timeout=5000).strip()
                    links = frame.locator("a[href]").evaluate_all(
                        "anchors => anchors.map(anchor => ({text: anchor.innerText.trim(), href: anchor.href}))"
                    )
                except PlaywrightTimeoutError:
                    continue

                if text:
                    rendered_parts.append(f"FRAME URL: {frame.url}\n{text}")

                for link in links:
                    href = link.get("href", "").strip()
                    label = link.get("text", "").strip()
                    if href:
                        rendered_parts.append(f"LINK: {label} -> {href}")
        finally:
            browser.close()

    return "\n".join(rendered_parts)


def call_llm(chunk_text):
    prompt = PROMPT_TEMPLATE.format(content=chunk_text)

    response = completion(
        model=ACTIVE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        api_base=API_BASE,
        num_ctx=NUM_CTX,
    )

    return response.choices[0].message.content


def parse_json_array(raw_text, source_name, chunk_index):
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(json)?", "", cleaned.strip())
    cleaned = re.sub(r"```$", "", cleaned.strip())
    cleaned = cleaned.strip()

    start = cleaned.find("[")
    end = cleaned.rfind("]")

    if start == -1 or end == -1 or end < start:
        log_failed_parse(raw_text, source_name, chunk_index)
        return []

    candidate = cleaned[start:end + 1]

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, list):
            return parsed
        log_failed_parse(raw_text, source_name, chunk_index)
        return []
    except json.JSONDecodeError:
        log_failed_parse(raw_text, source_name, chunk_index)
        return []


def log_failed_parse(raw_text, source_name, chunk_index):
    FAILED_PARSE_DIR.mkdir(parents=True, exist_ok=True)
    fail_path = FAILED_PARSE_DIR / f"{source_name}_chunk{chunk_index}.txt"
    fail_path.write_text(raw_text)
    print(f"  Could not parse JSON from chunk {chunk_index}, raw output saved to {fail_path}")


def normalize_job(job):
    if not isinstance(job, dict):
        return None

    company = job.get("company", "")
    title = job.get("title", "")
    link = job.get("link", "")

    if not all(isinstance(value, str) and value.strip() for value in (company, title, link)):
        return None

    return {
        "company": company.strip(),
        "title": title.strip(),
        "location": str(job.get("location", "")).strip(),
        "date_posted": str(job.get("date_posted", "")).strip(),
        "link": link.strip(),
        "is_direct": job.get("is_direct") is True,
    }


def resolve_date_posted(raw_date, fetched_at_iso):
    if not raw_date:
        return ""

    match = re.match(r"^(\d+)\s*d$", raw_date.strip(), re.IGNORECASE)
    if match and fetched_at_iso:
        days_ago = int(match.group(1))
        fetched_at = datetime.fromisoformat(fetched_at_iso)
        actual_date = fetched_at - timedelta(days=days_ago)
        return actual_date.date().isoformat()

    return raw_date


def get_or_create_source(connection, url, source_type, last_fetched_at, last_changed_at):
    cursor = connection.cursor()
    cursor.execute("SELECT id FROM sources WHERE url = ?", (url,))
    row = cursor.fetchone()

    if row:
        cursor.execute(
            "UPDATE sources SET last_fetched_at = ?, last_changed_at = ? WHERE id = ?",
            (last_fetched_at, last_changed_at, row[0]),
        )
        connection.commit()
        return row[0]

    cursor.execute(
        "INSERT INTO sources (url, source_type, last_fetched_at, last_changed_at) VALUES (?, ?, ?, ?)",
        (url, source_type, last_fetched_at, last_changed_at),
    )
    connection.commit()
    return cursor.lastrowid


def insert_job(connection, source_id, job, extracted_at):
    cursor = connection.cursor()
    cursor.execute(
        """
        INSERT INTO jobs
        (source_id, company, title, location, date_posted, link, is_direct, extracted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company, title, link) DO UPDATE SET
            is_direct = excluded.is_direct
        """,
        (
            source_id,
            job.get("company", ""),
            job.get("title", ""),
            job.get("location", ""),
            job.get("date_posted", ""),
            job.get("link", ""),
            job.get("is_direct", False),
            extracted_at,
        ),
    )
    connection.commit()
    return cursor.rowcount


def process_file(path, connection):
    source_name = path.name.replace("_current" + path.suffix, "")
    print(f"Processing: {path.name}")

    record = load_hash_record(source_name)
    if record is None:
        print("  No hash.json record found, skipping (run fetch.py first)")
        return

    source_url = record.get("url")
    source_type = record.get("source_type")

    if not source_url:
        print("  hash.json has no url field yet, skipping (apply the fetch.py edit and re-run fetch.py)")
        return

    source_id = get_or_create_source(
        connection, source_url, source_type, record.get("last_checked"), record.get("last_changed")
    )

    if source_type == "webpage":
        print("  Rendering webpage and embedded frames...")
        content = render_webpage(source_url)
    else:
        content = path.read_text()

    if not content.strip():
        print("  No extractable text found, skipping")
        return

    chunks = chunk_content(content)
    print(f"  Split into {len(chunks)} chunk(s)")

    total_extracted = 0
    total_saved = 0
    extracted_at = datetime.now(timezone.utc).isoformat()

    for index, chunk in enumerate(chunks, start=1):
        print(f"  Processing chunk {index}/{len(chunks)}...")
        raw_output = call_llm(chunk)
        jobs = parse_json_array(raw_output, source_name, index)
        total_extracted += len(jobs)

        for raw_job in jobs:
            job = normalize_job(raw_job)
            if job is None:
                print("  Skipping incomplete job record (company, title, and link are required)")
                continue

            job["date_posted"] = resolve_date_posted(job["date_posted"], record.get("last_changed"))
            saved = insert_job(connection, source_id, job, extracted_at)
            total_saved += saved

    print(f"  Extracted {total_extracted} jobs, saved {total_saved}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Process one current snapshot file from collection/data/raw/.")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}. Run init_db.py first.")
        sys.exit(1)

    connection = sqlite3.connect(DB_PATH)

    files = find_current_snapshots(args.file)
    if not files:
        print("No files to process.")
        return

    for path in files:
        process_file(path, connection)
        print()

    connection.close()


if __name__ == "__main__":
    main()
