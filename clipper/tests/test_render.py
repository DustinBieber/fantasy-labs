from clipper.render import probe, render_clip
from clipper.render.subtitles import ass_color, ass_time, build_ass, chunk_words, clean_text, timeline_words
from clipper.transcripts import Word

from .conftest import make_video, needs_ffmpeg


def test_ass_helpers():
    assert ass_color("#FFB627") == "&H0027B6FF"
    assert ass_color("#000000", 0x80) == "&H80000000"
    assert ass_time(3725.456) == "1:02:05.46"
    assert clean_text("BIG {take} \\ 🔥") == "BIG (take) /"


def test_timeline_words_maps_segments_back_to_back():
    words = [Word("a", 10.0, 10.4), Word("b", 10.5, 10.9), Word("x", 15.0, 15.4), Word("c", 30.0, 30.4)]
    out = timeline_words(words, [(10.0, 11.0), (30.0, 31.0)])
    assert [w.w for w in out] == ["a", "b", "c"]
    assert out[2].s == 1.0          # second segment starts at t=1.0 on the clip timeline


def test_chunks_break_on_pauses_and_size():
    words = [Word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate("one two three four five".split())]
    words.append(Word("later", 5.0, 5.3))
    chunks = chunk_words(words, 3)
    assert [[w.w for w in c] for c in chunks] == [["one", "two", "three"], ["four", "five"], ["later"]]


def test_build_ass_has_highlight_hook_and_cta(cfg):
    words = [Word(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate("bijan is a league winner".split())]
    ass = build_ass(words, 10.0, cfg.render, hook="Bijan SZN 🔥", cta="FULL EPISODE ON YOUTUBE")
    assert "PlayResY: 1920" in ass
    assert ass.count("Style: Caption") == 1
    assert "{\\c&H0027B6FF" in ass                 # active word highlight
    assert "BIJAN SZN" in ass and "🔥" not in ass
    assert "0:00:06.00,0:00:10.00,CTA" in ass       # CTA in the last 4 seconds


@needs_ffmpeg
def test_render_single_and_montage(cfg, tmp_path):
    src = make_video(tmp_path / "src.mp4", seconds=30)
    words = [Word(w, 2 + i * 0.4, 2 + i * 0.4 + 0.35) for i, w in enumerate(("trust me this guy is a league winner " * 8).split())]
    cfg.render.preset = "ultrafast"
    for name, segs in (("single", [(2.0, 8.0)]), ("montage", [(2.0, 5.0), (12.0, 15.5)])):
        out = render_clip(src, segs, words, tmp_path / f"{name}.mp4", cfg.render, hook="League winner", cta="FULL EPISODE")
        info = probe(out)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert (video["width"], video["height"]) == (1080, 1920)
        assert any(s["codec_type"] == "audio" for s in info["streams"])
        assert abs(float(info["format"]["duration"]) - sum(e - s for s, e in segs)) < 0.3
        assert out.with_suffix(".jpg").exists()
        assert not out.with_suffix(".work").exists()
