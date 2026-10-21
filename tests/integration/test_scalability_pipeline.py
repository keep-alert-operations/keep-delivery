"""Real SQL/ES evidence for bounded projection, failure isolation and leases."""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4
import json

import pytest

from appchi.search import reconciliation
from appchi.search.projection import index_mapping
from appchi.shared.errors import unavailable
from test_kafka_first import apply, receipt

pytestmark = pytest.mark.integration


def configuration(tenant):
    return SimpleNamespace(
        environment="development",
        global_tenant_id=tenant,
        elasticsearch_url=os.getenv("APPCHI_TEST_ELASTICSEARCH_URL"),
        elasticsearch_index="appchi-scale-" + uuid4().hex,
    )


def test_real_search_keeps_all_notes_above_old_document_cap(real_db, tenant, v1):
    config = configuration(tenant)
    if not config.elasticsearch_url:
        pytest.skip("Requires isolated Elasticsearch")
    alert = UUID(json.loads(apply(real_db, config, receipt(config, v1)))["alert_id"])
    notes = [" ".join(f"n{i:03d}_{j:04d}" for j in range(900)) for i in range(120)]
    with real_db.transaction(tenant) as conn:
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO alert_notes(id,tenant_id,alert_id,text,actor_id,created_at) VALUES(%s,%s,%s,%s,%s,clock_timestamp())",
                [(uuid4(), tenant, alert, note, uuid4()) for note in notes],
            )
    service = reconciliation.ProjectionService(
        real_db, config, {"search_max_document_bytes": 2 * 1024 * 1024}
    )
    client = service.client
    service.client = Mock(wraps=client)
    try:
        client.options(request_timeout=30).indices.create(
            index=config.elasticsearch_index,
            mappings=index_mapping(),
            settings={"number_of_shards": 1, "number_of_replicas": 0},
        )
        # A bounded transport timeout is an uncertain write, so exercise the
        # same immutable retry used by the worker rather than widening it.
        for attempt in range(3):
            try:
                assert service.project(tenant, [alert]) == {str(alert)}
                break
            except Exception as error:
                if getattr(error, "code", None) != "projection_unavailable" or attempt == 2:
                    raise
        writes = service.client.bulk.call_count
        assert writes >= 1
        result = client.search(
            index=config.elasticsearch_index, query={"term": {"search_tokens": "n119_0899"}}
        )
        assert result["hits"]["total"]["value"] == 1
        # A complete repeat verifies visible coverage without another refresh.
        assert service.project(tenant, [alert]) == {str(alert)}
        assert service.client.bulk.call_count == writes
    finally:
        client.options(request_timeout=30).indices.delete(
            index=config.elasticsearch_index, ignore_unavailable=True
        )
        service.close()


def seed_scan(real_db, tenant):
    epoch = str(uuid4())
    with real_db.transaction() as conn:
        conn.execute("UPDATE capability_gates SET ready=true WHERE capability='search'")
        conn.execute(
            "UPDATE tenant_view_state SET index_epoch=%s WHERE tenant_id=%s", (epoch, tenant)
        )
        conn.execute("""INSERT INTO search_reconciliation(index_epoch,tenant_id,boundary_seq,retry_after)
            SELECT index_epoch,tenant_id,0,clock_timestamp()+interval '1 hour' FROM tenant_view_state
            ON CONFLICT(index_epoch,tenant_id) DO UPDATE SET retry_after=EXCLUDED.retry_after""")
        conn.execute(
            "UPDATE search_reconciliation SET retry_after=NULL,last_page_at='2000-01-01' WHERE index_epoch=%s AND tenant_id=%s",
            (epoch, tenant),
        )
    return epoch


def test_empty_and_deleted_notes_skip_streaming_but_live_notes_stay_searchable(real_db, tenant, v1):
    config = configuration(tenant)
    offset = uuid4().int >> 66
    alerts = [
        UUID(
            json.loads(
                apply(
                    real_db, config, receipt(config, dict(v1, node_name=f"notes-{i}")), offset + i
                )
            )["alert_id"]
        )
        for i in range(3)
    ]
    with real_db.transaction(tenant) as conn:
        for alert, deleted, text in (
            (alerts[1], True, "deletedonlytoken"),
            (alerts[2], False, "livestreamtoken"),
        ):
            conn.execute(
                "INSERT INTO alert_notes(id,tenant_id,alert_id,text,actor_id,created_at,deleted) VALUES(%s,%s,%s,%s,%s,clock_timestamp(),%s)",
                (uuid4(), tenant, alert, text, uuid4(), deleted),
            )
    with real_db.transaction() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        observed = Mock(wraps=conn)
        items = reconciliation.current_page(observed, tenant)
        named = [call for call in observed.cursor.call_args_list if call.kwargs.get("name")]
        assert len(named) == 1
    documents = {item.alert_id: reconciliation.build_document(item) for item in items}
    assert set(documents) == {str(alert) for alert in alerts}
    assert "livestreamtoken" in documents[str(alerts[2])]["search_tokens"]
    assert "deletedonlytoken" not in documents[str(alerts[1])]["search_tokens"]
    assert all("has_live_notes" not in document for document in documents.values())


def test_page_notes_stream_through_one_statement_grouped_per_alert(real_db, tenant, v1):
    config = configuration(tenant)
    offset = uuid4().int >> 66
    alerts = sorted(
        UUID(
            json.loads(
                apply(
                    real_db, config, receipt(config, dict(v1, node_name=f"group-{i}")), offset + i
                )
            )["alert_id"]
        )
        for i in range(4)
    )
    # Interleaved inserts across alerts, with a deleted note and a note-less alert.
    rows = [
        (alerts[2], "thirdfirst", False),
        (alerts[0], "firstfirst", False),
        (alerts[2], "thirddeleted", True),
        (alerts[1], "secondonlydeleted", True),
        (alerts[0], "firstsecond", False),
        (alerts[2], "thirdsecond", False),
    ]
    with real_db.transaction(tenant) as conn:
        for alert, text, deleted in rows:
            conn.execute(
                "INSERT INTO alert_notes(id,tenant_id,alert_id,text,actor_id,created_at,deleted) VALUES(%s,%s,%s,%s,%s,clock_timestamp(),%s)",
                (uuid4(), tenant, alert, text, uuid4(), deleted),
            )
    # Unprojected filler alerts give the planner a realistic notes volume
    # (many notes per alert), so plans below use default planner settings.
    with real_db.transaction(tenant) as conn:
        filler = [uuid4() for _ in range(300)]
        with conn.cursor() as cursor:
            with cursor.copy("COPY alert_identities(id,tenant_id) FROM STDIN") as copy:
                for alert in filler:
                    copy.write_row((alert, tenant))
            with cursor.copy(
                "COPY alert_notes(id,tenant_id,alert_id,text,actor_id,created_at,deleted) FROM STDIN"
            ) as copy:
                for alert in filler:
                    for number in range(40):
                        copy.write_row(
                            (
                                uuid4(),
                                tenant,
                                alert,
                                f"filler {number}",
                                uuid4(),
                                "2026-01-01",
                                number % 8 == 0,
                            )
                        )
        conn.execute("ANALYZE alert_notes")
    requested = [alerts[2], alerts[3], alerts[0], alerts[1]]
    with real_db.transaction() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        # The per-alert statement used before: each alert's live notes by id.
        expected = {}
        for alert in alerts:
            texts = [
                row["text"]
                for row in conn.execute(
                    "SELECT text FROM alert_notes WHERE tenant_id=%s AND alert_id=%s AND NOT deleted ORDER BY id",
                    (tenant, alert),
                )
            ]
            expected[alert] = texts
        for ids, order in ((None, alerts), (requested, requested)):
            observed = Mock(wraps=conn)
            page = reconciliation.current_page(observed, tenant, alert_ids=ids)
            assert [UUID(item.alert_id) for item in page] == order
            named = [call for call in observed.cursor.call_args_list if call.kwargs.get("name")]
            assert len(named) == 1  # One cursor for the page, not one per alert.
            for item in page:
                tokens = set(item.tokens)
                for alert, texts in expected.items():
                    present = {text for text in texts if text in tokens}
                    assert present == (set(texts) if UUID(item.alert_id) == alert else set())
            by_id = {UUID(item.alert_id): set(item.tokens) for item in page}
            assert not {"thirddeleted", "secondonlydeleted"} & set().union(*by_id.values())
        # Grouping and note order per alert match the per-alert statements,
        # for index-ordered and caller-ordered pages alike, one statement each.
        executed = []
        for ids in ([alerts[0], alerts[2]], [alerts[2], alerts[0]]):
            spy = StatementSpy(conn)
            with reconciliation.PageNotes(spy, tenant, ids) as notes:
                for alert in ids:
                    assert list(notes.of(alert)) == expected[alert]
            assert len(spy.statements) == 1
            executed.append(spy.statements[0])
        # Plan each executed statement as the cursor it runs as, with default
        # planner settings: both read the partial notes index and neither sorts
        # the page. The caller-ordered walk probes the index per alert, so it
        # at most orders one alert's notes at a time (an incremental sort).
        (ordered, ordered_params), (caller, caller_params) = executed
        assert ordered.endswith("ORDER BY alert_id,id")
        nodes = cursor_plan(conn, ordered, ordered_params)
        assert not {"Sort", "Incremental Sort"} & {node["Node Type"] for node in nodes}, nodes
        assert "alert_notes_projection_lookup" in {node.get("Index Name") for node in nodes}
        assert "WITH ORDINALITY" in caller and "array_position" not in caller
        nodes = cursor_plan(conn, caller, caller_params)
        types = [node["Node Type"] for node in nodes]
        assert "Sort" not in types and "Nested Loop" in types, types
        assert "alert_notes_projection_lookup" in {node.get("Index Name") for node in nodes}


def cursor_plan(conn, query, params):
    """Every node of the plan PostgreSQL chooses for a page notes cursor."""
    plan = conn.execute(
        "EXPLAIN (FORMAT JSON) DECLARE notes_plan_probe NO SCROLL CURSOR FOR " + query, params
    ).fetchone()
    pending, nodes = [next(iter(plan.values()))[0]["Plan"]], []
    while pending:
        node = pending.pop()
        nodes.append(node)
        pending.extend(node.get("Plans", []))
    return nodes


class StatementSpy:
    """Record the statements a named cursor executes, delegating to it."""

    def __init__(self, conn):
        self.conn, self.statements = conn, []

    def cursor(self, name):
        spy, cursor = self, self.conn.cursor(name=name)

        class Recording:
            itersize = property(
                lambda _: cursor.itersize, lambda _, value: setattr(cursor, "itersize", value)
            )

            def execute(self, query, params):
                spy.statements.append((query, params))
                return cursor.execute(query, params)

            def __iter__(self):
                return iter(cursor)

            def close(self):
                cursor.close()

        return Recording()


def plan_nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from plan_nodes(child)


def explain(conn, call, *disabled):
    query, params = call.args
    for setting in disabled:
        conn.execute(f"SET LOCAL {setting}=off")
    plan = conn.execute("EXPLAIN (FORMAT JSON) " + query, params).fetchone()
    for setting in disabled:
        conn.execute(f"RESET {setting}")
    return list(plan_nodes(next(iter(plan.values()))[0]["Plan"]))


def test_page_statements_keep_rows_and_use_the_bounding_index(real_db, tenant, v1):
    config = configuration(tenant)
    offset = uuid4().int >> 66
    alerts = sorted(
        UUID(
            json.loads(
                apply(real_db, config, receipt(config, dict(v1, node_name=f"page-{i}")), offset + i)
            )["alert_id"]
        )
        for i in range(4)
    )
    requested = [alerts[2], alerts[0], alerts[3]]
    # The single statement used before for every page; pages must not change.
    legacy = """SELECT id FROM alert_identities WHERE tenant_id=%s
        AND (%s::uuid IS NULL OR id>%s) AND (%s::uuid[] IS NULL OR id=ANY(%s::uuid[]))
        AND current_projection_seq>%s AND (%s::bigint IS NULL OR current_projection_seq<=%s)
        ORDER BY array_position(%s::uuid[],id),id LIMIT 50"""
    with real_db.transaction() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        seqs = sorted(
            row["current_projection_seq"]
            for row in conn.execute(
                "SELECT current_projection_seq FROM alert_identities WHERE tenant_id=%s", (tenant,)
            )
        )
        cases = (
            {},
            {"cursor": alerts[0]},
            {"cursor": alerts[0], "ceiling_seq": seqs[-1]},
            {"floor_seq": seqs[1]},
            {"cursor": alerts[0], "floor_seq": seqs[1]},
            {"alert_ids": requested},
        )
        for case in cases:
            cursor, ids = case.get("cursor"), case.get("alert_ids")
            floor, ceiling = case.get("floor_seq", 0), case.get("ceiling_seq")
            expected = [
                row["id"]
                for row in conn.execute(
                    legacy, (tenant, cursor, cursor, ids, ids, floor, ceiling, ceiling, ids)
                )
            ]
            assert expected, case
            observed = Mock(wraps=conn)
            page = reconciliation.current_page(observed, tenant, **case)
            assert [UUID(item.alert_id) for item in page] == expected, case
            # Every page is planned from its actual bounds, never a generic plan.
            call = observed.execute.call_args_list[0]
            assert call.kwargs == {"prepare": False}
            if cursor and not floor:
                # Full and audit pages walk the id index from the cursor bound.
                nodes = explain(conn, call, "enable_sort")
                assert not [node for node in nodes if "Sort" in node["Node Type"]], nodes
                assert any("(id >" in node.get("Index Cond", "") for node in nodes), nodes
            elif floor:
                # Delta pages read only identities changed after floor_seq.
                # Even when sorting is discouraged, they are not served by an
                # id walk that would visit unchanged identities.
                nodes = explain(conn, call, "enable_seqscan", "enable_sort")
                assert any(
                    node.get("Index Name") == "alert_identity_projection_changes"
                    and "current_projection_seq >" in node.get("Index Cond", "")
                    for node in nodes
                ), nodes
        assert expected == requested


def fake_service(db, tenant):
    service = reconciliation.ProjectionService.__new__(reconciliation.ProjectionService)
    service.db, service.settings, service.client = db, configuration(tenant), Mock()
    service.max_document_bytes = 2000000
    return service


def test_scan_failure_is_visible_and_excluded_from_next_claim(real_db, tenant, monkeypatch):
    epoch = seed_scan(real_db, tenant)
    with real_db.transaction() as conn:
        healthy = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
        conn.execute(
            """UPDATE search_reconciliation r SET retry_after=NULL,last_page_at='2001-01-01'
            FROM tenant_view_state v WHERE r.tenant_id=v.tenant_id AND r.index_epoch=v.index_epoch
            AND r.tenant_id=%s""",
            (healthy,),
        )
    monkeypatch.setattr(
        reconciliation,
        "reconcile_page",
        Mock(side_effect=[unavailable("projection_document_capacity"), True]),
    )
    service = fake_service(real_db, tenant)
    with real_db.transaction() as conn:
        before = conn.execute(
            "SELECT searchable_seq FROM tenant_view_state WHERE tenant_id=%s", (tenant,)
        ).fetchone()
    assert service.reconcile() == 0
    assert service.reconcile() == 1
    assert reconciliation.reconcile_page.call_count == 2
    assert reconciliation.reconcile_page.call_args.args[3] == healthy
    with real_db.transaction() as conn:
        state = conn.execute(
            "SELECT * FROM search_reconciliation WHERE index_epoch=%s AND tenant_id=%s",
            (epoch, tenant),
        ).fetchone()
        assert state["last_error"] == "projection_document_capacity"
        assert state["retry_after"] is not None and state["lease_owner"] is None
        assert state["cursor"] is None and state["completed_at"] is None
        assert (
            conn.execute(
                "SELECT searchable_seq FROM tenant_view_state WHERE tenant_id=%s", (tenant,)
            ).fetchone()
            == before
        )


def test_second_replica_cannot_publish_claimed_page(real_db, tenant, monkeypatch):
    epoch = seed_scan(real_db, tenant)
    started, release = Event(), Event()

    def publish(db, client, physical, selected_tenant, selected_epoch, **kwargs):
        assert selected_tenant == tenant and selected_epoch == epoch
        with db.transaction() as conn:
            reconciliation.check_claim(conn, epoch, tenant, kwargs["claim"])
        started.set()
        assert release.wait(20)

    monkeypatch.setattr(reconciliation, "reconcile_page", publish)
    first, second = fake_service(real_db, tenant), fake_service(real_db, tenant)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(first.reconcile)
        try:
            assert started.wait(10)
            assert second.reconcile() == 0
        finally:
            release.set()
        assert pending.result(10) == 1
    with real_db.transaction() as conn:
        state = conn.execute(
            "SELECT lease_owner,lease_until FROM search_reconciliation WHERE index_epoch=%s AND tenant_id=%s",
            (epoch, tenant),
        ).fetchone()
        assert state == {"lease_owner": None, "lease_until": None}


def test_completed_empty_tenant_yields_to_pending_tenant(real_db, tenant, monkeypatch):
    epoch = seed_scan(real_db, tenant)
    with real_db.transaction() as conn:
        pending = conn.execute("SELECT id FROM tenants WHERE kind='global'").fetchone()["id"]
        conn.execute(
            """UPDATE search_reconciliation r SET retry_after=NULL,last_page_at='2001-01-01'
            FROM tenant_view_state v WHERE r.tenant_id=v.tenant_id AND r.index_epoch=v.index_epoch
                AND r.tenant_id=%s""",
            (pending,),
        )
    page = Mock(return_value=[])
    monkeypatch.setattr(reconciliation, "current_page", page)
    service = fake_service(real_db, tenant)
    assert service.reconcile() == 1
    assert page.call_args.args[1] == tenant
    with real_db.transaction() as conn:
        # Make the completed idle tenant eligible again without sleeping.
        conn.execute(
            """UPDATE search_reconciliation SET completed_at=clock_timestamp()-interval '3 seconds'
                WHERE index_epoch=%s AND tenant_id=%s""",
            (epoch, tenant),
        )
    assert service.reconcile() == 1
    assert page.call_args.args[1] == pending


def test_expired_replaced_claim_cannot_advance_watermark(real_db, tenant):
    epoch = seed_scan(real_db, tenant)
    old, new = uuid4(), uuid4()
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE search_reconciliation SET lease_owner=%s,lease_until=clock_timestamp()+interval '30 seconds' WHERE index_epoch=%s AND tenant_id=%s",
            (new, epoch, tenant),
        )
    client = Mock()
    with pytest.raises(Exception) as failure:
        reconciliation.reconcile_page(real_db, client, "physical", tenant, epoch, claim=old)
    assert failure.value.code == "search_reconciliation_fenced"
    client.bulk.assert_not_called()


def test_delta_scan_repairs_lost_hint_and_behind_cursor_write(real_db, tenant, v1, monkeypatch):
    config = configuration(tenant)
    if not config.elasticsearch_url:
        pytest.skip("Requires isolated Elasticsearch")
    payloads = [dict(v1, node_name="delta-" + str(i)) for i in range(2)]
    # real_db is module-scoped: every synthetic delivery needs a distinct
    # transport coordinate even when its business tenant differs.
    offset_base = uuid4().int >> 66
    alerts = {
        UUID(
            json.loads(apply(real_db, config, receipt(config, payload), offset_base + i))[
                "alert_id"
            ]
        ): payload
        for i, payload in enumerate(payloads)
    }
    ordered = sorted(alerts)
    service = reconciliation.ProjectionService(real_db, config)
    client = service.client
    epoch = str(uuid4())
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE tenant_view_state SET index_epoch=%s WHERE tenant_id=%s", (epoch, tenant)
        )

    def turn():
        return reconciliation.reconcile_page(
            real_db, client, config.elasticsearch_index, tenant, epoch
        )

    try:
        client.options(request_timeout=30).indices.create(
            index=config.elasticsearch_index,
            mappings=index_mapping(),
            settings={"number_of_shards": 1, "number_of_replicas": 0},
        )
        assert not turn()
        assert turn()
        # Source commits are deliberately never offered as indexer Kafka hints.
        for offset, alert in enumerate(ordered, offset_base + 2):
            apply(
                real_db, config, receipt(config, dict(alerts[alert], message="lost hint")), offset
            )
        monkeypatch.setattr(reconciliation, "PAGE_SIZE", 1)
        assert not turn()
        with real_db.transaction() as conn:
            state = conn.execute(
                "SELECT * FROM search_reconciliation WHERE tenant_id=%s AND index_epoch=%s",
                (tenant, epoch),
            ).fetchone()
        assert state["floor_seq"] > 0 and state["cursor"] == ordered[0]
        # This new source write is behind the current immutable identity cursor.
        apply(
            real_db,
            config,
            receipt(config, dict(alerts[ordered[0]], message="behind cursor")),
            offset_base + 4,
        )
        assert not turn()
        assert turn()
        with real_db.transaction() as conn:
            view = conn.execute(
                "SELECT searchable_seq FROM tenant_view_state WHERE tenant_id=%s", (tenant,)
            ).fetchone()
            authority = conn.execute(
                "SELECT projection_seq FROM tenant_work_state WHERE tenant_id=%s", (tenant,)
            ).fetchone()
        assert view["searchable_seq"] == state["boundary_seq"] < authority["projection_seq"]
        assert not turn()
        assert turn()
        assert (
            client.search(
                index=config.elasticsearch_index, query={"term": {"search_tokens": "behind"}}
            )["hits"]["total"]["value"]
            == 1
        )
        assert (
            client.search(
                index=config.elasticsearch_index, query={"term": {"search_tokens": "lost"}}
            )["hits"]["total"]["value"]
            == 1
        )
    finally:
        client.options(request_timeout=30).indices.delete(
            index=config.elasticsearch_index, ignore_unavailable=True
        )
        service.close()


def test_periodic_audit_repairs_missing_doc_without_blocking_delta(
    real_db, tenant, v1, monkeypatch
):
    config = configuration(tenant)
    if not config.elasticsearch_url:
        pytest.skip("Requires isolated Elasticsearch")
    offset = uuid4().int >> 66
    payloads = [dict(v1, node_name="audit-" + str(i)) for i in range(2)]
    alerts = {
        UUID(
            json.loads(apply(real_db, config, receipt(config, payload), offset + i))["alert_id"]
        ): payload
        for i, payload in enumerate(payloads)
    }
    first, last = sorted(alerts)
    service = reconciliation.ProjectionService(real_db, config)
    client = service.client
    epoch = str(uuid4())
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE tenant_view_state SET index_epoch=%s WHERE tenant_id=%s", (epoch, tenant)
        )

    def turn():
        return reconciliation.reconcile_page(
            real_db, client, config.elasticsearch_index, tenant, epoch
        )

    try:
        client.options(request_timeout=30).indices.create(
            index=config.elasticsearch_index,
            mappings=index_mapping(),
            settings={"number_of_shards": 1, "number_of_replicas": 0},
        )
        assert not turn()
        assert turn()
        hit = client.get(index=config.elasticsearch_index, id=str(last))
        # Simulate a missing projection without inventing a newer authority
        # version. Foreign higher-version tombstones must remain fenced.
        client.delete(
            index=config.elasticsearch_index,
            id=str(last),
            refresh=True,
            version=hit["_version"],
            version_type="external_gte",
        )
        assert turn()  # No-change delta does not reread all retained notes.
        assert client.count(index=config.elasticsearch_index)["count"] == 1
        with real_db.transaction() as conn:
            # Isolate this tenant from other tests sharing the database.
            conn.execute("""INSERT INTO search_reconciliation(index_epoch,tenant_id,boundary_seq,retry_after)
                SELECT index_epoch,tenant_id,0,clock_timestamp()+interval '1 hour' FROM tenant_view_state
                ON CONFLICT(index_epoch,tenant_id) DO UPDATE SET retry_after=EXCLUDED.retry_after""")
            conn.execute(
                """UPDATE search_reconciliation SET retry_after=NULL,
                full_audit_at=clock_timestamp()-interval '6 minutes',audit_turns=4,
                completed_at='2000-01-01',last_page_at='2000-01-01'
                WHERE tenant_id=%s AND index_epoch=%s""",
                (tenant, epoch),
            )
        monkeypatch.setattr(reconciliation, "PAGE_SIZE", 1)
        assert service.reconcile() == 1  # First audit page, not its completion.
        with real_db.transaction() as conn:
            audit = conn.execute(
                "SELECT * FROM search_reconciliation WHERE tenant_id=%s AND index_epoch=%s",
                (tenant, epoch),
            ).fetchone()
        assert audit["audit_cursor"] == first and audit["audit_boundary_seq"] == 2
        apply(
            real_db,
            config,
            receipt(config, dict(alerts[first], message="during audit")),
            offset + 2,
        )
        apply(real_db, config, receipt(config, dict(v1, node_name="new-during-audit")), offset + 3)
        # Three ordinary turns publish two new snapshots and complete the delta.
        for _ in range(3):
            assert service.reconcile() == 1
        with real_db.transaction() as conn:
            state = conn.execute(
                "SELECT * FROM search_reconciliation WHERE tenant_id=%s AND index_epoch=%s",
                (tenant, epoch),
            ).fetchone()
            verified = conn.execute(
                "SELECT searchable_seq FROM tenant_view_state WHERE tenant_id=%s", (tenant,)
            ).fetchone()["searchable_seq"]
        assert verified == 4
        assert state["audit_cursor"] == first and state["audit_boundary_seq"] == 2
        assert client.count(index=config.elasticsearch_index)["count"] == 2
        assert service.reconcile() == 1  # Fourth ordinary turn before another audit.
        assert service.reconcile() == 1  # Second audit page repairs missing old doc.
        assert client.count(index=config.elasticsearch_index)["count"] == 3
        for _ in range(5):  # Four ordinary turns, then bounded-generation audit completion.
            assert service.reconcile() == 1
        with real_db.transaction() as conn:
            state = conn.execute(
                "SELECT * FROM search_reconciliation WHERE tenant_id=%s AND index_epoch=%s",
                (tenant, epoch),
            ).fetchone()
            assert state["audit_boundary_seq"] is None and state["audit_started_at"] is None
            assert state["audit_cursor"] is None and state["audit_turns"] == 0
            assert (
                conn.execute(
                    "SELECT searchable_seq FROM tenant_view_state WHERE tenant_id=%s", (tenant,)
                ).fetchone()["searchable_seq"]
                == verified
            )
    finally:
        client.options(request_timeout=30).indices.delete(
            index=config.elasticsearch_index, ignore_unavailable=True
        )
        service.close()


def test_source_rechecks_reused_maintenance_gate_before_commit(real_db, tenant, v1, monkeypatch):
    from appchi import kafka_source

    config = configuration(tenant)
    record = receipt(config, v1)
    offset = uuid4().int >> 66
    # The source path takes the tenant coordinator lock once, in lock_coordinator.
    coordinator = kafka_source.lock_coordinator
    with real_db.transaction() as conn:
        prior = conn.execute(
            "SELECT ready FROM capability_gates WHERE capability='maintenance'"
        ).fetchone()["ready"]
        conn.execute("UPDATE capability_gates SET ready=false WHERE capability='maintenance'")

    def raced_coordinator(conn, selected_tenant):
        with real_db.transaction() as publication:
            publication.execute(
                "UPDATE capability_gates SET ready=true WHERE capability='maintenance'"
            )
        return coordinator(conn, selected_tenant)

    monkeypatch.setattr(kafka_source, "lock_coordinator", raced_coordinator)
    try:
        with pytest.raises(Exception) as failure:
            apply(real_db, config, record, offset)
        assert failure.value.code == "maintenance_gate_changed"
        with real_db.transaction() as conn:
            assert not conn.execute(
                "SELECT 1 FROM kafka_source_results WHERE delivery_id=%s",
                (json.loads(record[2])["event_id"],),
            ).fetchone()
            assert not conn.execute("SELECT 1 FROM alerts WHERE tenant_id=%s", (tenant,)).fetchone()
            conn.execute("UPDATE capability_gates SET ready=false WHERE capability='maintenance'")
        monkeypatch.setattr(kafka_source, "lock_coordinator", coordinator)
        # The same immutable delivery remains retryable after the gate settles.
        assert json.loads(apply(real_db, config, record, offset))["alert_id"]
    finally:
        with real_db.transaction() as conn:
            conn.execute(
                "UPDATE capability_gates SET ready=%s WHERE capability='maintenance'", (prior,)
            )


@pytest.mark.parametrize("takeover_check", [1, 3])
def test_audit_rechecks_ownership_after_row_lock(real_db, tenant, monkeypatch, takeover_check):
    epoch = seed_scan(real_db, tenant)
    owner, replacement = uuid4(), uuid4()
    with real_db.transaction() as conn:
        conn.execute(
            "UPDATE search_reconciliation SET lease_owner=%s,lease_until=clock_timestamp()+interval '30 seconds' WHERE tenant_id=%s AND index_epoch=%s",
            (owner, tenant, epoch),
        )
    original, checks = reconciliation.check_claim, 0

    def replace_after_check(conn, selected_epoch, selected_tenant, claim):
        nonlocal checks
        original(conn, selected_epoch, selected_tenant, claim)
        checks += 1
        if checks == takeover_check:
            # Deterministically model expiry/takeover after the optimistic
            # check but before the old owner obtains the row lock.
            with real_db.transaction() as replacement_conn:
                replacement_conn.execute(
                    "UPDATE search_reconciliation SET lease_owner=%s,lease_until=clock_timestamp()+interval '30 seconds' WHERE tenant_id=%s AND index_epoch=%s",
                    (replacement, tenant, epoch),
                )

    monkeypatch.setattr(reconciliation, "check_claim", replace_after_check)
    with pytest.raises(Exception) as failure:
        reconciliation.audit_page(
            real_db, Mock(), "physical", tenant, epoch, max_document_bytes=2000000, claim=owner
        )
    assert failure.value.code == "search_reconciliation_fenced"
    with real_db.transaction() as conn:
        state = conn.execute(
            "SELECT * FROM search_reconciliation WHERE tenant_id=%s AND index_epoch=%s",
            (tenant, epoch),
        ).fetchone()
        assert state["lease_owner"] == replacement
        assert state["full_audit_at"] is None and state["audit_cursor"] is None
        if takeover_check == 1:
            assert state["audit_started_at"] is None


def test_failed_audit_does_not_backoff_ordinary_delta(real_db, tenant, monkeypatch):
    epoch = seed_scan(real_db, tenant)
    with real_db.transaction() as conn:
        conn.execute(
            """UPDATE search_reconciliation SET completed_at='2000-01-01',
            full_audit_at='2000-01-01',audit_turns=4 WHERE tenant_id=%s AND index_epoch=%s""",
            (tenant, epoch),
        )
    audit = Mock(side_effect=unavailable("projection_document_capacity"))
    ordinary = Mock(return_value=True)
    monkeypatch.setattr(reconciliation, "audit_page", audit)
    monkeypatch.setattr(reconciliation, "reconcile_page", ordinary)
    service = fake_service(real_db, tenant)
    assert service.reconcile() == 0
    assert service.reconcile() == 1
    assert audit.call_count == 1 and ordinary.call_count == 1
    assert ordinary.call_args.args[3] == tenant
    with real_db.transaction() as conn:
        state = conn.execute(
            "SELECT * FROM search_reconciliation WHERE tenant_id=%s AND index_epoch=%s",
            (tenant, epoch),
        ).fetchone()
        assert state["audit_last_error"] == "projection_document_capacity"
        assert state["audit_retry_after"] is not None
        assert state["retry_after"] is None and state["last_error"] is None
