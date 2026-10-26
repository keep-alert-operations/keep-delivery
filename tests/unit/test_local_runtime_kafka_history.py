"""The local launcher refuses workers on a Kafka topic that restarted under PG proof."""

import importlib.util
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[2] / "scripts/local_runtime.py"
spec = importlib.util.spec_from_file_location("local_runtime", SOURCE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
RECEIVED = "appchi.development.alerts.received"
CHANGED = "appchi.development.alerts.changed"
DELIVERY = "3732b86c-24c5-4601-84b4-4f8720b1fac2"


def partition(topic=RECEIVED, *, low=0, high=7, required=7, recorded=None, event=None):
    item = {"topic": topic, "partition": 0, "low": low, "high": high, "required": required}
    if recorded is not None:
        item["recorded"] = recorded
        item["broker_event_id"] = event
    return item


def test_matching_history_passes():
    """Requirements: Kafka pipeline 8; verification plan Kafka-first redesign (REV-33) Recovery."""
    assert (
        runtime.incarnation_problems(
            [
                partition(recorded=(6, DELIVERY), event=DELIVERY),
                partition(CHANGED, high=12, required=12),
            ]
        )
        == []
    )


def test_recreated_topic_ending_below_recorded_offsets_is_refused():
    """Requirements: Kafka pipeline 8; verification plan Kafka-first redesign (REV-33) Recovery."""
    problems = runtime.incarnation_problems([partition(high=7, required=10)])
    assert problems == [f"{RECEIVED}:0 ends at offset 7, below recorded offset 10"]


def test_recreated_topic_reusing_a_recorded_offset_is_refused():
    """Requirements: Kafka pipeline 8; verification plan Kafka-first redesign (REV-33) Recovery."""
    problems = runtime.incarnation_problems(
        [partition(high=12, required=10, recorded=(9, DELIVERY), event="another")]
    )
    assert problems == [f"{RECEIVED}:0 offset 9 holds a different delivery"]


def test_retention_below_the_recorded_offset_skips_identity():
    """Requirements: Kafka pipeline 8; verification plan Kafka-first redesign (REV-33) Recovery."""
    assert (
        runtime.incarnation_problems(
            [partition(low=10, high=12, required=10, recorded=(9, DELIVERY), event=None)]
        )
        == []
    )


def test_changed_positions_beyond_the_broker_are_refused():
    """Requirements: Kafka pipeline 8; verification plan Kafka-first redesign (REV-33) Recovery."""
    problems = runtime.incarnation_problems([partition(CHANGED, high=12, required=34)])
    assert problems == [f"{CHANGED}:0 ends at offset 12, below recorded offset 34"]


def test_probe_refuses_to_recreate_topics_after_cutover():
    """Requirements: Kafka pipeline 8; verification plan Kafka-first redesign (REV-33) Recovery."""
    source = SOURCE.read_text(encoding="utf-8")
    refusal = source.index("if missing and cutover:")
    assert refusal < source.index("kafka.create_topics(missing)")
    assert source.index("require_topic_history(") < source.index("application topics exist")
