"""The topic bank loads, and the real topics.yaml is well-formed.

Two jobs. The first is the loader's validation, which exists because the ids
are foreign keys in learn.db: a duplicate id silently merges two topics'
histories, and that is unrecoverable after the fact rather than merely wrong.

The second is asserting things about the shipped topics.yaml itself. Those
assertions are cheap and they catch the edit that a YAML parse cannot — a
pasted topic with no `covers` hint grades against an empty checklist and
produces exactly the vague feedback the rubric was built to avoid.
"""

from __future__ import annotations

from collections import Counter

import pytest
import yaml
from pydantic import ValidationError

from esp_news.learn.topics import (
    DEFAULT_TOPICS_PATH,
    DIFFICULTIES,
    LearnSettings,
    TopicBank,
    load_topic_bank,
)


@pytest.fixture(scope="module")
def bank() -> TopicBank:
    return load_topic_bank()


def write_bank(tmp_path, data: dict):
    path = tmp_path / "topics.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


# ── the shipped bank ─────────────────────────────────────────────────────────


def test_the_real_topics_yaml_loads(bank):
    assert DEFAULT_TOPICS_PATH.exists()
    assert len(bank.topics) >= 60, "the bank was meant to be 60-80 topics"


def test_every_topic_has_a_usable_covers_hint(bank):
    """An empty or one-line hint is the failure that reaches the grader.

    It parses, it loads, and it produces "good overview, could go deeper" —
    the exact output the rubric exists to prevent. Length is a crude proxy for
    "names some checkable claims", but it catches a pasted stub.
    """
    thin = [t.id for t in bank.topics if len(t.covers) < 80]
    assert not thin, f"topics with a too-thin covers hint: {thin}"


def test_topic_ids_are_unique_and_stable_looking(bank):
    ids = [t.id for t in bank.topics]
    assert len(set(ids)) == len(ids)
    assert all(i == i.lower() and " " not in i for i in ids)


def test_every_difficulty_is_represented(bank):
    """The weighting has nothing to do if a tier is empty."""
    counts = Counter(t.difficulty for t in bank.topics)
    assert set(counts) == set(DIFFICULTIES)
    assert all(counts[d] >= 5 for d in DIFFICULTIES)


def test_the_cooldown_cannot_exhaust_the_bank(bank):
    """A cooldown longer than the bank can cover makes the filter useless.

    Not fatal — choose_topic drops the filter rather than failing — but it
    silently turns spaced repetition off, so it is worth catching in config.
    """
    assert bank.settings.repeat_cooldown_days < len(bank.topics)


# ── loader validation ────────────────────────────────────────────────────────


def test_a_missing_file_says_which_one(tmp_path):
    with pytest.raises(FileNotFoundError, match="topic bank not found"):
        load_topic_bank(tmp_path / "nope.yaml")


def test_an_empty_bank_is_rejected(tmp_path):
    path = write_bank(tmp_path, {"topics": []})
    with pytest.raises(ValidationError, match="nothing to draw"):
        load_topic_bank(path)


def test_duplicate_ids_are_rejected(tmp_path):
    """The one that corrupts history rather than merely failing."""
    topic = {"id": "dup", "name": "Dup", "difficulty": "intro", "covers": "x"}
    path = write_bank(tmp_path, {"topics": [topic, dict(topic, name="Other")]})
    with pytest.raises(ValidationError, match="duplicate topic ids: dup"):
        load_topic_bank(path)


def test_an_unknown_difficulty_is_rejected(tmp_path):
    path = write_bank(
        tmp_path,
        {"topics": [{"id": "a", "name": "A", "difficulty": "expert", "covers": "x"}]},
    )
    with pytest.raises(ValidationError):
        load_topic_bank(path)


def test_a_blank_covers_hint_is_rejected(tmp_path):
    path = write_bank(
        tmp_path,
        {"topics": [{"id": "a", "name": "A", "difficulty": "intro", "covers": "   "}]},
    )
    with pytest.raises(ValidationError):
        load_topic_bank(path)


def test_an_out_of_range_pass_score_is_rejected():
    with pytest.raises(ValidationError, match="between 1 and 10"):
        LearnSettings(pass_score=11)


def test_a_negative_weight_is_rejected():
    with pytest.raises(ValidationError, match="cannot be negative"):
        LearnSettings(weights={"intro": -1.0})


def test_settings_default_when_the_block_is_absent(tmp_path):
    path = write_bank(
        tmp_path,
        {"topics": [{"id": "a", "name": "A", "difficulty": "intro", "covers": "x" * 5}]},
    )
    bank = load_topic_bank(path)
    assert bank.settings.pass_score == 7
    assert bank.settings.weights.intro > bank.settings.weights.advanced


def test_get_finds_a_topic_by_id(bank):
    first = bank.topics[0]
    assert bank.get(first.id) is first
    assert bank.get("no_such_topic") is None
