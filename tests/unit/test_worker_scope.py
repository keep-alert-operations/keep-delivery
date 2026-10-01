from dataclasses import replace
from uuid import uuid4

import pytest

from appchi.maintenance.temporal import reduce_alert
from appchi.release_scope import DEFERRED_WORKERS
from test_maintenance_temporal import at, segment, snapshot, transition


def reduce(events, segments=None):
    return reduce_alert(uuid4(), tuple(segments or [segment()]), tuple(events),
                        now=at(90), recorded_at=at(90), proof_complete=True,
                        source_release_since=at(-20))


def test_time_alone_retains_suppression():
    result = reduce([transition(10, None, snapshot())])
    assert result.current_causes
    assert result.causes[0].closed is None
    assert not result.final_unsuppression_qualifiers


def test_source_repeat_after_end_releases_at_receipt_not_window_end():
    state = snapshot()
    result = reduce([transition(10, None, state), transition(70, state, state, order=2)])
    assert not result.current_causes
    assert result.causes[0].closed.at == at(70)
    assert result.final_unsuppression_qualifiers == (at(70),)


def test_manual_note_or_status_after_end_does_not_apply_timer():
    state, resolved = snapshot(), snapshot(status="resolved")
    result = reduce([transition(10, None, state),
                     replace(transition(70, state, resolved, order=2), kind="manual")])
    assert result.current_causes
    assert not result.final_unsuppression_qualifiers


def test_overlap_and_source_suspension_are_independent():
    state = snapshot(version="v2")
    result = reduce([transition(10, None, state, source=True),
                     transition(70, state, state, order=2, source=True)],
                    [segment(), segment(end=80)])
    assert len(result.current_causes) == 2
    assert "source:v2" in result.current_causes


def test_history_before_cutover_keeps_its_original_release():
    result = reduce_alert(uuid4(), (segment(),), (transition(10, None, snapshot()),),
                          now=at(90), recorded_at=at(90), proof_complete=True,
                          source_release_since=at(65))
    assert not result.current_causes
    assert result.causes[0].closed.at == at(60)


@pytest.mark.parametrize("role", sorted(DEFERRED_WORKERS))
def test_deferred_roles_fail_before_settings_or_database(role, monkeypatch, capsys):
    from appchi.worker import main
    monkeypatch.setattr("sys.argv", ["worker", role])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "automation is deferred" in capsys.readouterr().err
