import re
from html.parser import HTMLParser


class _VisibleTextParser(HTMLParser):
    BLOCK_TAGS = {"blockquote", "br", "h1", "h2", "h3", "li", "p"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip_depth += 1
        elif not self.skip_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)


def generate_bar_text(percentage: int) -> str:
    """Render a compact, styled unvaulting progress card as Discord message text."""
    percentage = max(0, min(100, int(percentage)))
    total_blocks = 20
    filled = round((percentage / 100) * total_blocks)
    empty = total_blocks - filled

    bar = "▰" * filled + "▱" * empty
    return (
        f">>> 🛰️ **DESTINY 2 · UNVAULTING**\n"
        f"*Development milestone*\n\n"
        f"`[{bar}]`  **{percentage}% complete**\n"
        f"*Bungie progress tracker*"
    )


def extract_unvaulting_percentage(html_text: str) -> int | None:
    """Return a percentage from a paragraph that mentions unvaulting-related work."""
    parser = _VisibleTextParser()
    parser.feed(html_text)

    keyword_pattern = re.compile(
        r"\b(?:unvault\w*|vaulted\s+content|legacy\s+destinations?|red\s+war|content\s+vault)\b",
        re.IGNORECASE,
    )
    percentage_pattern = re.compile(r"\b(\d{1,3})\s*%")
    release_pattern = re.compile(
        r"\b(?:now\s+live|now\s+available|is\s+(?:now\s+)?live|is\s+(?:now\s+)?available|has\s+launched|has\s+been\s+released)\b",
        re.IGNORECASE,
    )
    visible_text = "".join(parser.parts)
    found_percentage = None
    release_confirmed = False

    for paragraph in visible_text.splitlines():
        if not keyword_pattern.search(paragraph):
            continue
        if release_pattern.search(paragraph):
            release_confirmed = True
        match = percentage_pattern.search(paragraph)
        if match:
            percentage = int(match.group(1))
            if 0 <= percentage <= 100:
                found_percentage = max(found_percentage or 0, percentage)

    if release_confirmed:
        return 100
    if found_percentage is not None:
        return min(found_percentage, 99)
    return None