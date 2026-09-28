"""The checked-in benchmark set must be usable by the ablation harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.ablation import partition_evaluation_records, score_incident


def test_versioned_article_cases_are_scored_without_manual_section_ids() -> None:
    records = json.loads(Path("eval/evaluation_set.json").read_text())["incidents"]

    assert len(records) == 35
    incidents, manual_stressors = partition_evaluation_records(records)
    assert len(incidents) == 29
    assert len(manual_stressors) == 6
    for record in incidents:
        assert record["query"]
        assert record["incident_id"]
        score_incident(record, [], 0.0, 0.05)


def test_stressor_section_id_is_never_mistaken_for_a_kb_article() -> None:
    incidents, excluded = partition_evaluation_records(
        [
            {
                "incident_id": "baseline",
                "query": "VPN fails",
                "primary_article_ids": ["KB0001-v1.0"],
                "acceptable_article_ids": [],
                "forbidden_article_ids": [],
                "is_answerable": True,
            },
            {
                "id": "table-stressor",
                "description": "A table extraction problem",
                "expected_article_id": "3.3",
                "type": "manual",
            },
        ]
    )

    assert [record["incident_id"] for record in incidents] == ["baseline"]
    assert excluded == ["table-stressor"]


def test_unknown_record_shape_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unknown evaluation record shape"):
        partition_evaluation_records([{"id": "bad"}])
