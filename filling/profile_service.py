"""Local-only profile service used by the JobDaemon application-filling extension."""

from __future__ import annotations

import json
import os
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


FILLING_DIR = Path(__file__).resolve().parent
PROFILE_PATH = Path(os.environ.get("JOBDAEMON_PROFILE_FILE", FILLING_DIR / "profile.json"))
TEXT_PROFILE_PATH = FILLING_DIR / "profile.txt"
HOST = "127.0.0.1"
PORT = 8765


def load_profile() -> dict:
    if PROFILE_PATH.exists():
        with PROFILE_PATH.open(encoding="utf-8") as profile_file:
            profile = json.load(profile_file)
    elif TEXT_PROFILE_PATH.exists():
        profile = parse_text_profile(TEXT_PROFILE_PATH.read_text(encoding="utf-8"))
    else:
        raise FileNotFoundError(
            "Create profile.json from profile.example.json, or profile.txt from profile.example.txt."
        )

    validate_profile(profile)
    return profile


def validate_profile(profile: object) -> None:
    if not isinstance(profile, dict):
        raise ValueError("The profile must contain one JSON object.")
    for section in ("personal", "links", "resumes"):
        if section in profile and not isinstance(profile[section], dict):
            raise ValueError(f"{section} must be an object.")
    for section in ("education", "experience", "projects", "skills", "interests", "achievements", "application_answers"):
        if section in profile and not isinstance(profile[section], list):
            raise ValueError(f"{section} must be a list.")
    for section in ("education", "experience", "projects"):
        for item in profile.get(section, []):
            if not isinstance(item, dict):
                raise ValueError(f"Every {section} entry must be an object.")
            for field in ("graduation_date", "start_date", "end_date"):
                value = item.get(field, "")
                if value and (not isinstance(value, str) or not valid_iso_date(value)):
                    raise ValueError(f"{field} must be YYYY-MM-DD or YYYY-MM.")
            value = item.get("date", "")
            if value and (not isinstance(value, str) or not valid_project_date(value)):
                raise ValueError("project date must be YYYY, YYYY-MM, or YYYY-MM-DD.")


def parse_text_profile(text: str) -> dict:
    """Parse the intentionally small, editable profile.txt format without guessing values."""
    profile: dict[str, object] = {
        "personal": {}, "links": {}, "resumes": {}, "education": [],
        "experience": [], "projects": [], "skills": [], "interests": [], "application_answers": [],
    }
    section = ""
    current: dict[str, str] | None = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            if section not in {"personal", "links", "resumes", "education", "experience", "projects", "skills", "interests"}:
                raise ValueError(f"profile.txt line {line_number}: unknown section [{section}]")
            if section in {"education", "experience", "projects"}:
                current = {}
                profile[section].append(current)
            else:
                current = None
            continue
        if ":" not in line or not section:
            raise ValueError(f"profile.txt line {line_number}: use a [section] followed by key: value")
        key, value = (part.strip() for part in line.split(":", 1))
        if not key:
            raise ValueError(f"profile.txt line {line_number}: key cannot be empty")
        if section in {"skills", "interests"}:
            if key != "items":
                raise ValueError(f"profile.txt line {line_number}: use items: value1, value2")
            profile[section] = [item.strip() for item in value.split(",") if item.strip()]
        elif section in {"education", "experience", "projects"}:
            if current is None:
                raise ValueError(f"profile.txt line {line_number}: missing [{section}] section")
            current[key] = value
        else:
            profile[section][key] = value

    for date_section in ("education", "experience", "projects"):
        for item in profile[date_section]:
            for field in ("graduation_date", "start_date", "end_date"):
                value = item.get(field, "")
                if value and not valid_iso_date(value):
                    raise ValueError(f"profile.txt {field} must be YYYY-MM-DD or YYYY-MM")
            value = item.get("date", "")
            if value and not valid_project_date(value):
                raise ValueError("profile.txt project date must be YYYY, YYYY-MM, or YYYY-MM-DD")
    return profile


def valid_iso_date(value: str) -> bool:
    if len(value) == 7:
        try:
            year, month = (int(part) for part in value.split("-"))
        except ValueError:
            return False
        return 1 <= year <= 9999 and 1 <= month <= 12
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return len(value) == 10


def valid_project_date(value: str) -> bool:
    return (len(value) == 4 and value.isdigit()) or valid_iso_date(value)


class ProfileRequestHandler(BaseHTTPRequestHandler):
    server_version = "JobDaemonProfileService/1.0"

    def do_OPTIONS(self) -> None:
        if not self.is_extension_request():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.add_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        if not self.is_extension_request():
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        if self.path == "/api/v1/health":
            self.send_json({"api_version": "v1", "status": "ok"})
            return

        if self.path == "/api/v1/profile":
            try:
                self.send_json({"api_version": "v1", "profile": load_profile()})
            except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def is_extension_request(self) -> bool:
        return self.headers.get("Origin", "").startswith("chrome-extension://")

    def add_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
        self.send_header("Vary", "Origin")

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.add_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.client_address[0]} - {format % args}")


def main() -> None:
    try:
        server = ThreadingHTTPServer((HOST, PORT), ProfileRequestHandler)
    except OSError as error:
        if error.errno == 98:
            print(f"A profile service is already using http://{HOST}:{PORT}.")
            print("Stop the older service, then run: python filling/profile_service.py")
            return
        raise
    print(f"Profile service listening at http://{HOST}:{PORT}")
    print("1. Load filling/extension at chrome://extensions (Developer mode > Load unpacked).")
    print("2. Click the JobDaemon extension icon to open its side panel.")
    print("3. Open an application page and select Scan this application page.")
    print("The profile service only listens on this laptop and accepts Chrome extension requests.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nProfile service stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
