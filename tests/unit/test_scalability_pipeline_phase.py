"""Pipeline-phase summaries difference worker diagnostics and compare like with like."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

QUALIFICATION = Path(__file__).resolve().parents[2] / "qualification"


def load(name):
    spec = importlib.util.spec_from_file_location(name, QUALIFICATION / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report(role, totals, samples=None):
    return {"role": role, "totals": totals, "samples": samples or {}, "instrumented": ["client"]}


def test_worker_delta_counts_only_the_scenario_and_new_processes():
    phase = load("scalability_pipeline")
    before = {
        1: report(
            "source",
            {"apply": {"count": 10, "total_ms": 50.0, "records": 10}},
            {"apply": [[5.0, 1]] * 10},
        )
    }
    after = {
        1: report(
            "source",
            {"apply": {"count": 14, "total_ms": 70.0, "records": 14}},
            {"apply": [[5.0, 1]] * 10 + [[4.0, 1], [6.0, 1], [5.0, 1], [5.0, 1]]},
        ),
        2: report("source", {"apply": {"count": 3, "total_ms": 9.0, "records": 3}}),
    }
    roles = phase.worker_delta(before, after)
    assert roles["source"]["processes"] == 2
    assert roles["source"]["totals"]["apply"] == {"count": 7, "total_ms": 29.0, "records": 7}
    assert [value[0] for value in roles["source"]["samples"]["apply"]] == [4.0, 6.0, 5.0, 5.0]


def test_source_summary_derives_confirmation_cost_per_record():
    phase = load("scalability_pipeline")
    summary = phase.summarize_role(
        {
            "processes": 1,
            "instrumented": [],
            "totals": {
                "apply": {"count": 200, "total_ms": 400.0, "records": 200},
                "consume": {"count": 4, "total_ms": 2.0, "records": 200},
                "kafka_transaction": {"count": 2, "total_ms": 30.0, "records": 200},
                "partition_confirmation": {"count": 2, "total_ms": 10.0, "records": 200},
                "turn": {"count": 4, "total_ms": 500.0, "records": 200},
                "pg_tx:apply": {"count": 200, "total_ms": 380.0, "records": 0},
                "pg_tx:confirmation": {"count": 2, "total_ms": 9.0, "records": 0},
            },
            "samples": {"consume": [[0.5, 50]] * 4},
        }
    )
    derived = summary["derived"]
    assert derived["records_per_consume"] == 50
    assert derived["records_per_publish"] == 100
    assert derived["confirmation_ms_per_record"] == 0.05
    assert derived["confirmation_share_of_active_turns"] == 0.02
    assert derived["pg_transactions_per_record"] == {"apply": 1, "confirmation": 0.01}
    assert summary["operations"]["consume"]["records_per_call"]["mean"] == 50


def test_changed_summary_counts_commits_per_handled_record():
    phase = load("scalability_pipeline")
    derived = phase.summarize_role(
        {
            "processes": 1,
            "instrumented": [],
            "totals": {
                "batch_handler": {"count": 4, "total_ms": 40.0, "records": 200},
                "kafka_commit": {"count": 4, "total_ms": 8.0, "records": 0},
            },
            "samples": {},
        }
    )["derived"]
    assert derived["records_per_handler_call"] == 50
    assert derived["kafka_commits_per_record"] == 0.02
    assert derived["handler_ms_per_record"] == 0.2


def test_comparison_refuses_different_workloads(tmp_path, monkeypatch, capsys):
    compare = load("compare_pipeline_load")
    paths = []
    for name, burst in (("before", 2000), ("after", 1000)):
        path = tmp_path / f"{name}.json"
        path.write_text(
            json.dumps(
                {
                    "run": name,
                    "workload": {"burst": burst},
                    "environment": {"packages": {}},
                    "pipeline": {"scenarios": [], "serialization": {}},
                }
            )
        )
        paths.append(str(path))
    monkeypatch.setattr(sys, "argv", ["compare", "--before", paths[0], "--after", paths[1]])
    assert compare.main() == 2
    assert "Workloads differ" in capsys.readouterr().out


def test_workload_records_the_fixed_scenarios_and_topology():
    phase = load("scalability_pipeline")
    args = SimpleNamespace(
        tenants=50,
        warmup=500,
        steady_rate=50,
        steady_seconds=60,
        burst=2000,
        burst_concurrency=50,
        partitions=8,
        processors=4,
        indexers=4,
        correlators=4,
        features=False,
    )
    workload = phase.workload(args)
    assert workload["version"] == "pipeline-v1"
    assert workload["correlators"] == 0
    assert workload["scenarios"] == [
        "steady",
        "burst_spread",
        "burst_one_tenant",
        "burst_global_v1",
    ]


def test_undrained_scenario_is_kept_and_stops_the_sequence():
    phase = load("scalability_pipeline")
    runner = phase.PipelinePhase.__new__(phase.PipelinePhase)
    runner.args = SimpleNamespace(warmup=0, steady_rate=3, steady_seconds=60, burst=300)
    runner.correlation = True
    seen = []

    def scenario(definition, count, measure=True):
        seen.append(definition["name"])
        done = definition["name"] != "burst_spread"
        return {
            "name": definition["name"],
            "offered": count,
            "accepted": count,
            "drained_seconds": {"watermark": 1.0} if done else {},
            "drain_timed_out": not done,
            "workers": {},
            "kafka_source_results_writes": {"hot_update_ratio": 0.0, "wal_bytes_per_alert": 1},
            "stages": {
                "processor": {"complete": True, "rejected": 0, "per_second": 5.0},
                "correlation": {"complete": done, "per_second": 2.0},
                "search": {"complete": True},
            },
        }

    runner.scenario = scenario
    result = runner.execute()
    assert seen == ["steady", "burst_spread"]
    assert result["stopped_after"] == "burst_spread"
    assert result["correctness_passed"] is False
    assert result["serialization"]["one_tenant_vs_spread"] is None


def test_undrained_warmup_stops_before_any_measured_scenario():
    phase = load("scalability_pipeline")
    runner = phase.PipelinePhase.__new__(phase.PipelinePhase)
    runner.args = SimpleNamespace(warmup=100, steady_rate=3, steady_seconds=60, burst=300)
    seen = []

    def scenario(definition, count, measure=True):
        seen.append(definition["name"])
        return {
            "name": definition["name"],
            "offered": count,
            "accepted": count,
            "drained_seconds": {},
            "drain_timed_out": True,
            "drain_counts": {"processor": 80},
        }

    runner.scenario = scenario
    result = runner.execute()
    assert seen == ["warmup"]
    assert result["stopped_after"] == "warmup"
    assert result["correctness_passed"] is False
    assert result["scenarios"] == []


def comparison_result(path, name, *, valid=True, scenarios=("steady",), commit="abcdef0123"):
    path.write_text(
        json.dumps(
            {
                "run": name,
                "workload": {"version": "pipeline-v1"},
                "environment": {"packages": {"keep-domain": {"commit": commit}}},
                "pipeline": {
                    "correctness_passed": valid,
                    "scenarios": [
                        {
                            "name": scenario,
                            "drain_timed_out": not valid,
                            "stages": {"processor": {"per_second": 5.0}},
                        }
                        for scenario in scenarios
                    ],
                    "serialization": {},
                },
            }
        )
    )
    return str(path)


def test_comparison_refuses_invalid_runs_unless_allowed(tmp_path, monkeypatch, capsys):
    compare = load("compare_pipeline_load")
    before = comparison_result(tmp_path / "before.json", "before")
    after = comparison_result(tmp_path / "after.json", "after", valid=False)
    monkeypatch.setattr(sys, "argv", ["compare", "--before", before, "--after", after])
    assert compare.main() == 2
    output = capsys.readouterr().out
    assert "correctness_passed is false" in output and "steady did not drain" in output
    monkeypatch.setattr(
        sys, "argv", ["compare", "--before", before, "--after", after, "--allow-invalid"]
    )
    assert compare.main() == 0


def test_comparison_lists_every_scenario_and_tolerates_missing_commits(
    tmp_path, monkeypatch, capsys
):
    compare = load("compare_pipeline_load")
    before = comparison_result(tmp_path / "before.json", "before", commit=None)
    after = comparison_result(
        tmp_path / "after.json", "after", scenarios=("steady", "burst_spread")
    )
    monkeypatch.setattr(sys, "argv", ["compare", "--before", before, "--after", after])
    assert compare.main() == 0
    output = capsys.readouterr().out
    assert "domain@?" in output
    assert "### steady" in output and "### burst_spread" in output


def test_editable_provenance_parses_file_urls_and_rejects_failed_git(tmp_path, monkeypatch):
    phase = load("scalability_pipeline")
    git_revision, seen = phase.git_revision, []
    monkeypatch.setattr(phase, "git_revision", lambda path: seen.append(path) or {"commit": "c"})

    class Distribution:
        version = "0.1.0"

        def read_text(self, name):
            return json.dumps(
                {"url": (tmp_path / "my repo").as_uri(), "dir_info": {"editable": True}}
            )

    monkeypatch.setattr(phase.importlib.metadata, "distribution", lambda name: Distribution())
    packages = phase.package_provenance()
    assert Path(seen[0]) == tmp_path / "my repo"
    assert packages["keep-domain"] == {"version": "0.1.0", "editable": True, "commit": "c"}
    assert git_revision(str(tmp_path / "not-a-checkout")) == {
        "commit": None,
        "dirty": None,
    }
