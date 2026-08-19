import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

COLLECTION_DIR = Path(__file__).resolve().parent
DATA_DIR = COLLECTION_DIR / "data" / "raw"
README_LINKS_FILE = COLLECTION_DIR / "sources" / "readme_links.txt"
WEBPAGE_LINKS_FILE = COLLECTION_DIR / "sources" / "webpage_links.txt"

GITHUB_API = "https://api.github.com"
RAW_GITHUB = "https://raw.githubusercontent.com"

HTTP_TIMEOUT = 30.0
HEADERS = {"User-Agent": "jobdaemonlinux-fetch"}


def read_links(path):
    if not path.exists():
        print(f"Missing links file: {path}")
        return []
    lines = path.read_text().splitlines()
    return [line.strip() for line in lines if line.strip()]


def parse_github_url(url):
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]

    if len(parts) < 2:
        return None

    owner = parts[0]
    repo = parts[1]

    if len(parts) >= 5 and parts[2] == "blob":
        branch = parts[3]
        filepath = "/".join(parts[4:])
        return {"owner": owner, "repo": repo, "branch": branch, "filepath": filepath}

    return {"owner": owner, "repo": repo, "branch": None, "filepath": "README.md"}


def get_default_branch(owner, repo, client):
    api_url = f"{GITHUB_API}/repos/{owner}/{repo}"
    response = client.get(api_url)
    response.raise_for_status()
    return response.json()["default_branch"]


def fetch_text(url, client):
    response = client.get(url)
    response.raise_for_status()
    return response.text


def slugify(*parts):
    joined = "_".join(parts)
    joined = re.sub(r"[^A-Za-z0-9._-]+", "_", joined)
    return joined


def hash_content(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_hash_record(hash_path):
    if not hash_path.exists():
        return None
    return json.loads(hash_path.read_text())


def process_source(source_name, content, timestamp, extension, source_url, source_type):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    current_path = DATA_DIR / f"{source_name}_current{extension}"
    previous_path = DATA_DIR / f"{source_name}_previous{extension}"
    hash_path = DATA_DIR / f"{source_name}.hash.json"

    new_hash = hash_content(content)
    old_record = load_hash_record(hash_path)

    if old_record is not None and old_record.get("hash") == new_hash:
        print("  No change detected - skipping snapshot")
        old_record["last_checked"] = timestamp
        old_record["url"] = source_url
        old_record["source_type"] = source_type
        hash_path.write_text(json.dumps(old_record, indent=2))
        return False

    if old_record is not None and current_path.exists():
        previous_path.write_text(current_path.read_text())

    current_path.write_text(content)

    new_record = {
        "hash": new_hash,
        "last_checked": timestamp,
        "last_changed": timestamp,
        "url": source_url,
        "source_type": source_type,
    }
    hash_path.write_text(json.dumps(new_record, indent=2))

    if old_record is None:
        print("  First fetch - snapshot saved")
    else:
        print("  Change detected - snapshot updated")

    return True


def process_readme_links(links, client, timestamp):
    print("=== Step 1: README / GitHub sources ===")
    changed = 0
    failed = []

    for link in links:
        print(f"Processing: {link}")
        parsed = parse_github_url(link)

        if parsed is None:
            print("  Could not parse GitHub URL, skipping")
            failed.append(link)
            continue

        owner = parsed["owner"]
        repo = parsed["repo"]
        filepath = parsed["filepath"]
        branch = parsed["branch"]

        try:
            if branch is None:
                branch = get_default_branch(owner, repo, client)
                print(f"  Resolved default branch: {branch}")

            content = fetch_text(f"{RAW_GITHUB}/{owner}/{repo}/{branch}/{filepath}", client)
            print(f"  Fetched OK ({len(content)} bytes)")
        except httpx.HTTPStatusError as exc:
            print(f"  Fetch failed: {exc.response.status_code} for {exc.request.url}")
            failed.append(link)
            continue
        except httpx.HTTPError as exc:
            print(f"  Fetch failed: {exc}")
            failed.append(link)
            continue

        source_name = slugify(owner, repo, filepath.replace("/", "_"))
        if process_source(source_name, content, timestamp, ".md", link, "github_readme"):
            changed += 1

    return changed, failed


def process_webpage_links(links, client, timestamp):
    print("=== Step 2: Webpage sources ===")
    changed = 0
    failed = []

    for link in links:
        url = link if link.startswith("http") else f"https://{link}"
        print(f"Processing: {url}")

        try:
            content = fetch_text(url, client)
            print(f"  Fetched OK ({len(content)} bytes)")
        except httpx.HTTPStatusError as exc:
            print(f"  Fetch failed: {exc.response.status_code} for {exc.request.url}")
            failed.append(link)
            continue
        except httpx.HTTPError as exc:
            print(f"  Fetch failed: {exc}")
            failed.append(link)
            continue

        source_name = slugify(urlparse(url).netloc)
        if process_source(source_name, content, timestamp, ".html", url, "webpage"):
            changed += 1

    return changed, failed


def main():
    timestamp = datetime.now(timezone.utc).isoformat()

    readme_links = read_links(README_LINKS_FILE)
    webpage_links = read_links(WEBPAGE_LINKS_FILE)

    with httpx.Client(timeout=HTTP_TIMEOUT, headers=HEADERS, follow_redirects=True) as client:
        r_changed, r_failed = process_readme_links(readme_links, client, timestamp)
        print()
        w_changed, w_failed = process_webpage_links(webpage_links, client, timestamp)

    print()
    print("=== Summary ===")
    print(f"README sources: {len(readme_links)} processed, {r_changed} changed, {len(r_failed)} failed")
    print(f"Webpage sources: {len(webpage_links)} processed, {w_changed} changed, {len(w_failed)} failed")

    if r_failed or w_failed:
        print("Failed links:")
        for link in r_failed + w_failed:
            print(f"  - {link}")
        sys.exit(1)


if __name__ == "__main__":
    main()
