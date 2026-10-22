from collections import deque
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

from appchi.kafka_changed import ChangedConsumer, PartitionRetries
from appchi.kafka_source import SourceConsumer


def message(partition, offset):
    return Mock(
        topic=lambda: "topic",
        partition=lambda: partition,
        offset=lambda: offset,
        error=lambda: None,
        value=lambda: b"{}",
        key=lambda: b"key",
    )


def database():
    conn = Mock()

    @contextmanager
    def transaction(*args, **kwargs):
        yield conn

    return SimpleNamespace(transaction=transaction), conn


def changed(messages, handler, batch_handler=None):
    worker = ChangedConsumer.__new__(ChangedConsumer)
    worker.db, conn = database()
    worker.settings, worker.profile, worker.role = Mock(), None, "indexer"
    worker.handler, worker.batch_handler = handler, batch_handler
    worker.batch_size, worker.pending = 50, deque()
    worker.client = Mock()
    worker.client.consume.return_value = messages
    worker.client.poll.return_value = None
    worker.retries = PartitionRetries(worker.client)
    return worker, conn


def test_failed_partition_is_not_committed_and_healthy_partition_progresses():
    bad, good = message(0, 7), message(1, 3)

    def handler(db, settings, profile, item):
        if item.partition() == 0:
            raise ValueError("poison")

    worker, conn = changed([bad, good], handler)
    assert worker.turn() == 0
    worker.client.commit.assert_not_called()
    point = worker.client.seek.call_args.args[0]
    assert (point.partition, point.offset) == (0, 7)
    assert worker.turn() == 1
    worker.client.commit.assert_called_once_with(message=good, asynchronous=False)
    assert worker.client.close.call_count == 0
    assert ("topic", 0) in worker.retries.failed
    assert any("changed_processing_failed" in call.args[0] for call in conn.execute.call_args_list)


def test_partial_batch_commits_covered_prefix_then_rotates_partitions():
    items = [message(0, i) for i in range(3)] + [message(1, 0)]
    batch = Mock(side_effect=[2, 1, 1])
    worker, _ = changed(items, Mock(), batch)
    assert worker.turn() == 2
    assert worker.client.commit.call_args.kwargs["message"] is items[1]
    assert worker.turn() == 1
    assert worker.client.commit.call_args.kwargs["message"] is items[3]
    assert worker.turn() == 1
    assert worker.client.commit.call_args.kwargs["message"] is items[2]


def test_later_poison_does_not_fence_before_healthy_prefix():
    items = [message(0, 4), message(0, 5)]

    def handler(db, settings, profile, item):
        if item.offset() == 5:
            raise ValueError("poison")

    worker, _ = changed(items, handler, Mock(side_effect=ValueError("batch contains poison")))
    assert worker.turn() == 1
    assert worker.client.commit.call_args.kwargs["message"] is items[0]
    assert worker.turn() == 0
    assert worker.client.commit.call_count == 1
    assert worker.client.seek.call_args.args[0].offset == 5


def test_retry_resumes_exact_failed_offset_after_backoff(monkeypatch):
    from appchi import kafka_changed

    clock = [10.0]
    monkeypatch.setattr(kafka_changed, "monotonic", lambda: clock[0])
    client = Mock()
    retry = PartitionRetries(client)
    retry.fail(message(3, 11))
    clock[0] = 14
    retry.resume_due()
    client.resume.assert_not_called()
    clock[0] = 15
    retry.resume_due()
    assert client.resume.call_args.args[0][0].offset == 11
    assert not retry.failed


def test_source_application_failure_retains_offset_without_kafka_transaction(monkeypatch):
    from appchi import kafka_source, clock_health

    monkeypatch.setattr(clock_health, "require_clock_health", lambda *args: None)
    worker = SourceConsumer.__new__(SourceConsumer)
    worker.db, conn = database()
    worker.settings, worker.profile = Mock(), None
    worker.client = Mock()
    worker.client.consume.return_value = []
    worker.client.poll.side_effect = [message(0, 8), message(1, 2)]
    worker.retries = PartitionRetries(worker.client)
    bad_producer, good_producer = Mock(), Mock()
    worker.producers = {0: bad_producer, 1: good_producer}
    monkeypatch.setattr(
        kafka_source, "apply_record", Mock(side_effect=[ValueError("poison"), None])
    )
    assert worker.turn() == 0
    bad_producer.begin_transaction.assert_not_called()
    assert worker.client.seek.call_args.args[0].offset == 8
    assert worker.turn() == 1
    good_producer.commit_transaction.assert_called_once()
    assert any(
        "kafka_source_results SET kafka_confirmed" in call.args[0]
        for call in conn.execute.call_args_list
    )


def test_correlation_retention_gap_fences_only_lost_partition():
    from confluent_kafka import TopicPartition

    worker, _ = changed([], Mock())
    worker.closing, worker.role = False, "correlation"
    worker.settings = SimpleNamespace(environment="test")
    worker.control = {"changed_partitions": 2}
    topic = "appchi.test.alerts.changed"
    worker.client.list_topics.return_value = SimpleNamespace(
        topics={topic: SimpleNamespace(partitions={0: None, 1: None})}
    )
    worker.client.committed.return_value = [SimpleNamespace(offset=0)]
    worker.client.get_watermark_offsets.side_effect = [(1, 5), (0, 5)]
    worker._assign(worker.client, [TopicPartition(topic, 0), TopicPartition(topic, 1)])
    assert worker.client.pause.call_args.args[0][0].partition == 0
    worker.client.commit.assert_not_called()
    assert worker.retries.failed[(topic, 0)][1] == float("inf")
    assert (topic, 1) not in worker.retries.failed
    good = message(1, 0)
    worker.client.consume.return_value = [good]
    assert worker.turn() == 1
    worker.client.commit.assert_called_once_with(message=good, asynchronous=False)
