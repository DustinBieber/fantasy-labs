"""Build an ASS subtitle file: word-by-word captions, a hook headline, and a CTA."""
from __future__ import annotations

import unicodedata

from ..config import Render
from ..transcripts import Word

GAP_BREAK = 0.45       # start a new caption chunk after a pause this long
HOLD = 0.35            # keep a chunk on screen across gaps shorter than this
MAX_CHUNK_CHARS = 18


def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def clean_text(text: str) -> str:
    """Escape ASS control characters and drop emoji/symbols the font can't draw."""
    text = text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")
    return "".join(ch for ch in text if unicodedata.category(ch) not in ("So", "Cs", "Co", "Cn")).strip()


def timeline_words(words: list[Word], segments: list[tuple[float, float]]) -> list[Word]:
    """Map source-time words onto the clip timeline (segments play back to back)."""
    out: list[Word] = []
    offset = 0.0
    for s, e in segments:
        for w in words:
            mid = (w.s + w.e) / 2
            if s <= mid <= e:
                ns = max(offset, w.s - s + offset)
                ne = min(offset + (e - s), w.e - s + offset)
                out.append(Word(w.w, round(ns, 3), round(max(ne, ns + 0.05), 3)))
        offset += e - s
    return out


def chunk_words(words: list[Word], per_chunk: int) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        too_long = len(" ".join(x.w for x in cur + [w])) > MAX_CHUNK_CHARS
        if cur and (len(cur) >= per_chunk or w.s - cur[-1].e > GAP_BREAK or too_long or cur[-1].w[-1:] in ".?!"):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(
    words: list[Word],
    duration: float,
    style: Render,
    hook: str = "",
    cta: str = "",
) -> str:
    W, H = style.width, style.height
    font = style.font
    fs = style.font_size
    white = ass_color(style.caption_color)
    hi = ass_color(style.highlight_color)
    black = ass_color(style.outline_color)
    shade = ass_color("#000000", 0x80)

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{font},{fs},{white},{white},{black},{shade},-1,0,0,0,100,100,0,0,1,6,2,5,60,60,0,1",
        # BorderStyle 3 draws an opaque box in OutlineColour behind the text.
        f"Style: Hook,{font},{int(fs * 0.82)},{black},{black},{hi},{shade},-1,0,0,0,100,100,0,0,3,16,0,8,80,80,{int(H * 0.12)},1",
        f"Style: CTA,{font},{int(fs * 0.52)},{white},{white},{ass_color('#000000', 0x60)},{shade},-1,0,0,0,100,100,1,0,3,12,0,2,60,60,{int(H * 0.08)},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def fmt(word: str) -> str:
        word = clean_text(word)
        return word.upper() if style.uppercase else word

    x, y = W // 2, int(H * style.caption_y)
    chunks = chunk_words(words, max(1, style.words_per_caption))
    for ci, chunk in enumerate(chunks):
        chunk_end = chunk[-1].e
        if ci + 1 < len(chunks) and chunks[ci + 1][0].s - chunk_end < HOLD:
            chunk_end = chunks[ci + 1][0].s
        for wi, w in enumerate(chunk):
            start = chunk[0].s if wi == 0 else w.s
            end = chunk[wi + 1].s if wi + 1 < len(chunk) else chunk_end
            if end <= start:
                continue
            parts = []
            for k, other in enumerate(chunk):
                text = fmt(other.w)
                if k == wi:
                    parts.append(f"{{\\c{hi}\\fscx108\\fscy108}}{text}{{\\c{white}\\fscx100\\fscy100}}")
                else:
                    parts.append(text)
            lines.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{{\\pos({x},{y})}}{' '.join(parts)}")

    hook = clean_text(hook)
    if hook:
        hook_end = duration if style.hook_seconds <= 0 else min(duration, style.hook_seconds)
        text = hook.upper() if style.uppercase else hook
        lines.append(f"Dialogue: 2,{ass_time(0)},{ass_time(hook_end)},Hook,,0,0,0,,{{\\fad(150,0)}}{text}")

    cta = clean_text(cta)
    if cta and style.cta_mode != "off":
        start = 0.0 if style.cta_mode == "always" else max(0.0, duration - style.cta_seconds)
        lines.append(f"Dialogue: 2,{ass_time(start)},{ass_time(duration)},CTA,,0,0,0,,{{\\fad(200,0)}}{cta}")

    return "\n".join(lines) + "\n"
