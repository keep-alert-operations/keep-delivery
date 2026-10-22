"""Compare pipeline-phase results of scalability_load.py across package revisions.

Pass one or more result files per side; each metric reports the median of its
side. Runs are comparable only when their recorded workloads match.

    python qualification/compare_pipeline_load.py --before a.json b.json --after c.json d.json
"""

import argparse
import json
from pathlib import Path
from statistics import median

# (label, path inside a scenario result, whether larger is better)
METRICS = (
    ("processor alerts/s", ("stages", "processor", "per_second"), True),
    ("processor p50 ms", ("stages", "processor", "latency_ms", "p50"), False),
    ("processor p99 ms", ("stages", "processor", "latency_ms", "p99"), False),
    ("correlation alerts/s", ("stages", "correlation", "per_second"), True),
    ("correlation p99 ms", ("stages", "correlation", "latency_ms", "p99"), False),
    ("search drained s", ("stages", "search", "drained_seconds"), False),
    ("search visible p99 ms", ("stages", "search", "visibility_sample_ms", "p99"), False),
    ("admission p99 ms", ("admission_ms", "p99"), False),
    ("source records/consume", ("workers", "source", "derived", "records_per_consume"), True),
    ("source records/publish", ("workers", "source", "derived", "records_per_publish"), True),
    ("source apply ms/record", ("workers", "source", "derived", "apply_ms_per_record"), False),
    (
        "source kafka txn ms/record",
        ("workers", "source", "derived", "kafka_transaction_ms_per_record"),
        False,
    ),
    (
        "confirmation ms/record",
        ("workers", "source", "derived", "confirmation_ms_per_record"),
        False,
    ),
    (
        "confirmation share of turns",
        ("workers", "source", "derived", "confirmation_share_of_active_turns"),
        False,
    ),
    (
        "indexer records/handler call",
        ("workers", "indexer", "derived", "records_per_handler_call"),
        True,
    ),
    (
        "indexer commits/record",
        ("workers", "indexer", "derived", "kafka_commits_per_record"),
        False,
    ),
    (
        "correlation handler ms/record",
        ("workers", "correlation", "derived", "handler_ms_per_record"),
        False,
    ),
    (
        "correlation commits/record",
        ("workers", "correlation", "derived", "kafka_commits_per_record"),
        False,
    ),
    ("WAL bytes/alert", ("kafka_source_results_writes", "wal_bytes_per_alert"), False),
    ("source result HOT ratio", ("kafka_source_results_writes", "hot_update_ratio"), True),
    ("lock-waiting backends", ("database", "lock_waiting_backends_mean"), False),
    ("retried inputs (faults)", ("faults",), False),
)


def lookup(value, path):
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def load(paths):
    results = [json.loads(Path(path).read_text()) for path in paths]
    for path, result in zip(paths, results):
        if "pipeline" not in result:
            raise SystemExit(f"{path} has no pipeline-phase result")
    return results


def side(results, scenario, path):
    values = []
    for result in results:
        match = next((s for s in result["pipeline"]["scenarios"] if s["name"] == scenario), None)
        value = lookup(match, path) if match else None
        if isinstance(value, dict):
            value = sum(value.values())
        if isinstance(value, (int, float)):
            values.append(value)
    return median(values) if values else None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--before", nargs="+", required=True)
    parser.add_argument("--after", nargs="+", required=True)
    parser.add_argument("--allow-workload-mismatch", action="store_true")
    args = parser.parse_args()
    before, after = load(args.before), load(args.after)
    workloads = {json.dumps(r.get("workload"), sort_keys=True) for r in before + after}
    if len(workloads) != 1:
        print("Workloads differ:")
        for path, result in zip(args.before + args.after, before + after):
            print(f"  {path}: {json.dumps(result.get('workload'), sort_keys=True)}")
        if not args.allow_workload_mismatch:
            return 2
    for label, results in (("before", before), ("after", after)):
        for result in results:
            print(
                f"{label}: run {result['run']} passed={result.get('passed')} "
                f"clean={result.get('clean')} packages="
                + ", ".join(
                    f"{name.removeprefix('appchi-')}@{(info or {}).get('commit', '?')[:8]}"
                    + ("+dirty" if (info or {}).get("dirty") else "")
                    for name, info in result["environment"]["packages"].items()
                )
            )
    scenarios = [s["name"] for s in before[0]["pipeline"]["scenarios"]]
    for scenario in scenarios:
        print(f"\n### {scenario}\n")
        print("| metric | before | after | change |")
        print("|---|---:|---:|---:|")
        for label, path, larger_better in METRICS:
            old, new = side(before, scenario, path), side(after, scenario, path)
            if old is None and new is None:
                continue
            change = ""
            if old not in (None, 0) and new is not None:
                delta = (new - old) / abs(old) * 100
                better = (delta > 0) == larger_better
                change = f"{delta:+.1f}%" + (" better" if better and delta else "")
            print(f"| {label} | {fmt(old)} | {fmt(new)} | {change} |")
    print("\n### Per-tenant serialization (processor alerts/s ratio to burst_spread)\n")
    for label, results in (("before", before), ("after", after)):
        ratios = [r["pipeline"]["serialization"] for r in results]
        print(
            f"- {label}: one tenant {fmt(median_of(ratios, 'one_tenant_vs_spread'))}, "
            f"Global v1 {fmt(median_of(ratios, 'global_v1_vs_spread'))}"
        )
    return 0


def median_of(items, key):
    values = [item[key] for item in items if isinstance(item.get(key), (int, float))]
    return median(values) if values else None


def fmt(value):
    if value is None:
        return "-"
    return f"{value:,.3f}".rstrip("0").rstrip(".") if isinstance(value, float) else f"{value:,}"


if __name__ == "__main__":
    raise SystemExit(main())
