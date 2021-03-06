"""A namespace for functions that render the Markdown of tokens from markdown-
it-py."""
import logging
from typing import Any, Mapping, Optional, Sequence

from markdown_it.token import Token

from mdformat.renderer import _codepoints
from mdformat.renderer._container_renderers import link_close as render_link
from mdformat.renderer._util import (
    MARKERS,
    RE_CHAR_REFERENCE,
    is_text_inside_autolink,
    longest_consecutive_sequence,
    maybe_add_link_brackets,
)

LOGGER = logging.getLogger(__name__)


def default(
    tokens: Sequence[Token], idx: int, options: Mapping[str, Any], env: dict
) -> str:
    """Default formatter for tokens that don't have one implemented."""
    return ""


def hr(tokens: Sequence[Token], idx: int, options: Mapping[str, Any], env: dict) -> str:
    thematic_break_width = 70
    return "_" * thematic_break_width + MARKERS.BLOCK_SEPARATOR


def image(
    tokens: Sequence[Token], idx: int, options: Mapping[str, Any], env: dict
) -> str:
    token = tokens[idx]
    assert token.attrs is not None, "image token attrs must not be None"

    # "alt" attr MUST be set, even if empty. Because it's mandatory and
    # should be placed on proper position for tests.
    #
    # Replace content with actual value
    description = _render_inline_as_text(token.children, options, env)
    token.attrSet("alt", description)

    ref_label = token.meta.get("label")
    if ref_label:
        env.setdefault("used_refs", set()).add(ref_label)
        ref_label_repr = ref_label.lower()
        if description.lower() == ref_label_repr:
            return f"![{description}]"
        return f"![{description}][{ref_label_repr}]"

    uri = token.attrGet("src")
    assert uri is not None
    uri = maybe_add_link_brackets(uri)
    title = token.attrGet("title")
    if title is not None:
        return f'![{description}]({uri} "{title}")'
    return f"![{description}]({uri})"


def code_inline(
    tokens: Sequence[Token], idx: int, options: Mapping[str, Any], env: dict
) -> str:
    code = tokens[idx].content
    all_chars_are_whitespace = not code.strip()
    longest_backtick_seq = longest_consecutive_sequence(code, "`")
    if not longest_backtick_seq or all_chars_are_whitespace:
        return f"`{code}`"
    separator = "`" * (longest_backtick_seq + 1)
    return f"{separator} {code} {separator}"


def fence(
    tokens: Sequence[Token], idx: int, options: Mapping[str, Any], env: dict
) -> str:
    token = tokens[idx]
    assert token.map is not None, "fence token map must not be None"

    info_str = token.info.strip() if token.info else ""
    lang = info_str.split()[0] if info_str.split() else ""
    code_block = token.content

    # Info strings of backtick code fences can not contain backticks or tildes.
    # If that is the case, we make a tilde code fence instead.
    if "`" in info_str or "~" in info_str:
        fence_char = "~"
    else:
        fence_char = "`"

    # Format the code block using enabled codeformatter funcs
    if lang in options.get("codeformatters", {}):
        fmt_func = options["codeformatters"][lang]
        try:
            code_block = fmt_func(code_block, info_str)
        except Exception:
            # Swallow exceptions so that formatter errors (e.g. due to
            # invalid code) do not crash mdformat.
            LOGGER.warning(
                f"Failed formatting content of a {lang} code block "
                f"(line {token.map[0] + 1} before formatting)"
            )

    # The code block must not include as long or longer sequence of `fence_char`s
    # as the fence string itself
    fence_len = max(3, longest_consecutive_sequence(code_block, fence_char) + 1)
    fence_str = fence_char * fence_len

    return f"{fence_str}{info_str}\n{code_block}{fence_str}" + MARKERS.BLOCK_SEPARATOR


def code_block(
    tokens: Sequence[Token], idx: int, options: Mapping[str, Any], env: dict
) -> str:
    return fence(tokens, idx, options, env)


def html_block(
    tokens: Sequence[Token], idx: int, options: Mapping[str, Any], env: dict
) -> str:
    return tokens[idx].content.rstrip("\n") + MARKERS.BLOCK_SEPARATOR


def html_inline(
    tokens: Sequence[Token], idx: int, options: Mapping[str, Any], env: dict
) -> str:
    return tokens[idx].content


def hardbreak(
    tokens: Sequence[Token], idx: int, options: Mapping[str, Any], env: dict
) -> str:
    return "\\" + "\n"


def softbreak(
    tokens: Sequence[Token], idx: int, options: Mapping[str, Any], env: dict
) -> str:
    return "\n"


def text(
    tokens: Sequence[Token], idx: int, options: Mapping[str, Any], env: dict
) -> str:
    """Process a text token.

    Text should always be a child of an inline token. An inline token
    should always be enclosed by a heading or a paragraph.
    """
    text = tokens[idx].content
    if is_text_inside_autolink(tokens, idx):
        return text

    # Escape backslash to prevent it from making unintended escapes.
    # This escape has to be first, else we start multiplying backslashes.
    text = text.replace("\\", "\\\\")

    text = _escape_asterisk_emphasis(text)  # Escape emphasis/strong marker.
    text = _escape_underscore_emphasis(text)  # Escape emphasis/strong marker.
    text = text.replace("[", "\\[")  # Escape link label enclosure
    text = text.replace("]", "\\]")  # Escape link label enclosure
    text = text.replace("<", "\\<")  # Escape URI enclosure
    text = text.replace("`", "\\`")  # Escape code span marker

    # Escape "&" if it starts a sequence that can be interpreted as
    # a character reference.
    for char_refs_found, char_ref in enumerate(RE_CHAR_REFERENCE.finditer(text)):
        start = char_ref.start() + char_refs_found
        text = text[:start] + "\\" + text[start:]

    # Replace no-break space with its decimal representation
    text = text.replace(chr(160), "&#160;")

    # The parser can give us consecutive newlines which can break
    # the markdown structure. Replace two or more consecutive newlines
    # with newline character's decimal reference.
    text = text.replace("\n\n", "&#10;&#10;")

    # If the last character is a "!" and the token next up is a link, we
    # have to escape the "!" or else the link will be interpreted as image.
    if (
        text.endswith("!")
        and (idx + 1) < len(tokens)
        and tokens[idx + 1].type == "link_open"
    ):
        text = text[:-1] + "\\!"

    return text


def _render_inline_as_text(
    tokens: Optional[Sequence[Token]], options: Mapping[str, Any], env: dict
) -> str:
    """Special kludge for image `alt` attributes to conform CommonMark spec.

    Don't try to use it! Spec requires to show `alt` content with
    stripped markup, instead of simple escaping.
    """
    if not tokens:
        return ""
    text_stack = [""]

    for i, token in enumerate(tokens):
        if token.type == "text":
            text_stack[-1] += token.content
        elif token.type == "image":
            text_stack[-1] += _render_inline_as_text(token.children, options, env)
        elif token.type == "link_open":
            text_stack.append("")
        elif token.type == "link_close":
            link_text = text_stack.pop()
            text_stack[-1] += render_link(link_text, tokens, i, options, env)

    result = text_stack.pop()
    assert not text_stack
    return result


def _escape_asterisk_emphasis(text: str) -> str:
    """Escape asterisks to prevent unexpected emphasis/strong emphasis.

    Currently we escape all asterisks unless both previous and next
    character are Unicode whitespace.
    """
    # Fast exit to improve performance
    if "*" not in text:
        return text

    escaped_text = ""

    text_length = len(text)
    for i, current_char in enumerate(text):
        if current_char != "*":
            escaped_text += current_char
            continue
        prev_char = text[i - 1] if (i - 1) >= 0 else None
        next_char = text[i + 1] if (i + 1) < text_length else None
        if (
            prev_char in _codepoints.UNICODE_WHITESPACE
            and next_char in _codepoints.UNICODE_WHITESPACE
        ):
            escaped_text += current_char
            continue
        escaped_text += "\\" + current_char

    return escaped_text


def _escape_underscore_emphasis(text: str) -> str:
    """Escape underscores to prevent unexpected emphasis/strong emphasis.

    Currently we escape all underscores unless:
      - Neither of the surrounding characters are one of Unicode whitespace,
        start or end of line, or Unicode punctuation
      - Both surrounding characters are Unicode whitespace
    """
    # Fast exit to improve performance
    if "_" not in text:
        return text

    bad_neighbor_chars = (
        _codepoints.UNICODE_WHITESPACE
        | _codepoints.UNICODE_PUNCTUATION
        | frozenset({None})
    )
    escaped_text = ""

    text_length = len(text)
    for i, current_char in enumerate(text):
        if current_char != "_":
            escaped_text += current_char
            continue
        prev_char = text[i - 1] if (i - 1) >= 0 else None
        next_char = text[i + 1] if (i + 1) < text_length else None
        if (
            prev_char in _codepoints.UNICODE_WHITESPACE
            and next_char in _codepoints.UNICODE_WHITESPACE
        ) or (
            prev_char not in bad_neighbor_chars and next_char not in bad_neighbor_chars
        ):
            escaped_text += current_char
            continue
        escaped_text += "\\" + current_char

    return escaped_text
