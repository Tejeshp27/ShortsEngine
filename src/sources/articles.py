# =============================================================
# src/sources/articles.py — turn web pages into plain article text
#
# RSS feeds only give us a headline and a one-line summary. To
# write an accurate script, the writing model needs the actual
# article. This module:
#   - downloads ONE article page (only for the story we picked)
#   - pulls out the paragraph text and throws away the HTML
#   - trims it to a maximum length so we send fewer tokens
#   - caches the result on disk so we never download it twice
#
# It only uses Python's built-in libraries and makes no paid calls.
# =============================================================

import hashlib
import json
import os
import urllib.request
from html.parser import HTMLParser

# Where downloaded articles are saved so we never fetch them twice.
ARTICLE_CACHE_FOLDER = os.path.join("cache", "articles")

# A plain, honest name for our program so news sites can see who is visiting.
USER_AGENT = "ShortsEngine/0.1 (personal news-summary project)"


class ParagraphCollector(HTMLParser):
    """
    Reads an HTML page and collects the text inside every <p> tag.

    News articles keep their story text in <p> (paragraph) tags,
    while menus, ads and scripts live elsewhere. Keeping only the
    paragraphs is a simple way to get just the article.

    Example:
        collector = ParagraphCollector()
        collector.feed("<p>Hello</p><div>menu</div><p>World</p>")
        print(collector.paragraphs)   # ["Hello", "World"]
    """

    def __init__(self):
        """Set up an empty list of paragraphs before reading any HTML."""
        super().__init__()
        self.paragraphs = []
        self.inside_paragraph = False
        self.current_text = ""

    def handle_starttag(self, tag, attrs):
        """Called by HTMLParser whenever an opening tag like <p> is found."""
        if tag == "p":
            self.inside_paragraph = True
            self.current_text = ""

    def handle_endtag(self, tag):
        """Called by HTMLParser whenever a closing tag like </p> is found."""
        if tag == "p" and self.inside_paragraph:
            cleaned = " ".join(self.current_text.split())
            if cleaned:
                self.paragraphs.append(cleaned)
            self.inside_paragraph = False

    def handle_data(self, data):
        """Called by HTMLParser for plain text between tags."""
        if self.inside_paragraph:
            self.current_text += data


class TextCollector(HTMLParser):
    """
    Reads a small piece of HTML and keeps only the visible text.

    Example:
        collector = TextCollector()
        collector.feed("<b>Big</b> news")
        print(collector.get_text())   # "Big news"
    """

    def __init__(self):
        """Set up an empty list of text pieces before reading any HTML."""
        super().__init__()
        self.pieces = []

    def handle_data(self, data):
        """Called by HTMLParser for plain text between tags."""
        self.pieces.append(data)

    def get_text(self):
        """Return all collected text joined together with single spaces."""
        return " ".join(" ".join(self.pieces).split())


def strip_html(html_text):
    """
    Remove HTML tags from a short piece of text.

    Args:
        html_text (str): Text that may contain tags, like "<p>Hi</p>".

    Returns:
        str: The same text without tags, e.g. "Hi".
    """
    collector = TextCollector()
    collector.feed(html_text or "")
    return collector.get_text()


def extract_paragraphs(html_page):
    """
    Pull the article paragraphs out of a full HTML page.

    Args:
        html_page (str): The full HTML of a news article page.

    Returns:
        str: The paragraphs joined with blank lines between them.
             Very short paragraphs (like "Advertisement") are skipped.
    """
    collector = ParagraphCollector()
    collector.feed(html_page)

    # Paragraphs shorter than this are almost always captions,
    # bylines or buttons, not part of the story.
    minimum_words = 8

    kept = []
    for paragraph in collector.paragraphs:
        if len(paragraph.split()) >= minimum_words:
            kept.append(paragraph)

    return "\n\n".join(kept)


def trim_text(text, max_chars):
    """
    Shorten text to at most max_chars characters, cutting at a paragraph break.

    Args:
        text (str): The text to shorten.
        max_chars (int): The longest the result may be.

    Returns:
        str: The trimmed text. If it was cut, it ends with "[...]".
    """
    if len(text) <= max_chars:
        return text

    shortened = text[:max_chars]

    # Cut at the last paragraph break so we don't stop mid-sentence.
    last_break = shortened.rfind("\n\n")
    if last_break > max_chars // 2:
        shortened = shortened[:last_break]

    return shortened + "\n\n[...]"


def cache_path_for_url(url):
    """
    Work out which cache file belongs to an article URL.

    URLs contain characters that can't be used in file names, so
    we turn the URL into a short fixed "fingerprint" (a SHA-256 hash)
    and use that as the file name. The same URL always gives the
    same fingerprint.

    Args:
        url (str): The article URL.

    Returns:
        str: A path like "cache/articles/3fa9c1....json".
    """
    fingerprint = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return os.path.join(ARTICLE_CACHE_FOLDER, f"{fingerprint}.json")


def download_page(url):
    """
    Download a web page and return its HTML as text.

    Args:
        url (str): The page to download.

    Returns:
        str: The page's HTML.

    Raises:
        Exception: If the download fails (no internet, 404, timeout...).
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        raw_bytes = response.read()
    return raw_bytes.decode("utf-8", errors="replace")


def get_article_text(story, max_chars, fetch_full_text=True):
    """
    Get the best available text for a story, using the cache when possible.

    Order of preference:
      1. A previously saved copy in cache/articles/
      2. A fresh download of the article page
      3. The RSS summary (if the download fails or is turned off)

    Args:
        story (dict): A story from the RSS fetcher (needs 'url' and 'summary').
        max_chars (int): Trim the text to this many characters.
        fetch_full_text (bool): If False, skip downloading and use the summary.

    Returns:
        str: Plain article text, trimmed to max_chars.
    """
    summary = story.get("summary", "")

    if not fetch_full_text:
        return trim_text(summary, max_chars)

    cache_file = cache_path_for_url(story["url"])

    # 1. Already downloaded? Use the saved copy.
    if os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        print("  Article loaded from cache.")
        return trim_text(saved["text"], max_chars)

    # 2. Try to download the page.
    try:
        html_page = download_page(story["url"])
        article_text = extract_paragraphs(html_page)
    except Exception as error:
        print(f"  WARNING: Could not download the article ({error}).")
        print("           Falling back to the RSS summary.")
        return trim_text(summary, max_chars)

    if not article_text:
        print("  WARNING: No article text found on the page. Using the RSS summary.")
        return trim_text(summary, max_chars)

    # Save the full (untrimmed) text so a later run with a bigger
    # max_chars doesn't need to download it again.
    os.makedirs(ARTICLE_CACHE_FOLDER, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({"url": story["url"], "text": article_text}, f)

    print(f"  Downloaded article ({len(article_text)} characters).")
    return trim_text(article_text, max_chars)
