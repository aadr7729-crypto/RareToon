import unittest
from unittest.mock import patch

import scraper
from keyboards import episode_keyboard


class FakeResponse:
    def __init__(self, text, url):
        self.text = text
        self.url = url


class FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, url, timeout=30):
        return self.response


class HindiTrackParsingTests(unittest.TestCase):
    def test_season_and_only_hindi_dub_links_are_returned(self):
        page_url = (
            "https://example.test/anime-season-4-episodes-hindi-dubbed/"
        )
        html = """
        <html><head><title>Example Anime - Season 4 Hindi Dubbed</title></head>
        <body>
          <section>
            <h3>Episode 1</h3>
            <span>Hindi DUB -</span>
            <a href="https://codedew.com/zipper/dub-1">WatchMultiQuality</a>
          </section>
          <section>
            <h3>Episode 1</h3>
            <span>Hindi SUB -</span>
            <a href="https://codedew.com/zipper/sub-1">WatchMultiQuality</a>
          </section>
          <section>
            <h3>Episode 2</h3>
            <span>Hindi DUB -</span>
            <a href="https://codedew.com/zipper/dub-2">WatchMultiQuality</a>
          </section>
        </body></html>
        """
        response = FakeResponse(html, page_url)
        with patch.object(scraper, "_session", return_value=FakeSession(response)):
            result = scraper.get_season_episodes(page_url, "hindi")

        episodes = result["episodes"]
        self.assertEqual(
            [(episode.season, episode.episode) for episode in episodes],
            [(4, 1), (4, 2)],
        )
        self.assertEqual(
            [episode.download_url for episode in episodes],
            [
                "https://codedew.com/zipper/dub-1",
                "https://codedew.com/zipper/dub-2",
            ],
        )
        self.assertTrue(all(
            episode.audio_variant == "hindi_dub" for episode in episodes
        ))
        buttons = episode_keyboard(episodes).inline_keyboard
        self.assertTrue(buttons[0][0].text.startswith("S04 • EPISODE 1"))

    def test_unlabelled_hindi_tracks_are_not_used_as_dub_fallback(self):
        page_url = "https://example.test/season-4-hindi/"
        html = """
        <html><head><title>Example Anime Season 4</title></head><body>
          <h3>Episode 1</h3>
          <a href="https://codedew.com/zipper/ambiguous">WatchMultiQuality</a>
        </body></html>
        """
        response = FakeResponse(html, page_url)
        with patch.object(scraper, "_session", return_value=FakeSession(response)):
            result = scraper.get_season_episodes(page_url, "hindi")

        self.assertEqual(result["episodes"], [])

    def test_plain_hindi_track_on_hindi_category_page_is_treated_as_dub(self):
        page_url = (
            "https://www.rareanimes.mov/hindi/"
            "blue-box-season-2-hindi-episodes-download-hd/"
        )
        html = """
        <html>
          <head><title>Blue Box Season 2 Hindi Episodes Download HD</title></head>
          <body>
            <section>
              <p>Episode 01 – Deja Vu</p>
              <p>Hindi – [
                <a href="https://codedew.com/zipper/blue-box-1">
                  WatchMultQuality
                </a> ]
              </p>
            </section>
            <section>
              <p>Episode 02 – New Start</p>
              <p>Hindi – [
                <a href="https://codedew.com/zipper/blue-box-2">
                  WatchMultQuality
                </a> ]
              </p>
            </section>
          </body>
        </html>
        """
        response = FakeResponse(html, page_url)
        with patch.object(scraper, "_session", return_value=FakeSession(response)):
            result = scraper.get_season_episodes(page_url, "hindi")

        self.assertEqual(
            [(episode.season, episode.episode, episode.audio_variant)
             for episode in result["episodes"]],
            [(2, 1, "hindi_dub"), (2, 2, "hindi_dub")],
        )

    def test_explicit_hindi_sub_track_stays_excluded_on_hindi_category_page(self):
        page_url = "https://www.rareanimes.mov/hindi/example-season-2/"
        html = """
        <html><head><title>Example Season 2</title></head><body>
          <p>Episode 01</p>
          <p>Hindi SUB – [
            <a href="https://codedew.com/zipper/sub-1">WatchMultQuality</a> ]
          </p>
        </body></html>
        """
        response = FakeResponse(html, page_url)
        with patch.object(scraper, "_session", return_value=FakeSession(response)):
            result = scraper.get_season_episodes(page_url, "hindi")

        self.assertEqual(result["episodes"], [])

    def test_movie_with_language_heading_and_whitespace_has_hindi_episode(self):
        page_url = (
            "https://www.rareanimes.mov/hindi/"
            "doraemon-new-nobita-undersea-devil-movie-42/"
        )
        html = """
        <html>
          <head>
            <title>Doraemon New Nobita and the Castle of the Undersea
            Devil (2026) Hindi Download (Movie 42)</title>
          </head>
          <body>
            <nav>Doraemon Season 1 Season 2 Season 3</nav>
            <main>
              <p>Hindi-Line Audio – Download</p>
              <h3>
                <a href="https://codedew.com/zipper/hindi-movie">
                  WatchMultiQuality
                </a>
              </h3>
              <p>Japanese – Download</p>
              <h3>
                <a href="https://codedew.com/zipper/japanese-movie">
                  WatchMultiQuality
                </a>
              </h3>
            </main>
          </body>
        </html>
        """
        response = FakeResponse(html, page_url)
        with patch.object(scraper, "_session", return_value=FakeSession(response)):
            result = scraper.get_season_episodes(page_url, "hindi")

        self.assertEqual(
            [
                (
                    episode.season,
                    episode.episode,
                    episode.episode_type,
                    episode.audio_variant,
                    episode.download_url,
                )
                for episode in result["episodes"]
            ],
            [(0, 1, "movie", "hindi_dub",
              "https://codedew.com/zipper/hindi-movie")],
        )


if __name__ == "__main__":
    unittest.main()