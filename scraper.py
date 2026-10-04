#This Bot Is Created By Shivam, Thanks To Shivam For Providing Repo
import os
import requests
from bs4 import BeautifulSoup
import re
import base64
import json
import urllib.parse
import time

from config import BASE_URL, CODEDEW_BASE


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    # Requests handles gzip/deflate here. Advertising Brotli without a
    # Brotli decoder makes the response look like binary data to BeautifulSoup.
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

AVAILABLE_LANGS = {"hindi", "tamil", "telugu", "bengali", "malayalam", "english"}


class AnimeResult:
    def __init__(self, title, url, languages=None, status="unknown", episode_count=0):
        self.title = title
        self.url = url
        self.languages = languages or []
        self.status = status
        self.episode_count = episode_count

    def to_dict(self):
        return {
            "title": self.title,
            "url": self.url,
            "languages": self.languages,
            "status": self.status,
            "episode_count": self.episode_count,
        }


class EpisodeInfo:
    def __init__(
        self, season, episode, download_url, title="", language="hindi",
        episode_type="episode", audio_variant="",
    ):
        self.season = season
        self.episode = episode
        self.download_url = download_url
        self.title = title
        self.language = language
        self.episode_type = episode_type
        self.audio_variant = audio_variant

    def to_dict(self):
        return {
            "season": self.season,
            "episode": self.episode,
            "download_url": self.download_url,
            "title": self.title,
            "language": self.language,
            "episode_type": self.episode_type,
            "audio_variant": self.audio_variant,
        }


class DownloadLink:
    def __init__(self, quality, size, url):
        self.quality = quality
        self.size = size
        self.url = url

    def to_dict(self):
        return {"quality": self.quality, "size": self.size, "url": self.url}


def _session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _search_tokens(value):
    """Return case-insensitive words used for search matching."""
    return re.findall(r"[^\W_]+", value.casefold(), re.UNICODE)


def _matches_search(query, title, href):
    """Keep only result cards that actually match the requested anime."""
    query_tokens = _search_tokens(query)
    result_tokens = _search_tokens(f"{title} {href.replace('-', ' ')}")
    if not query_tokens:
        return False

    for token in query_tokens:
        if len(token) <= 2:
            if token.isdigit():
                matched = any(
                    candidate.lstrip("0") == token.lstrip("0")
                    for candidate in result_tokens
                )
            else:
                matched = token in result_tokens
        else:
            # The site returns "Overgeared" for a search for "Overgear".
            matched = any(
                candidate == token or candidate.startswith(token)
                for candidate in result_tokens
            )
        if not matched:
            return False
    return True


def anime_title_matches(title, page_reference):
    """Check that a season-page URL belongs to the requested anime."""
    path = urllib.parse.urlparse(str(page_reference or "")).path or str(page_reference or "")
    path = urllib.parse.unquote(path).replace("-", " ").replace("_", " ")
    title = str(title or "").casefold()
    path = path.casefold()
    ignored = {
        "season", "episode", "episodes", "ep", "s", "e",
        "hindi", "tamil", "telugu", "bengali", "malayalam", "english",
        "dub", "dubbed", "sub", "subbed", "download", "watch", "hd",
        "rare", "toons", "india", "anime", "all",
    }
    title_tokens = [
        token for token in _search_tokens(title)
        if token not in ignored and not token.isdigit()
    ]
    page_tokens = set(_search_tokens(path))
    if not title_tokens:
        return True
    return all(token in page_tokens for token in title_tokens)


def _hindi_page_audio_variant(text):
    """Recognize multi-language titles such as 'Hindi - Tamil - Telugu Dubbed'."""
    variant = _hindi_audio_variant(text)
    if variant:
        return variant
    match = re.search(r"\bhindi\b", text or "", re.IGNORECASE)
    if not match:
        return ""
    rest = (text or "")[match.end():]
    next_variant = re.search(
        r"\b(Dub(?:bed)?|Sub(?:bed)?)\b", rest, re.IGNORECASE
    )
    if not next_variant:
        return ""
    value = next_variant.group(1).lower()
    return "dub" if value.startswith("dub") else "sub"


def _has_language_path(url, language):
    """Return whether the source URL uses the site's language-specific path."""
    path_parts = urllib.parse.urlparse(str(url or "")).path.split("/")
    return language.casefold() in {part.casefold() for part in path_parts if part}


def search_anime(query, language=None):
    """Search for anime on rareanimes.mov.
    Returns list of AnimeResult objects."""
    normalized_query = " ".join(query.split())
    cache_key = f"search:v2:{normalized_query.casefold()}:{language or 'all'}"
    from database import cache_get
    cached = cache_get(cache_key)
    if cached:
        try:
            data = json.loads(cached)
            return [AnimeResult(**d) for d in data]
        except Exception:
            pass

    s = _session()
    url = f"{BASE_URL}/?s={urllib.parse.quote(normalized_query)}"
    
    lang_prefix = f"/{language}/" if language else "/hindi/"
    
    try:
        r = s.get(url, timeout=30, allow_redirects=True)
    except Exception:
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []
    seen_urls = set()

    def add_result(title, href):
        if not href or not href.startswith(BASE_URL):
            return
        if href in seen_urls:
            return
        if any(kw in href for kw in [
            "/wp-content/", "/tag/", "/category/", "/author/", "/dmca",
            "/privacy", "/contact", "/cookie", "/terms", "/request-shows",
            "/dead-link",
        ]):
            return
        title = re.sub(r"\s+", " ", title).strip()
        if not title or title in ("Search", "Next Page", "Surprise!"):
            return
        if not re.search(r"(anime|episode|season|dubbed|download|hd)", href + title, re.IGNORECASE):
            return
        if not _matches_search(normalized_query, title, href):
            return
        seen_urls.add(href)
        results.append(AnimeResult(
            title=title,
            url=href,
            languages=[],
            status="unknown",
            episode_count=0,
        ))

    # Search results are published as article cards. Reading their title links
    # avoids collecting unrelated navigation and "latest posts" links.
    for article in soup.find_all("article"):
        title_el = article.select_one(".entry-title")
        if not title_el:
            continue
        link = title_el.find("a", href=True) or article.find("a", href=True)
        if link:
            add_result(title_el.get_text(" ", strip=True), link["href"])

    # Keep a constrained fallback for themes that omit article wrappers.
    if not results:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(" ", strip=True)
            if not text or len(text) < 3:
                continue
            add_result(text, href)

    if results:
        from database import cache_set
        cache_set(cache_key, json.dumps([r.to_dict() for r in results]), ttl=600)

    return results


def get_ongoing_anime_catalog(language, max_pages=3, limit=60):
    """Return recent posts from the site's language-specific ongoing tag feed."""
    language = (language or "").lower()
    if language not in AVAILABLE_LANGS:
        return []

    cache_key = f"ongoing-catalog:v1:{language}:{max_pages}:{limit}"
    from database import cache_get
    cached = cache_get(cache_key)
    if cached:
        try:
            return json.loads(cached)
        except (TypeError, ValueError):
            pass

    session = _session()
    current_url = f"{BASE_URL}/{language}/tag/ongoing/"
    visited = set()
    results = []
    while current_url and current_url not in visited and len(visited) < max_pages:
        visited.add(current_url)
        try:
            response = session.get(current_url, timeout=30, allow_redirects=True)
        except Exception:
            break
        if response.status_code >= 400:
            break
        soup = BeautifulSoup(response.text, "lxml")
        for article in soup.select("article"):
            heading = article.select_one("h1 a[href], h2 a[href], h3 a[href]")
            if not heading:
                continue
            title = heading.get_text(" ", strip=True)
            url = heading.get("href", "")
            if (
                not title or not url.startswith(BASE_URL)
                or "/category/" in url or "/tag/" in url
            ):
                continue
            if any(item.get("url") == url for item in results):
                continue
            results.append({"title": title, "url": url, "language": language})
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break
        next_link = soup.select_one(
            "a.next[href], a[rel='next'][href], .pagination a[href]"
        )
        current_url = next_link.get("href") if next_link else None

    if results:
        from database import cache_set
        cache_set(cache_key, json.dumps(results), ttl=900)
    return results


_OTT_PROVIDERS = (
    ("Crunchyroll", r"\bcrunchyroll\b", "https://www.crunchyroll.com/search?q="),
    ("Netflix", r"\bnetflix\b", "https://www.netflix.com/search?q="),
    ("Prime Video", r"\b(?:amazon\s+)?prime\s+video\b|\bamazon\b", "https://www.primevideo.com/search/ref=atv_nb_sr?phrase="),
    ("Disney+", r"\bdisney\s*\+", "https://www.disneyplus.com/search?q="),
    ("Hulu", r"\bhulu\b", "https://www.hulu.com/search?q="),
    ("Bilibili", r"\bbilibili\b", "https://www.bilibili.tv/en/search-result?q="),
    ("Muse Asia", r"\bmuse\s+asia\b", "https://www.youtube.com/results?search_query="),
    ("Ani-One Asia", r"\bani[\s-]?one(?:\s+asia)?\b", "https://www.youtube.com/results?search_query="),
)


def _extract_series_metadata(soup):
    """Read the series facts shown in the anime site's post content."""
    content = soup.select_one(".entry-content") or soup
    paragraphs = [
        re.sub(r"\s+", " ", item.get_text(" ", strip=True)).strip()
        for item in content.find_all("p")
        if item.get_text(" ", strip=True)
    ]
    summary = paragraphs[0] if paragraphs else ""
    info_text = " ".join(paragraphs)
    combined = f"{summary} {info_text}"

    def labeled_value(label):
        match = re.search(
            rf"\b{label}\s*:\s*(.*?)(?=\s+(?:🍂Season|🎞Episodes|🕓Release Year|"
            rf"⌛RunTime|🎭\s*Genre|🔊Language|🎬Quality|Credit|Synopsis)\s*:|$)",
            info_text,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\s+", " ", match.group(1)).strip(" :") if match else ""

    season_text = labeled_value(r"(?:🍂\s*)?Season")
    season_match = re.search(r"\d+", season_text)
    episode_text = labeled_value(r"(?:🎞\s*)?Episodes?")
    if not episode_text:
        episode_match = re.search(
            r"\b(?:this\s+season\s+has|season\s+has)\s+(\d+|ongoing)\s+episodes?\b",
            summary,
            re.IGNORECASE,
        )
        episode_text = episode_match.group(1) if episode_match else ""
    if re.search(r"\bongoing\b", episode_text, re.IGNORECASE):
        episode_count = "Ongoing"
    else:
        episode_count = int(re.search(r"\d+", episode_text).group()) if re.search(
            r"\d+", episode_text
        ) else ""

    genre_text = labeled_value(r"(?:🎭\s*)?Genres?")
    if not genre_text:
        genre_match = re.search(
            r"\bbased on\s+(.+?)(?:\.|$)", summary, re.IGNORECASE
        )
        genre_text = genre_match.group(1).strip() if genre_match else ""

    quality_field = labeled_value(r"(?:🎬\s*)?Quality")
    quality_source = f"{summary} {quality_field}"
    quality_labels = []
    for match in re.finditer(
        r"(?<!\d)(360|480|720|1080|1440|2160)\s*P?\b|(?<!\d)4\s*K\b",
        quality_source,
        re.IGNORECASE,
    ):
        quality = (
            "4K" if match.group(0).upper().replace(" ", "") == "4K"
            else f"{match.group(1)}P"
        )
        if quality not in quality_labels:
            quality_labels.append(quality)
    dual_audio = bool(re.search(r"\bdual(?:\s+audio)?\b", summary, re.IGNORECASE))
    quality_text = ", ".join(quality_labels)
    if dual_audio:
        quality_text = f"{quality_text} [DUAL]" if quality_text else "[DUAL]"

    provider = ""
    provider_url = ""
    for name, pattern, search_url in _OTT_PROVIDERS:
        if re.search(pattern, summary, re.IGNORECASE):
            provider = name
            provider_url = search_url + urllib.parse.quote_plus(
                re.sub(r"\s+", " ", summary.split("Season", 1)[0]).strip()
            )
            break
    if provider:
        title_match = re.search(
            r"\bdownload\s+(.+?)\s+season\s*\d+\b", summary, re.IGNORECASE
        )
        search_term = (
            title_match.group(1).strip()
            if title_match
            else re.sub(r"\s+", " ", summary.split("Season", 1)[0]).strip()
        )
        for name, pattern, search_url in _OTT_PROVIDERS:
            if re.search(pattern, summary, re.IGNORECASE):
                provider_url = search_url + urllib.parse.quote_plus(search_term)
                break
        for anchor in content.find_all("a", href=True):
            href = anchor["href"]
            if re.search(provider.split()[0], href, re.IGNORECASE):
                provider_url = urllib.parse.urljoin(BASE_URL, href)
                break

    return {
        "season_number": int(season_match.group()) if season_match else None,
        "episode_count": episode_count,
        "quality_labels": quality_text,
        "genres": [
            item.strip()
            for item in re.split(
                r"\s*,\s*|\s+and\s+", genre_text.replace(", and ", ", ")
            )
            if item.strip()
        ],
        "ott_name": provider,
        "ott_url": provider_url,
    }


def get_anime_page_info(url):
    """Fetch an anime page and extract metadata.
    Returns dict with: title, status, languages, categories, season_page_url"""
    s = _session()
    
    try:
        r = s.get(url, timeout=30)
    except Exception as e:
        return {"error": str(e)}
    
    soup = BeautifulSoup(r.text, "lxml")
    
    info = {
        "title": "",
        "status": "unknown",
        "languages": [],
        "categories": [],
        "watch_multiquality_url": None,
        "watch_now_urls": [],
        "all_season_links": [],
        "is_season_page": False,
        "episode_count": 0,
    }
    
    title_tag = soup.find("title")
    if title_tag:
        info["title"] = title_tag.get_text(strip=True)
    
    # Check for ongoing/completed
    text_lower = r.text.lower()
    categories = []
    
    # Look for category links in the post header
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if "/category/" in href:
            categories.append(text.strip())
        elif "/tag/" in href:
            categories.append(text.strip())
    
    info["categories"] = categories
    
    if any("ongoing" in c.lower() for c in categories) or "ongoing" in url.lower():
        info["status"] = "ongoing"
    elif any("completed" in c.lower() for c in categories):
        info["status"] = "completed"
    elif "ongoing" in text_lower:
        info["status"] = "ongoing"
    elif "completed" in text_lower:
        info["status"] = "completed"
    
    # Extract languages from URL and categories
    lang_match = re.match(rf"{re.escape(BASE_URL)}/([^/]+)/", url)
    if lang_match:
        path_lang = lang_match.group(1).lower()
        if path_lang in AVAILABLE_LANGS:
            info["languages"].append(path_lang)
    
    for cat in categories:
        cat_lower = cat.lower()
        for lang in AVAILABLE_LANGS:
            if lang in cat_lower:
                if lang not in info["languages"]:
                    info["languages"].append(lang)
    
    # Find all WatchMultiQuality and WatchNow links (codedew.com)
    multiquality_links = []
    watchnow_links = []
    hubcloud_links = []
    mega_links = []
    dlbeta_links = []
    other_links = []
    
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        
        if "codedew.com" in href:
            if "WatchMultiQuality" in text or "watchmulti" in text.lower() or "mult" in text.lower():
                multiquality_links.append({"text": text, "url": href})
            elif "WatchNow" in text or "watch" in text.lower():
                watchnow_links.append({"text": text, "url": href})
            else:
                other_links.append({"text": text, "url": href})
        elif "hubcloud" in text.lower() or "hub" in text.lower():
            hubcloud_links.append({"text": text, "url": href})
        elif "mega" in text.lower():
            mega_links.append({"text": text, "url": href})
        elif "dl" in text.lower() or "beta" in text.lower():
            dlbeta_links.append({"text": text, "url": href})
    
    info["watch_multiquality_url"] = multiquality_links[0]["url"] if multiquality_links else None
    info["watch_now_urls"] = watchnow_links
    info["multiquality_links"] = multiquality_links
    info["hubcloud_links"] = hubcloud_links
    info["mega_links"] = mega_links
    info["dlbeta_links"] = dlbeta_links
    
    # Detect if this is a season listing page or a single episode page
    # Season pages have many episode download links
    total_dl_links = len(multiquality_links) + len(watchnow_links)
    info["is_season_page"] = total_dl_links > 1
    info["episode_count"] = total_dl_links
    
    # Check for all-season links (other seasons of the same anime)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if "rareanimes.mov" in href and ("season" in text.lower() or "all episodes" in text.lower()):
            if "all episodes" in text.lower() or re.search(r"season\s*\d+", text, re.IGNORECASE):
                if (
                    href != url
                    and anime_title_matches(info["title"] or url, href)
                ):
                    info["all_season_links"].append({"text": text, "url": href})
    
    info["series_metadata"] = _extract_series_metadata(soup)
    return info


def get_multiquality_episodes(multiquality_url):
    """Fetch the codedew multiquality page and extract episode list.
    Follows Next/Previous navigation to enumerate all episodes.
    Returns list of EpisodeInfo objects."""
    s = _session()
    
    try:
        r = s.get(multiquality_url, timeout=30, allow_redirects=True)
    except Exception:
        return []
    
    episodes = []
    visited_urls = set()
    current_url = r.url
    
    max_iterations = 200
    iteration = 0
    
    while current_url and iteration < max_iterations:
        iteration += 1
        if current_url in visited_urls:
            break
        visited_urls.add(current_url)
        
        try:
            r = s.get(current_url, timeout=30, allow_redirects=True)
        except Exception:
            break
        
        # The final URL might differ
        current_url = r.url
        
        soup = BeautifulSoup(r.text, "lxml")
        
        # Extract episode info from the main download button
        btn = soup.find("a", class_="btn-dl") or soup.find("a", id="mainActionBtn")
        episode_text = ""
        episode_url = None
        
        if btn:
            episode_text = btn.get_text(strip=True)
            episode_url = btn.get("href", "")
        
        # Also look for the title/heading
        title_el = soup.find("title")
        page_title = title_el.get_text(strip=True) if title_el else ""
        
        # Parse episode number from text like "S16 E11".
        season, episode = _parse_episode_info(episode_text, page_title)
        
        # Extract language from the page
        language = "hindi"
        for cat in ["Hindi", "Tamil", "Telugu", "Malayalam", "Bengali"]:
            if cat in r.text:
                language = cat.lower()
                break
        
        # Extract episode type (movie, special, etc.)
        episode_type = "episode"
        if "movie" in page_title.lower() or "Movie" in page_title:
            episode_type = "movie"
        
        if episode_url and "javascript" not in episode_url:
            audio_variant = _hindi_audio_variant(
                f"{page_title} {soup.get_text(' ', strip=True)}"
            )
            episodes.append(EpisodeInfo(
                season=season,
                episode=episode,
                download_url=episode_url,
                title=page_title,
                language=language,
                episode_type=episode_type,
                audio_variant=f"hindi_{audio_variant}" if audio_variant else "",
            ))
        
        # Find next page URL
        next_url = _find_next_url(soup)
        if not next_url:
            break
        
        if not next_url.startswith("http"):
            if next_url.startswith("/"):
                next_url = f"{CODEDEW_BASE}{next_url}"
            else:
                next_url = f"{current_url.rsplit('/', 1)[0]}/{next_url}"
        
        current_url = next_url
    
    return episodes


_LANGUAGE_TRACK_RE = re.compile(
    r"\b(Hindi|Tamil|Telugu|Malayalam|Bengali|English)\b"
    r"(?:\s*[-–—:]?\s*(Dub(?:bed)?|Sub(?:bed)?))?",
    re.IGNORECASE,
)


def _hindi_audio_variant(text):
    """Return 'dub'/'sub' only when the text explicitly identifies the Hindi track."""
    match = re.search(
        r"\bHindi\b\s*(?:[-–—:]?\s*)?(Dub(?:bed)?|Sub(?:bed)?)\b"
        r"|\b(Dub(?:bed)?|Sub(?:bed)?)\s+in\s+Hindi\b",
        text or "",
        re.IGNORECASE,
    )
    if not match:
        return ""
    value = (match.group(1) or match.group(2)).lower()
    return "dub" if value.startswith("dub") else "sub"


def _track_for_link(anchor):
    """Read the language/audio label immediately associated with a download link."""
    inspected_text_nodes = 0
    for previous_text in anchor.find_all_previous(string=True):
        previous_text = str(previous_text).strip()
        if not previous_text:
            continue
        inspected_text_nodes += 1
        match = _LANGUAGE_TRACK_RE.search(previous_text)
        if match:
            language = match.group(1).lower()
            variant = (match.group(2) or "").lower()
            return language, (
                "dub" if variant.startswith("dub")
                else "sub" if variant.startswith("sub")
                else ""
            )
        # Track headings may be separated from their download buttons by
        # several host links, but don't inherit a label from unrelated page
        # content when there is no nearby track heading.
        if inspected_text_nodes >= 12:
            break

    for parent in anchor.parents:
        if getattr(parent, "name", None) in {"body", "html"}:
            break
        matches = list(_LANGUAGE_TRACK_RE.finditer(
            parent.get_text(" ", strip=True)
        ))
        if len(matches) == 1:
            match = matches[0]
            language = match.group(1).lower()
            variant = (match.group(2) or "").lower()
            return language, (
                "dub" if variant.startswith("dub")
                else "sub" if variant.startswith("sub")
                else ""
            )
    return "", ""


def _season_from_text(text):
    match = re.search(r"\bSeason\s*[-:#]?\s*(\d+)\b", text or "", re.IGNORECASE)
    if not match:
        match = re.search(r"\bS\s*[-:#]?\s*(\d+)\b", text or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def _episode_from_text(text):
    text = text or ""
    match = re.search(r"\bS\s*(\d+)\s*[- .]?\s*E(?:P)?\s*(\d+)\b", text, re.I)
    if match:
        return int(match.group(2))
    match = re.search(r"\b(?:Episode|EP)\s*[-:#]?\s*(\d+)\b", text, re.I)
    if not match:
        match = re.search(r"\bE\s*[-:#]?\s*(\d+)\b", text, re.I)
    return int(match.group(1)) if match else None


def _episode_number_for_link(anchor):
    for previous in anchor.previous_elements:
        if isinstance(previous, str):
            episode = _episode_from_text(str(previous))
            if episode is not None:
                return episode
    return None


def _parse_episode_info(text, fallback_title=""):
    """Parse season and episode markers without mistaking an episode count for an episode."""
    combined = f"{text or ''} {fallback_title or ''}"
    season = _season_from_text(combined) or 1
    episode = _episode_from_text(combined) or 1
    return season, episode


def _find_next_url(soup):
    """Find the 'Next' navigation URL from the multiquality page."""
    # Look for onclick handlers with location.href
    for a in soup.find_all("a", onclick=True):
        onclick = a.get("onclick", "")
        if "location.href" in onclick:
            m = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", onclick)
            if m:
                href = m.group(1)
                if "next" in a.get("id", "").lower() or "next" in a.get("class", ""):
                    return href
                if a.get("id") == "nextBtn":
                    return href
    
    for a in soup.find_all("a", href=True):
        onclick = a.get("onclick", "")
        if onclick and "location.href" in onclick:
            m = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", onclick)
            if m and "next" in a.get("id", "").lower():
                return m.group(1)
    
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        onclick = a.get("onclick", "")
        if ("next" in text or "next" in a.get("id", "").lower()) and onclick:
            m = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", onclick)
            if m:
                return m.group(1)
    
    return None


def resolve_download_url(zipper_url):
    """Follow the codedew 3-step download flow to get the final download page URL and API data.
    Returns dict with: argon_url, token, links_route, video_id, episode_title
    """
    s = _session()
    
    try:
        r = s.get(zipper_url, timeout=30, allow_redirects=True)
    except Exception as e:
        return {"error": str(e)}
    
    soup = BeautifulSoup(r.text, "lxml")

    # Episode links now redirect to a multiquality page first. Its primary
    # action points to the actual three-step zipper flow.
    multiquality_btn = soup.find("a", id="mainActionBtn", href=True)
    if multiquality_btn:
        try:
            r = s.get(
                urllib.parse.urljoin(r.url, multiquality_btn["href"]),
                timeout=30,
                allow_redirects=True,
            )
        except Exception as e:
            return {"error": str(e)}
        soup = BeautifulSoup(r.text, "lxml")

    iframe_result = _extract_argon_iframe(r, s)
    if iframe_result is not None:
        return iframe_result
    
    # Step 1: Find the data-href on the goBtn / main button
    btn = soup.find("a", id="goBtn") or soup.find("a", {"data-href": True})
    if not btn:
        btn = soup.find("a", class_="btn")
    if not btn:
        # Try to find any link or button
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if "zipper" in href and "ad_done" in href:
                btn = a
                break
    
    if not btn:
        return {"error": "No download button found on step 1 page"}
    
    data_href = btn.get("data-href", "")
    if not data_href:
        # Try href attribute
        data_href = btn.get("href", "")
    
    if not data_href:
        return {"error": "No data-href found on step 1 button"}
    
    # Step 2: Follow the data-href
    step2_url = data_href if data_href.startswith("http") else f"{CODEDEW_BASE}{data_href}"
    
    try:
        r2 = s.get(step2_url, timeout=30, allow_redirects=True)
    except Exception as e:
        return {"error": str(e)}
    
    soup2 = BeautifulSoup(r2.text, "lxml")
    
    # Find the next data-href (step 2 button)
    btn2 = soup2.find("a", {"data-href": True})
    if not btn2:
        for a in soup2.find_all("a"):
            href = a.get("href", "")
            if "ziptron" in href:
                btn2 = a
                break
    
    if not btn2:
        return {"error": "No download button found on step 2 page"}
    
    data_href2 = btn2.get("data-href", "")
    if not data_href2:
        data_href2 = btn2.get("href", "")
    
    step3_url = data_href2 if data_href2.startswith("http") else f"{CODEDEW_BASE}{data_href2}"
    
    # Step 3: Fetch the ziptron page (which auto-submits a form to liptron.php)
    try:
        r3 = s.get(step3_url, timeout=30, allow_redirects=True)
    except Exception as e:
        return {"error": str(e)}
    
    soup3 = BeautifulSoup(r3.text, "lxml")
    
    # Find the form action and hidden input
    form = soup3.find("form", id="landing")
    if not form:
        form = soup3.find("form")
    
    if not form:
        # The page might already be the argon page or a redirect
        if "argon.razorshell" in r3.text or "juicyData" in r3.text:
            return _extract_argon_data(r3, s)
        iframe_result = _extract_argon_iframe(r3, s)
        if iframe_result is not None:
            return iframe_result
        return {"error": "No form found on step 3 page"}
    
    form_action = form.get("action", "")
    rtiwatch = form.find("input", {"name": "rtiwatch"})
    rtiwatch_value = rtiwatch.get("value", "") if rtiwatch else ""
    
    # POST to liptron.php
    post_url = form_action if form_action.startswith("http") else f"{CODEDEW_BASE}{form_action}"
    
    form_data = {}
    for inp in form.find_all("input", {"name": True, "value": True}):
        form_data[inp["name"]] = inp["value"]
    
    try:
        r4 = s.post(post_url, data=form_data, timeout=30, allow_redirects=True)
    except Exception as e:
        return {"error": str(e)}
    
    # The current final page embeds the Argon page in an iframe.
    iframe_result = _extract_argon_iframe(r4, s)
    if iframe_result is not None:
        return iframe_result

    # Older responses embedded juicyData directly.
    return _extract_argon_data(r4, s)


def _extract_argon_iframe(response, session):
    """Extract Argon data when the download page embeds it in an iframe."""
    soup = BeautifulSoup(response.text, "lxml")
    iframe = soup.find("iframe", src=re.compile(r"argon\.razorshell\.space"))
    if not iframe:
        return None

    iframe_url = urllib.parse.urljoin(response.url, iframe["src"])
    try:
        iframe_response = session.get(iframe_url, timeout=30, allow_redirects=True)
    except Exception as e:
        return {"error": f"Could not load download iframe: {e}"}
    return _extract_argon_data(iframe_response, session)


def _extract_argon_data(response, session):
    """Extract token, video ID, and API routes from the argon.razorshell.space page."""
    r = response
    soup = BeautifulSoup(r.text, "lxml")
    
    result = {
        "argon_url": r.url,
        "token": "",
        "video_id": "",
        "links_route": "",
        "ping_route": "",
        "episode_title": "",
    }
    
    # Get title
    title_tag = soup.find("title")
    if title_tag:
        result["episode_title"] = title_tag.get_text(strip=True)
    
    # Find juicyData
    m = re.search(r'window\.juicyData\s*=\s*(\{.*?\})\s*</script>', r.text, re.DOTALL)
    if m:
        json_str = m.group(1)
        # Fix escaped slashes
        json_str = json_str.replace('\\/', '/')
        try:
            data = json.loads(json_str)
            result["token"] = data.get("data", {}).get("token", "")
            result["video_id"] = data.get("data", {}).get("video", "")
            routes = data.get("data", {}).get("routes", {})
            result["links_route"] = routes.get("links", "")
            result["ping_route"] = routes.get("ping", "")
        except json.JSONDecodeError:
            pass
    
    if not result["token"]:
        return {"error": "Could not extract juicyData from argon page", "raw_html": r.text[:500]}
    
    return result


def get_download_links(token, links_route, video_id):
    """POST to the argon API links route to get download quality options.
    Returns list of DownloadLink objects."""
    s = _session()
    
    api_url = f"https://argon.razorshell.space{links_route}"
    
    api_headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.5",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "Referer": f"https://argon.razorshell.space/downlead/{video_id}/",
    }
    
    try:
        r = s.post(api_url, headers=api_headers, json={"token": token}, timeout=30)
    except Exception as e:
        return [{"error": str(e)}]
    
    if r.status_code != 200:
        return [{"error": f"API returned status {r.status_code}: {r.text[:200]}"}]
    
    try:
        data = r.json()
    except Exception:
        return [{"error": f"Invalid JSON response: {r.text[:200]}"}]
    
    if not data.get("success"):
        return [{"error": f"API error: {data.get('message', 'unknown')}"}]
    
    links = []
    for q in data.get("qualities", []):
        links.append(DownloadLink(
            quality=q.get("label", ""),
            size=q.get("size", ""),
            url=q.get("link", ""),
        ))
    
    return links


def download_video_file(url, output_path, quality="720P", progress_callback=None):
    """Download a video file from the given URL.
    Returns the path to the downloaded file, or None on failure."""
    s = _session()
    
    try:
        r = s.get(url, stream=True, timeout=30)
    except Exception as e:
        return None
    
    if r.status_code != 200:
        return None
    
    total_size = int(r.headers.get("content-length", 0))
    
    downloaded = 0
    try:
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        progress = (downloaded / total_size) * 100
                        progress_callback(progress)
    except Exception:
        return None
    
    return output_path if os.path.exists(output_path) else None


def get_all_download_urls(zipper_url):
    """Full flow: from a codedew zipper URL for a specific episode, get all download quality URLs.
    Returns list of DownloadLink objects."""
    # Step 1-3: Resolve to argon page
    argon_data = resolve_download_url(zipper_url)
    if "error" in argon_data:
        return [], argon_data
    
    # Step 4: Get download links from API
    links = get_download_links(
        token=argon_data["token"],
        links_route=argon_data["links_route"],
        video_id=argon_data["video_id"],
    )
    
    return links, argon_data


def get_season_episodes(season_page_url, language=None):
    """Get all episodes from a season page on rareanimes.mov.
    Returns list of EpisodeInfo objects with download URLs.
    Also returns the 'all seasons' links for multi-season anime."""
    s = _session()
    
    try:
        r = s.get(season_page_url, timeout=30)
    except Exception as e:
        return {"error": str(e), "episodes": [], "all_seasons": []}
    
    soup = BeautifulSoup(r.text, "lxml")
    title_tag = soup.find("title")
    page_title = title_tag.get_text(" ", strip=True) if title_tag else ""
    requested_language = (language or "hindi").lower()
    page_context = f"{page_title} {season_page_url} {r.url}"
    page_season = _season_from_text(page_context) or 1
    page_hindi_variant = _hindi_page_audio_variant(page_context)
    hindi_category_page = (
        requested_language == "hindi"
        and _has_language_path(r.url or season_page_url, "hindi")
    )
    
    # Get multiquality links
    multiquality_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if "codedew.com" in href and ("WatchMultiQuality" in text or "mult" in text.lower()):
            multiquality_links.append({"text": text, "url": href, "anchor": a})
    
    if not multiquality_links:
        # Try WatchNow links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if "codedew.com" in href and "WatchNow" in text:
                multiquality_links.append({"text": text, "url": href, "anchor": a})
    
    # Get all season links
    all_seasons = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if (
            href.startswith(BASE_URL)
            and ("season" in text.lower() or "all episodes" in text.lower())
            and anime_title_matches(page_title or season_page_url, href)
        ):
            all_seasons.append({"text": text, "url": href})
    
    # Use the page identity, not the full site HTML: shared navigation and
    # recommendations contain "Season" even on a standalone movie page.
    page_identity = f"{page_title} {season_page_url} {r.url}"
    is_movie = (
        re.search(r"\bmovie\b", page_identity, re.IGNORECASE) is not None
        and _season_from_text(page_identity) is None
    )
    
    episodes = []
    seen_episodes = set()
    for i, link in enumerate(multiquality_links):
        track_language, track_variant = _track_for_link(link["anchor"])
        audio_variant = ""
        if requested_language == "hindi":
            if track_language and track_language != "hindi":
                continue
            variant = track_variant or page_hindi_variant
            # RareAnimes' /hindi/ pages sometimes label a dub track only as
            # "Hindi" (without "Dub"), including newer season pages. Keep
            # explicit Hindi Sub labels excluded, but accept that plain label
            # when it is on the site's Hindi category page.
            if not variant and track_language == "hindi" and hindi_category_page:
                variant = "dub"
            # Hindi is intentionally strict: do not substitute Hindi Sub or an
            # unlabelled track when the user asked for Hindi Dub only.
            if variant != "dub":
                continue
            audio_variant = "hindi_dub"
        elif track_language and track_language != requested_language:
            continue
        elif track_language:
            audio_variant = track_language
            if track_variant:
                audio_variant = f"{track_language}_{track_variant}"

        season, episode = _parse_episode_from_link(
            link, i, is_movie, page_season, link["anchor"]
        )
        identity = (season, episode)
        if identity in seen_episodes:
            continue
        seen_episodes.add(identity)
        
        episodes.append(EpisodeInfo(
            season=season,
            episode=episode,
            download_url=link["url"],
            title="Hindi Dub" if audio_variant == "hindi_dub" else link.get("text", ""),
            language=requested_language,
            episode_type="movie" if is_movie else "episode",
            audio_variant=audio_variant,
        ))
    
    # If no multiquality links found, try to use the single episode's zipper URL
    if not episodes:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "codedew.com/zipper" in href:
                track_language, track_variant = _track_for_link(a)
                if requested_language == "hindi":
                    if track_language and track_language != "hindi":
                        continue
                    if (track_variant or page_hindi_variant) != "dub":
                        continue
                    audio_variant = "hindi_dub"
                else:
                    if track_language and track_language != requested_language:
                        continue
                    audio_variant = (
                        f"{track_language}_{track_variant}"
                        if track_language and track_variant else track_language
                    )
                season = _season_from_text(href) or page_season
                episode = _episode_number_for_link(a) or 1
                episodes.append(EpisodeInfo(
                    season=0 if is_movie else season,
                    episode=episode,
                    download_url=href,
                    title="Hindi Dub" if audio_variant == "hindi_dub" else "",
                    language=requested_language,
                    episode_type="movie" if is_movie else "episode",
                    audio_variant=audio_variant,
                ))
    
    episodes.sort(key=lambda episode: (int(episode.season or 0), int(episode.episode or 0)))
    return {"episodes": episodes, "all_seasons": all_seasons}


def _parse_episode_from_link(
    link_info, index, is_movie=False, default_season=1, anchor=None
):
    """Try to determine season and episode numbers from the link context."""
    text = link_info.get("text", "")
    season = _season_from_text(text) or default_season
    episode = _episode_number_for_link(anchor) if anchor is not None else None
    if episode is None:
        episode = _episode_from_text(text)
    if episode is None:
        episode = index + 1

    if is_movie:
        season = 0
        episode = 1
    
    return season, episode


def detect_ongoing_or_completed(anime_page_url):
    """Detect if an anime is ongoing or completed by checking the page."""
    s = _session()
    
    try:
        r = s.get(anime_page_url, timeout=30)
    except Exception:
        return "unknown"
    
    soup = BeautifulSoup(r.text, "lxml")
    
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True).lower()
        if "/category/" in href:
            if "ongoing" in text:
                return "ongoing"
            if "completed" in text:
                return "completed"
    
    text_lower = r.text.lower()
    if "ongoing" in text_lower:
        return "ongoing"
    if "completed" in text_lower:
        return "completed"
    
    return "unknown"


def get_latest_episode(multiquality_url):
    """Get the latest episode info from a multiquality page.
    Returns (season, episode) tuple or None."""
    s = _session()
    
    try:
        r = s.get(multiquality_url, timeout=30, allow_redirects=True)
    except Exception:
        return None
    
    soup = BeautifulSoup(r.text, "lxml")
    
    btn = soup.find("a", id="goBtn") or soup.find("a", {"data-href": True})
    if not btn:
        return None
    
    episode_text = btn.get_text(strip=True)
    title_tag = soup.find("title")
    page_title = title_tag.get_text(strip=True) if title_tag else ""
    
    season, episode = _parse_episode_info(episode_text, page_title)
    
    # Check if there's a Next button (new episode available)
    has_next = False
    for a in soup.find_all("a", onclick=True):
        onclick = a.get("onclick", "")
        if "location.href" in onclick and "next" in a.get("id", "").lower():
            has_next = True
            break
    
    return {"season": season, "episode": episode, "has_next": has_next, "page_url": r.url}
