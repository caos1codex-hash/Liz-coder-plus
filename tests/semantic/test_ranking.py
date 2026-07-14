"""Tests for RankingEngine."""

import pytest

from multiagent.memctx.models import MemoryRecord, MemoryType
from multiagent.semantic.ranking import (
    RankingConfig,
    RankingEngine,
    RankingSignal,
    RankedResult,
)


def _make_record(
    record_id: str,
    text: str = "content",
    priority: int = 0,
    updated_at: str = "",
    memory_type: MemoryType = MemoryType.KNOWLEDGE,
) -> MemoryRecord:
    from datetime import datetime, timezone
    if not updated_at:
        updated_at = datetime.now(timezone.utc).isoformat()
    return MemoryRecord(
        id=record_id,
        content={"text": text},
        priority=priority,
        updated_at=updated_at,
        memory_type=memory_type,
    )


class TestRankingConfig:
    def test_default_weights(self):
        config = RankingConfig()
        assert config.get_weight(RankingSignal.SEMANTIC_SIMILARITY) == 0.5
        assert config.get_weight(RankingSignal.PRIORITY) == 0.2
        assert config.get_weight(RankingSignal.RECENCY) == 0.15
        assert config.get_weight(RankingSignal.FREQUENCY) == 0.1
        assert config.get_weight(RankingSignal.MEMORY_TYPE) == 0.05

    def test_set_weight(self):
        config = RankingConfig()
        config.set_weight(RankingSignal.PRIORITY, 0.8)
        assert config.get_weight(RankingSignal.PRIORITY) == 0.8

    def test_set_weight_invalid(self):
        config = RankingConfig()
        with pytest.raises(ValueError):
            config.set_weight(RankingSignal.PRIORITY, 1.5)
        with pytest.raises(ValueError):
            config.set_weight(RankingSignal.PRIORITY, -0.1)

    def test_type_preferences(self):
        config = RankingConfig(type_preferences={"knowledge": 0.3})
        assert config.type_preferences["knowledge"] == 0.3

    def test_to_dict(self):
        config = RankingConfig()
        d = config.to_dict()
        assert "weights" in d
        assert "type_preferences" in d
        assert "recency_decay_hours" in d


class TestRankingEngine:
    @pytest.fixture
    def engine(self):
        return RankingEngine()

    def test_rank_by_priority(self, engine):
        records = [
            _make_record("low", priority=1),
            _make_record("high", priority=10),
            _make_record("mid", priority=5),
        ]
        ranked = engine.rank(records)
        assert ranked[0].record.id == "high"
        assert ranked[1].record.id == "mid"
        assert ranked[2].record.id == "low"

    def test_rank_with_semantic_scores(self, engine):
        records = [
            _make_record("a"),
            _make_record("b"),
        ]
        semantic_scores = {"a": 0.9, "b": 0.3}
        ranked = engine.rank(records, semantic_scores=semantic_scores)
        assert ranked[0].record.id == "a"
        assert ranked[0].semantic_score == 0.9

    def test_rank_with_use_counts(self, engine):
        records = [
            _make_record("freq"),
            _make_record("rare"),
        ]
        use_counts = {"freq": 50, "rare": 1}
        ranked = engine.rank(records, use_counts=use_counts)
        # Higher frequency should contribute positively
        freq_score = ranked[0].signal_scores.get(RankingSignal.FREQUENCY.value, 0)
        rare_score = ranked[1].signal_scores.get(RankingSignal.FREQUENCY.value, 0)
        assert freq_score >= rare_score

    def test_rank_combined_signals(self, engine):
        records = [
            _make_record("old-low", priority=1),
            _make_record("new-high", priority=10),
        ]
        semantic_scores = {"old-low": 0.95, "new-high": 0.8}
        ranked = engine.rank(records, semantic_scores=semantic_scores)
        # High priority + decent semantic should win
        for r in ranked:
            assert r.final_score >= 0

    def test_rank_empty_records(self, engine):
        ranked = engine.rank([])
        assert ranked == []

    def test_rank_with_type_preferences(self):
        config = RankingConfig(
            type_preferences={"knowledge": 0.5, "conversation": 0.1}
        )
        engine = RankingEngine(config)

        records = [
            _make_record("conv", memory_type=MemoryType.CONVERSATION),
            _make_record("know", memory_type=MemoryType.KNOWLEDGE),
        ]
        ranked = engine.rank(records)
        # Knowledge should get a type bonus
        know_type_score = ranked[0].signal_scores.get(RankingSignal.MEMORY_TYPE.value, 0)
        conv_type_score = ranked[1].signal_scores.get(RankingSignal.MEMORY_TYPE.value, 0)
        assert know_type_score > conv_type_score

    def test_rank_with_min_score_threshold(self):
        config = RankingConfig(min_score_threshold=0.5)
        engine = RankingEngine(config)

        records = [
            _make_record("low"),
            _make_record("high", priority=100),
        ]
        ranked = engine.rank(records)
        # With high threshold, low-priority records may be filtered
        for r in ranked:
            assert r.final_score >= 0.5

    def test_rank_preserves_all_signals(self, engine):
        rec = _make_record("test", priority=5)
        ranked = engine.rank([rec], semantic_scores={"test": 0.7}, use_counts={"test": 10})
        result = ranked[0]

        assert RankingSignal.SEMANTIC_SIMILARITY.value in result.signal_scores
        assert RankingSignal.PRIORITY.value in result.signal_scores
        assert RankingSignal.RECENCY.value in result.signal_scores
        assert RankingSignal.FREQUENCY.value in result.signal_scores
        assert RankingSignal.MEMORY_TYPE.value in result.signal_scores

    def test_ranked_result_to_dict(self, engine):
        rec = _make_record("test")
        ranked = engine.rank([rec])
        d = ranked[0].to_dict()
        assert d["id"] == "test"
        assert "final_score" in d
        assert "signal_scores" in d
        assert "matched_by" in d
        assert "memory_type" in d

    def test_recency_decay(self, engine):
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        recent = _make_record(
            "recent", updated_at=now.isoformat(), priority=1
        )
        old = _make_record(
            "old",
            updated_at=(now - timedelta(days=30)).isoformat(),
            priority=1,
        )
        ranked = engine.rank([recent, old])
        recent_recency = ranked[0].signal_scores[RankingSignal.RECENCY.value]
        old_recency = ranked[1].signal_scores[RankingSignal.RECENCY.value]
        assert recent_recency > old_recency

    def test_custom_config(self):
        config = RankingConfig(
            weights={
                RankingSignal.PRIORITY.value: 0.8,
                RankingSignal.SEMANTIC_SIMILARITY.value: 0.1,
                RankingSignal.RECENCY.value: 0.05,
                RankingSignal.FREQUENCY.value: 0.03,
                RankingSignal.MEMORY_TYPE.value: 0.02,
            }
        )
        engine = RankingEngine(config)
        assert engine.config.get_weight(RankingSignal.PRIORITY) == 0.8

    def test_matched_by_populated(self, engine):
        rec = _make_record("test", priority=5)
        ranked = engine.rank([rec], semantic_scores={"test": 0.8}, use_counts={"test": 3})
        matched = ranked[0].matched_by
        assert len(matched) > 0
        # Should mention priority
        assert any("priority" in m for m in matched)


class TestRankingSignal:
    def test_values(self):
        assert RankingSignal.SEMANTIC_SIMILARITY.value == "semantic_similarity"
        assert RankingSignal.PRIORITY.value == "priority"
        assert RankingSignal.RECENCY.value == "recency"
        assert RankingSignal.FREQUENCY.value == "frequency"
        assert RankingSignal.MEMORY_TYPE.value == "memory_type"