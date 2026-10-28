import re


def slugify(text: str) -> str:
    """Lowercase, replace runs of non-alphanumerics with '-', trim dashes."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug


def initials(name: str) -> str:
    """'Ada Lovelace' -> 'AL'. Bug: assumes every word starts with a letter."""
    return "".join(word[0] for word in name.split()).upper()
