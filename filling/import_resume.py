"""Create a reviewable filling-profile draft from a PDF resume without inventing data."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


MONTHS = {
    "jan": "01", "january": "01", "feb": "02", "february": "02", "mar": "03", "march": "03",
    "apr": "04", "april": "04", "may": "05", "jun": "06", "june": "06", "jul": "07",
    "july": "07", "aug": "08", "august": "08", "sep": "09", "sept": "09", "september": "09",
    "oct": "10", "october": "10", "nov": "11", "november": "11", "dec": "12", "december": "12",
}
SECTION_NAMES = {
    "education": "education",
    "experience": "experience",
    "projects": "projects",
    "technical skills": "skills",
    "skills": "skills",
    "leadership & achievements": "achievements",
    "leadership and achievements": "achievements",
}


def run_poppler(*args: str) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=True)
    return result.stdout


def extract_text(pdf_path: Path) -> str:
    return run_poppler("pdftotext", "-layout", str(pdf_path), "-")


def extract_urls(pdf_path: Path) -> list[str]:
    output = run_poppler("pdfinfo", "-url", str(pdf_path))
    return re.findall(r"https?://\S+|mailto:\S+", output)


def nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def split_sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    header: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        name = SECTION_NAMES.get(line.lower())
        if name:
            current = name
            sections.setdefault(name, [])
        elif current is None:
            header.append(line)
        else:
            sections[current].append(line)
    return header, sections


def month_date(value: str) -> str:
    match = re.search(r"([A-Za-z]+)\.?\s+(20\d{2})", value)
    if not match:
        return ""
    month = MONTHS.get(match.group(1).lower().rstrip("."))
    return f"{match.group(2)}-{month}" if month else ""


def split_location(value: str) -> tuple[str, str]:
    match = re.match(r"(.+?)\s{2,}([A-Za-z .]+,\s*[A-Z]{2}|Remote)$", value)
    if not match:
        return value, ""
    return match.group(1).strip(), match.group(2).strip()


def contact_profile(header: list[str], urls: list[str]) -> dict:
    header_text = " ".join(header)
    email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", header_text)
    phone_match = re.search(r"(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}", header_text)
    name = header[0] if header else ""
    name_parts = name.split()
    links: dict[str, str] = {}
    for url in urls:
        lower = url.lower()
        if "linkedin.com" in lower:
            links.setdefault("linkedin", url)
        elif "github.com" in lower:
            links.setdefault("github", url)
        elif url.startswith("http") and "website" not in links:
            links["website"] = url
    return {
        "personal": {
            "first_name": name_parts[0] if name_parts else "",
            "last_name": " ".join(name_parts[1:]),
            "email": email_match.group(0) if email_match else "",
            "phone": phone_match.group(0) if phone_match else "",
        },
        "links": links,
    }


def parse_education(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    for index, line in enumerate(lines):
        if not re.search(r"university|college|institute|school", line, re.IGNORECASE):
            continue
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        degree_match = re.search(r"(Bachelor|Master|Doctor|Associate)[^()]*", next_line, re.IGNORECASE)
        if not degree_match:
            continue
        school, location = split_location(line)
        entry = {"school": school, "degree": degree_match.group(0).strip(), "major": "", "minor": "", "graduation_date": "", "gpa": "", "coursework": []}
        if location:
            entry["location"] = location
        major_match = re.search(r"\bin\s+([A-Za-z &]+?)(?:\s*\(|$)", next_line)
        if major_match:
            entry["major"] = major_match.group(1).strip()
        gpa_match = re.search(r"GPA:\s*([0-4](?:\.\d+)?)", next_line, re.IGNORECASE)
        if gpa_match:
            entry["gpa"] = gpa_match.group(1)
        nearby = " ".join(lines[index:index + 3])
        entry["graduation_date"] = month_date(nearby)
        coursework_match = re.search(r"Relevant Coursework:\s*(.+)", nearby, re.IGNORECASE)
        if coursework_match:
            entry["coursework"] = [item.strip() for item in coursework_match.group(1).split(",") if item.strip()]
        entries.append(entry)
    return entries


def parse_experience(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    date_pattern = re.compile(r"((?:[A-Za-z]{3,9}\.?\s+)?20\d{2})\s*[–-]\s*(Present|(?:[A-Za-z]{3,9}\.?\s+)?20\d{2})")
    starts = [(index, date_pattern.search(line)) for index, line in enumerate(lines)]
    starts = [(index, match) for index, match in starts if match]
    for position, (start, match) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        title = lines[start][:match.start()].strip(" -")
        employer, location = split_location(lines[start + 1] if start + 1 < end else "")
        highlights = join_bullets(lines[start + 2:end])
        entries.append({
            "company": employer,
            "title": title,
            "location": location,
            "start_date": month_date(match.group(1)),
            "end_date": "" if match.group(2).lower() == "present" else month_date(match.group(2)),
            "description": " ".join(highlights),
            "highlights": highlights,
        })
    return entries


def parse_projects(lines: list[str], urls: list[str]) -> list[dict]:
    entries: list[dict] = []
    for line in lines:
        if line.startswith("•"):
            if entries:
                entries[-1]["highlights"].append(line.lstrip("• ").strip())
            continue
        match = re.search(r"(?:(January|February|March|April|May|June|July|August|September|October|November|December),?\s+)?(20\d{2})", line)
        if not match:
            if entries and entries[-1]["highlights"]:
                entries[-1]["highlights"][-1] = f"{entries[-1]['highlights'][-1]} {line}".strip()
            continue
        name = line[:match.start()].replace("Link", "").strip(" -–")
        if not name:
            continue
        date_value = month_date(match.group(0)) or match.group(2)
        entries.append({"name": name, "date": date_value, "url": "", "description": "", "highlights": []})
    github_urls = [url for url in urls if "github.com" in url.lower()]
    for entry in entries:
        if "jobdaemon" in entry["name"].lower():
            entry["url"] = next((url for url in github_urls if "jobdaemon" in url.lower()), "")
        entry["description"] = " ".join(entry["highlights"])
    return entries


def parse_skills(lines: list[str]) -> list[str]:
    text = " ".join(lines)
    text = re.sub(r"[A-Za-z &]+:\s*", "", text)
    text = re.sub(r"\)(?=[A-Z])", "), ", text)
    return [item.strip() for item in text.split(",") if item.strip()]


def join_bullets(lines: list[str]) -> list[str]:
    bullets: list[str] = []
    for line in lines:
        if line.startswith("•"):
            bullets.append(line.lstrip("• ").strip())
        elif bullets:
            bullets[-1] = f"{bullets[-1]} {line}".strip()
    return bullets


def build_profile(pdf_path: Path) -> dict:
    lines = nonempty_lines(extract_text(pdf_path))
    urls = extract_urls(pdf_path)
    header, sections = split_sections(lines)
    profile = contact_profile(header, urls)
    profile.update({
        "resumes": {"default": str(pdf_path.resolve())},
        "education": parse_education(sections.get("education", [])),
        "experience": parse_experience(sections.get("experience", [])),
        "projects": parse_projects(sections.get("projects", []), urls),
        "skills": parse_skills(sections.get("skills", [])),
        "achievements": sections.get("achievements", []),
        "interests": [],
        "application_answers": [],
    })
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a reviewable profile draft from a PDF resume.")
    parser.add_argument("resume", type=Path)
    parser.add_argument("--output", type=Path, default=Path("filling/profile.resume.json"))
    args = parser.parse_args()
    if not args.resume.is_file():
        raise FileNotFoundError(f"Resume not found: {args.resume}")
    profile = build_profile(args.resume)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    print(f"Review the draft before using it: {args.output}")


if __name__ == "__main__":
    main()
