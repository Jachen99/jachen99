import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / ".github" / "scripts"))

from generate_profile_cards import CardGenerationError, GitHubClient, generate_profile_cards


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FixtureClient:
    def __init__(self, fixture):
        self.fixture = fixture
        self.commit_requests = []

    def get_user(self, owner):
        self.assert_public_owner(owner)
        return self.fixture["user"]

    def list_public_owner_repositories(self, owner):
        self.assert_public_owner(owner)
        return [repo for repo in self.fixture["repositories"] if not repo["private"]]

    def commit_count(self, owner, repository):
        self.assert_public_owner(owner)
        self.commit_requests.append(repository)
        return self.fixture["commit_counts"][repository]

    @staticmethod
    def assert_public_owner(owner):
        if owner != "Jachen99":
            raise AssertionError(f"unexpected owner: {owner}")


class GenerateProfileCardsTest(unittest.TestCase):
    def setUp(self):
        fixture_path = REPOSITORY_ROOT / "tests" / "fixtures" / "public_profile.json"
        self.fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    def test_generates_only_two_dark_cards_from_public_fixture_data(self):
        client = FixtureClient(self.fixture)
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir) / "profile-summary-card-output"
            stale_file = output_root / "other-theme" / "README.md"
            stale_file.parent.mkdir(parents=True)
            stale_file.write_text("stale", encoding="utf-8")

            generate_profile_cards("Jachen99", output_root, client=client)

            generated = sorted(
                path.relative_to(output_root).as_posix()
                for path in output_root.rglob("*.svg")
            )
            self.assertEqual(
                generated,
                [
                    "github_dark/2-most-commit-language.svg",
                    "github_dark/3-stats.svg",
                ],
            )
            self.assertFalse(stale_file.exists())
            self.assertEqual(client.commit_requests, ["public-java", "public-web"])

            stats = (output_root / "github_dark" / "3-stats.svg").read_text(encoding="utf-8")
            languages = (
                output_root / "github_dark" / "2-most-commit-language.svg"
            ).read_text(encoding="utf-8")
            self.assertIn("Public repositories", stats)
            self.assertIn("11", stats)
            self.assertIn("JavaScript &amp; XML", languages)
            self.assertNotIn("private-never-read", stats + languages)
            self.assertNotIn("<script", stats + languages)

    def test_client_paginates_public_repository_results_and_uses_last_commit_page(self):
        first_page = [
            {"name": f"repository-{index}", "private": False, "visibility": "public"}
            for index in range(100)
        ]
        second_page = [
            {"name": "private", "private": True, "visibility": "private"},
            {"name": "repository-100", "private": False, "visibility": "public"},
        ]

        def opener(request, timeout):
            self.assertEqual(timeout, 15)
            page = parse_qs(urlparse(request.full_url).query).get("page", [""])[0]
            if page == "1":
                return FakeResponse(first_page)
            if page == "2":
                return FakeResponse(second_page)
            if "/commits?" in request.full_url:
                return FakeResponse([{"sha": "one"}], {"Link": '<x?page=9>; rel="last"'})
            raise AssertionError(request.full_url)

        client = GitHubClient(opener=opener)
        repositories = client.list_public_owner_repositories("Jachen99")

        self.assertEqual(len(repositories), 101)
        self.assertEqual(repositories[-1]["name"], "repository-100")
        self.assertEqual(client.commit_count("Jachen99", "repository-100"), 9)

    def test_client_fails_fast_when_a_public_api_request_fails(self):
        def opener(request, timeout):
            raise OSError("offline")

        with self.assertRaises(CardGenerationError):
            GitHubClient(opener=opener).get_user("Jachen99")


if __name__ == "__main__":
    unittest.main()
