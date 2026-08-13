#!/usr/bin/env python3
"""Generate repository-local profile cards from public GitHub REST data."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape


API_ROOT = "https://api.github.com"
TIMEOUT_SECONDS = 15


class CardGenerationError(RuntimeError):
    """Raised when GitHub public data cannot be retrieved safely."""


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        opener: Callable[..., Any] = urlopen,
        timeout: int = TIMEOUT_SECONDS,
    ) -> None:
        self.token = token
        self.opener = opener
        self.timeout = timeout

    def get_user(self, owner: str) -> dict[str, Any]:
        data, _ = self._get_json(f"/users/{quote(owner, safe='')}")
        if not isinstance(data, dict):
            raise CardGenerationError("GitHub user response was not an object")
        return data

    def list_public_owner_repositories(self, owner: str) -> list[dict[str, Any]]:
        repositories: list[dict[str, Any]] = []
        page = 1
        while True:
            data, _ = self._get_json(
                f"/users/{quote(owner, safe='')}/repos?type=owner&per_page=100&page={page}"
            )
            if not isinstance(data, list):
                raise CardGenerationError("GitHub repositories response was not a list")
            public_page = [
                repository
                for repository in data
                if isinstance(repository, dict)
                and repository.get("private") is False
                and repository.get("visibility", "public") == "public"
            ]
            repositories.extend(public_page)
            if len(data) < 100:
                return repositories
            page += 1

    def commit_count(self, owner: str, repository: str) -> int:
        data, headers = self._get_json(
            f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/commits?per_page=1"
        )
        if not isinstance(data, list):
            raise CardGenerationError("GitHub commits response was not a list")
        if not data:
            return 0
        link_header = headers.get("Link", "")
        last_page = re.search(r"[?&]page=(\d+)[^>]*>;\s*rel=\"last\"", link_header)
        return int(last_page.group(1)) if last_page else len(data)

    def _get_json(self, path: str) -> tuple[Any, dict[str, str]]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "github-profile-card-generator",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(f"{API_ROOT}{path}", headers=headers)
        try:
            with self.opener(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
                response_headers = dict(response.headers.items())
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise CardGenerationError(f"GitHub API request failed for {path}: {error}") from error
        try:
            return json.loads(payload), response_headers
        except json.JSONDecodeError as error:
            raise CardGenerationError(f"GitHub API returned invalid JSON for {path}") from error


def generate_profile_cards(
    owner: str,
    output_root: Path,
    token: str | None = None,
    client: Any | None = None,
) -> None:
    github = client or GitHubClient(token=token)
    github.get_user(owner)
    repositories = github.list_public_owner_repositories(owner)

    total_stars = sum(int(repository.get("stargazers_count", 0)) for repository in repositories)
    total_forks = sum(int(repository.get("forks_count", 0)) for repository in repositories)
    total_commits = sum(
        github.commit_count(owner, str(repository["name"])) for repository in repositories
    )
    languages: dict[str, int] = {}
    for repository in repositories:
        language = repository.get("language")
        if isinstance(language, str) and language:
            languages[language] = languages.get(language, 0) + 1

    if output_root.exists():
        shutil.rmtree(output_root)
    dark_output = output_root / "github_dark"
    dark_output.mkdir(parents=True)
    (dark_output / "3-stats.svg").write_text(
        render_stats_card(owner, len(repositories), total_commits, total_stars, total_forks),
        encoding="utf-8",
    )
    (dark_output / "2-most-commit-language.svg").write_text(
        render_languages_card(owner, languages), encoding="utf-8"
    )


def render_stats_card(
    owner: str, repositories: int, commits: int, stars: int, forks: int
) -> str:
    rows = [
        ("Public repositories", repositories),
        ("Public commits", commits),
        ("Stars received", stars),
        ("Forks", forks),
    ]
    rendered_rows = "".join(
        f'<text x="38" y="{102 + index * 34}" class="label">{escape(label)}</text>'
        f'<text x="610" y="{102 + index * 34}" class="value" text-anchor="end">{value}</text>'
        for index, (label, value) in enumerate(rows)
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="650" height="260" viewBox="0 0 650 260" role="img" aria-labelledby="title">
  <title id="title">{escape(owner)} public GitHub statistics</title>
  <style>.card{{fill:#0d1117;stroke:#30363d}}.heading{{fill:#f0f6fc;font:700 24px sans-serif}}.subheading{{fill:#8b949e;font:14px sans-serif}}.label{{fill:#c9d1d9;font:16px sans-serif}}.value{{fill:#58a6ff;font:700 18px sans-serif}}.rule{{stroke:#21262d}}</style>
  <rect class="card" x="1" y="1" width="648" height="258" rx="12"/>
  <text class="heading" x="38" y="50">{escape(owner)} · GitHub statistics</text>
  <text class="subheading" x="38" y="76">Public data · refreshed weekly</text>
  <line class="rule" x1="38" y1="86" x2="612" y2="86"/>
  {rendered_rows}
</svg>\n'''


def render_languages_card(owner: str, languages: dict[str, int]) -> str:
    ranked_languages = sorted(languages.items(), key=lambda item: (-item[1], item[0]))[:5]
    maximum = max((count for _, count in ranked_languages), default=1)
    rows = "".join(
        f'<text x="38" y="{112 + index * 30}" class="label">{escape(language)}</text>'
        f'<rect x="275" y="{98 + index * 30}" width="{int(260 * count / maximum)}" height="14" rx="7" class="bar"/>'
        f'<text x="570" y="{112 + index * 30}" class="value" text-anchor="end">{count} repos</text>'
        for index, (language, count) in enumerate(ranked_languages)
    ) or '<text x="38" y="112" class="label">No public repository languages reported</text>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="650" height="260" viewBox="0 0 650 260" role="img" aria-labelledby="title">
  <title id="title">{escape(owner)} public repository languages</title>
  <style>.card{{fill:#0d1117;stroke:#30363d}}.heading{{fill:#f0f6fc;font:700 24px sans-serif}}.subheading{{fill:#8b949e;font:14px sans-serif}}.label{{fill:#c9d1d9;font:16px sans-serif}}.value{{fill:#58a6ff;font:14px sans-serif}}.bar{{fill:#238636}}</style>
  <rect class="card" x="1" y="1" width="648" height="258" rx="12"/>
  <text class="heading" x="38" y="50">{escape(owner)} · Languages</text>
  <text class="subheading" x="38" y="76">Public repositories by primary language</text>
  {rows}
</svg>\n'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        generate_profile_cards(
            arguments.username,
            arguments.output_dir,
            token=os.environ.get("GITHUB_TOKEN"),
        )
    except CardGenerationError as error:
        print(f"profile card generation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
