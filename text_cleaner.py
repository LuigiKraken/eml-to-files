"""Text cleaning pipeline: signatures, threads, HTML→text, disclaimers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from io import StringIO


@dataclass
class CleanerConfig:
    strip_signatures: bool = True
    extra_signature_markers: list[str] | None = None
    simplify_threads: bool = True
    deduplicate_quotes: bool = True
    prefer_plaintext: bool = True
    strip_disclaimers: bool = True
    strip_cid_references: bool = True
    min_body_length: int = 10


# ── Signature detection ─────────────────────────────────────────────────────

_BUILTIN_SIG_MARKERS = [
    r"^-- ?$",
    r"^mit freundlichen gr[uü][sß]en",
    r"^best(e[n]?)? regards",
    r"^kind regards",
    r"^viele gr[uü][sß]e",
    r"^freundliche gr[uü][sß]e",
    r"^herzliche gr[uü][sß]e",
    r"^liebe gr[uü][sß]e",
    r"^beste gr[uü][sß]e",
    r"^sch[oö]ne gr[uü][sß]e",
    r"^regards",
    r"^cheers",
    r"^many thanks",
    r"^vielen dank",
    r"^mfg\b",
    r"^sent from my",
    r"^gesendet von",
    r"^von meinem (iphone|ipad|samsung|smartphone)",
    r"^get outlook for",
]


def _build_sig_pattern(extra_markers: list[str] | None = None) -> re.Pattern:
    patterns = list(_BUILTIN_SIG_MARKERS)
    if extra_markers:
        for m in extra_markers:
            patterns.append(r"^" + re.escape(m.lower()))
    combined = "|".join(f"(?:{p})" for p in patterns)
    return re.compile(combined, re.IGNORECASE | re.MULTILINE)


def strip_signature(text: str, extra_markers: list[str] | None = None) -> str:
    pattern = _build_sig_pattern(extra_markers)
    lines = text.split("\n")

    # Walk backwards to find the signature start: the *last* match that is
    # followed mostly by short, non-content lines (address blocks, phone, etc.)
    best_cut = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if pattern.match(stripped):
            remaining = lines[i + 1:]
            content_lines = [l for l in remaining if len(l.strip()) > 3]
            # A real signature block typically has address/phone lines but
            # fewer than 30 meaningful lines after the marker.
            if len(content_lines) <= 30:
                best_cut = i
                break  # take the first qualifying marker

    if best_cut is not None:
        text = "\n".join(lines[:best_cut]).rstrip()

    return text


# ── Thread / quote stripping ────────────────────────────────────────────────

_QUOTE_HEADER_PATTERNS = [
    # "Von: ... Gesendet: ... An: ... Betreff: ..."  (German Outlook)
    re.compile(
        r"^Von:\s+.+?\s*$.*?^Betreff:\s+.+?\s*$",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    ),
    # "From: ... Sent: ... To: ... Subject: ..."  (English Outlook)
    re.compile(
        r"^From:\s+.+?\s*$.*?^Subject:\s+.+?\s*$",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    ),
    # "On <date>, <person> wrote:"
    re.compile(
        r"^On .+? wrote:\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    # "Am <date> schrieb <person>:"
    re.compile(
        r"^Am .+? schrieb .+?:\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    # "> " quoted lines block (3+ consecutive quoted lines)
    re.compile(
        r"(?:^>+[ ]?.*\n){3,}",
        re.MULTILINE,
    ),
    # "-----Original Message-----" / "-----Ursprüngliche Nachricht-----"
    re.compile(
        r"^-{3,}\s*(Original Message|Urspr[uü]ngliche Nachricht)\s*-{3,}\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    # "________________________________" (Outlook separator)
    re.compile(r"^_{10,}\s*$", re.MULTILINE),
]


def simplify_thread(text: str) -> str:
    """Remove quoted reply chains, keeping only the newest message."""
    earliest_pos = len(text)

    for pattern in _QUOTE_HEADER_PATTERNS:
        m = pattern.search(text)
        if m and m.start() < earliest_pos:
            earliest_pos = m.start()

    if earliest_pos < len(text):
        text = text[:earliest_pos].rstrip()

    return text


def deduplicate_quoted_blocks(text: str) -> str:
    """Remove duplicate '>'-quoted blocks."""
    blocks: list[str] = []
    seen: set[str] = set()
    current_block: list[str] = []
    in_quote = False

    for line in text.split("\n"):
        is_quoted = line.startswith(">")
        if is_quoted:
            in_quote = True
            current_block.append(line)
        else:
            if in_quote and current_block:
                block_text = "\n".join(current_block)
                normalized = re.sub(r"\s+", " ", block_text.strip())
                if normalized not in seen:
                    seen.add(normalized)
                    blocks.append(block_text)
                current_block = []
                in_quote = False
            blocks.append(line)

    if current_block:
        block_text = "\n".join(current_block)
        normalized = re.sub(r"\s+", " ", block_text.strip())
        if normalized not in seen:
            blocks.append(block_text)

    return "\n".join(blocks)


# ── HTML to plain text ──────────────────────────────────────────────────────

class _HTMLToText(HTMLParser):
    BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}
    SKIP_TAGS = {"script", "style", "head"}

    def __init__(self):
        super().__init__()
        self._buf = StringIO()
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if self._skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self._buf.write("\n")
        if tag == "a":
            for name, val in attrs:
                if name == "href" and val and not val.startswith("cid:"):
                    self._buf.write(f" [{val}] ")
                    break

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        if self._skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self._buf.write("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._buf.write(data)

    def get_text(self) -> str:
        return self._buf.getvalue()


def html_to_text(html: str) -> str:
    parser = _HTMLToText()
    try:
        parser.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return parser.get_text()


# ── Disclaimers ─────────────────────────────────────────────────────────────

_DISCLAIMER_PATTERNS = [
    re.compile(
        r"(Diese E-Mail|This e-?mail|Confidentiality).{0,80}(vertraulich|confidential|intended).+",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(HINWEIS|DISCLAIMER|NOTICE|VERTRAULICHKEITS).{0,20}:?\s*\n.+",
        re.IGNORECASE | re.DOTALL,
    ),
]


def strip_disclaimers(text: str) -> str:
    for pat in _DISCLAIMER_PATTERNS:
        m = pat.search(text)
        if m and m.start() > len(text) * 0.5:
            text = text[: m.start()].rstrip()
    return text


# ── CID references ──────────────────────────────────────────────────────────

_CID_RE = re.compile(r"\[cid:[^\]]+\]", re.IGNORECASE)


def strip_cid_references(text: str) -> str:
    return _CID_RE.sub("", text)


# ── Whitespace normalization ────────────────────────────────────────────────

def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\u00a0", " ", text)  # non-breaking space
    text = re.sub(r"[ \t]+\n", "\n", text)  # trailing whitespace
    text = re.sub(r"\n{4,}", "\n\n\n", text)  # cap consecutive blank lines
    return text.strip()


# ── Public pipeline ─────────────────────────────────────────────────────────

def clean_body(
    plain: str,
    html: str,
    cfg: CleanerConfig,
) -> str:
    """Run the full cleaning pipeline and return the final text."""
    if cfg.prefer_plaintext and plain.strip():
        text = plain
    elif html.strip():
        text = html_to_text(html)
    elif plain.strip():
        text = plain
    else:
        return ""

    # Normalise =0D=0A artefacts left over from quoted-printable decoding
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r" \n", "\n", text)

    if cfg.strip_cid_references:
        text = strip_cid_references(text)

    if cfg.simplify_threads:
        text = simplify_thread(text)
    elif cfg.deduplicate_quotes:
        text = deduplicate_quoted_blocks(text)

    if cfg.strip_signatures:
        text = strip_signature(text, cfg.extra_signature_markers)

    if cfg.strip_disclaimers:
        text = strip_disclaimers(text)

    text = normalize_whitespace(text)

    return text
