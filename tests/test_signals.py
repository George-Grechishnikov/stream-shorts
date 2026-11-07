import numpy as np

from shorts import signals
from shorts.models import ChatMessage, load_json
from shorts.signals.audio import loudness_series
from shorts.signals.chat import chat_series, is_hype, top_messages
from shorts.signals.series import robust_z, shift, smooth
from tests.conftest import write_chat, write_meta, write_wav


def msg(t, text="привет", user="u", emotes=()):
    return ChatMessage(t=t, user=user, text=text, emotes=list(emotes))


def test_is_hype_emotes_words_and_caps():
    assert is_hype(msg(0, "KEKW"))
    assert is_hype(msg(0, "что", emotes=["OMEGALUL"]))
    assert is_hype(msg(0, "ахахаха ну ты даёшь"))
    assert is_hype(msg(0, "ЧТО ПРОИСХОДИТ"))
    assert not is_hype(msg(0, "привет всем"))
    assert not is_hype(msg(0, "ОК"))  # короткий капс — не хайп


def test_chat_series_counts_rate_users_hype():
    msgs = [msg(1.2, "KEKW", "a"), msg(1.8, "KEKW", "a"), msg(1.9, "привет", "b"), msg(5.0, "привет", "c"),
            msg(99, "вне VOD", "d")]
    s = chat_series(msgs, duration=10)
    assert len(s["chat_rate"]) == 11
    assert s["chat_rate"][1] == 3 and s["chat_users"][1] == 2 and s["emote_hype"][1] == 2
    assert s["chat_rate"][5] == 1 and s["emote_hype"][5] == 0
    assert s["chat_rate"].sum() == 4  # сообщение после конца отброшено


def test_top_messages_counts_duplicates():
    msgs = [msg(1, "KEKW"), msg(2, "KEKW"), msg(3, "лол"), msg(50, "KEKW")]
    assert top_messages(msgs, 0, 10)[0] == "KEKW ×2"


def test_shift_moves_left_and_pads_zero():
    assert shift(np.array([0, 0, 5, 0.0]), 2).tolist() == [5, 0, 0, 0]
    assert shift(np.array([1, 2.0]), 0).tolist() == [1, 2]


def test_smooth_keeps_length():
    assert len(smooth(np.ones(50), 5)) == 50


def test_robust_z_flags_spike_and_clips():
    rng = np.random.default_rng(1)
    x = rng.normal(10, 1, 3600)
    x[1800] = 100
    z = robust_z(x)
    assert z.argmax() == 1800
    assert z.max() == 6.0  # обрезка сверху
    assert z.min() >= 0.0
    assert np.median(z) < 1


def test_loudness_series_per_second(tmp_path):
    p = tmp_path / "a.wav"
    write_wav(p, 20, loud={7: 0.8})
    db = loudness_series(p)
    assert len(db) == 20
    assert db.argmax() == 7
    assert -5 < db[7] < 0 and db[0] < -30


def test_signals_run_pads_loudness_with_silence(job, cfg):
    """Регрессия: ряды дополняются до длины VOD; громкость — тишиной, а не 0 дБ (= максимум)."""
    write_meta(job, duration=30.5)            # 31 секунда, а аудио — 25
    write_wav(job.p("audio.wav"), 25, loud={10: 0.5})
    write_chat(job, [msg(3, "KEKW"), msg(12, "лол")])
    signals.run(job, cfg)
    s = load_json(job.p("signals.json"))["series"]
    assert set(s) == {"chat_rate", "chat_users", "emote_hype", "loudness"}
    assert all(len(v) == 31 for v in s.values())
    loud = np.array(s["loudness"])
    assert loud.argmax() == 10
    assert (loud[25:] == -100).all()
