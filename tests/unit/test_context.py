"""Context routing must preserve requirements without loading unrelated domains."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/context.py"
SPEC = importlib.util.spec_from_file_location("appchi_context", SCRIPT)
context = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(context)


def route(route_id, *, keywords=(), requires=(), sources=(), paths=(), expand=()):
    return {"id": route_id, "title": route_id.title(), "when": [f"Working on {route_id}"],
            "keywords": list(keywords), "path_globs": list(paths), "requires": list(requires),
            "sources": list(sources), "repositories": ["appchi-design"],
            "expand": list(expand), "checks": ["Run applicable repository checks"]}


@pytest.fixture
def workspace(tmp_path):
    directory = tmp_path / "appchi-design"
    (directory / "context").mkdir(parents=True)
    (directory / "AGENTS.md").write_text("# Instructions\n", encoding="utf-8")
    (directory / "spec.md").write_text(
        "# Specification\n\nLater correction: no timer release.\n\n"
        "## Required\nRequired rule.\n\n### Child\nChild rule.\n\n"
        "## Unrelated\nDo not load this.\n\n## Déjà: next!\nUnicode rule.\n",
        encoding="utf-8")
    data = {
        "schema_version": 1,
        "sources": {
            "baseline": {"path": "appchi-design/spec.md", "authority": "scope",
                         "sections": ["Required"], "include_preamble": True},
            "detail": {"path": "appchi-design/spec.md", "authority": "normative",
                       "sections": ["Déjà: next!"], "include_preamble": True},
        },
        "routes": [route("foundation", sources=["baseline"]),
                   route("incident", keywords=["incident", "incidents"],
                         requires=["foundation"], sources=["detail"],
                         paths=["appchi-api/incidents/*"],
                         expand=[{"when": "Schema changes", "route": "database"}]),
                   route("database", keywords=["database"], requires=["foundation"]),
                   route("ui", keywords=["spacing", "ui"], requires=["foundation"])],
    }
    save(tmp_path, data)
    evaluations = {"schema_version": 1, "cases": [
        {"id": f"incident-{i}", "task": "Edit incidents", "paths": [], "routes": [],
         "must_include_routes": ["foundation", "incident"], "must_exclude_routes": ["database"],
         "must_include_sources": ["baseline", "detail"]} for i in range(20)]}
    (directory / "context/evaluation-cases.json").write_text(json.dumps(evaluations), encoding="utf-8")
    return tmp_path, data


def save(root, data):
    (root / context.CATALOG).write_text(json.dumps(data), encoding="utf-8")


def test_plan_required_closure_deduplicates_and_keeps_conditional_route_unselected(workspace):
    root, _ = workspace
    plan = context.Context(root).plan(routes=["incident", "ui"], task="incident")
    assert [r["id"] for r in plan["routes"]] == ["foundation", "incident", "ui"]
    assert [s["id"] for s in plan["sources"]] == ["baseline", "detail"]
    assert plan["conditional_expansions"][0]["route"] == "database"
    assert not plan["conditional_expansions"][0]["already_selected"]
    assert plan["status"] == "candidate"
    assert "does not establish completeness" in plan["guidance"]


def test_matching_uses_phrase_boundaries_and_normalized_paths(workspace):
    root, _ = workspace
    catalog = context.Context(root)
    assert catalog.plan(task="build incidental helpers")["status"] == "unresolved"
    assert [r["id"] for r in catalog.plan(task="Improve UI spacing")["routes"]] == ["foundation", "ui"]
    plan = catalog.plan(paths=[r"appchi-api\incidents\routes.py"])
    assert [r["id"] for r in plan["routes"]] == ["foundation", "incident"]
    assert "Path:" in plan["routes"][1]["reasons"][0]


def test_explicit_routes_keep_other_automatic_candidates_as_suggestions(workspace):
    root, _ = workspace
    plan = context.Context(root).plan(routes=["ui"], task="incident UI spacing")
    assert [r["id"] for r in plan["routes"]] == ["foundation", "ui"]
    assert [r["id"] for r in plan["suggestions"]] == ["incident"]


def test_preamble_child_sections_and_overlap_are_preserved_once(workspace):
    root, data = workspace
    data["sources"]["baseline"]["sections"].append("Child")
    save(root, data)
    source = context.Context(root).source("baseline")
    assert "Later correction: no timer release." in source["content"]
    assert "Child rule." in source["content"]
    assert source["content"].count("Child rule.") == 1
    assert "Do not load this." not in source["content"]
    assert source["ranges"] == [[1, 10]]


def test_full_file_sources_do_not_omit_any_content(workspace):
    root, data = workspace
    data["sources"]["baseline"]["sections"] = []
    save(root, data)
    assert context.Context(root).source("baseline")["content"] == (root / "appchi-design/spec.md").read_text(encoding="utf-8")


def test_source_hash_changes_with_current_bytes_even_on_same_context_instance(workspace):
    root, _ = workspace
    catalog = context.Context(root)
    before = catalog.plan(routes=["incident"])["sources"][0]["sha256"]
    path = root / "appchi-design/spec.md"
    path.write_text(path.read_text(encoding="utf-8") + "Changed source bytes.\n", encoding="utf-8")
    after = catalog.plan(routes=["incident"])["sources"][0]["sha256"]
    assert before != after


def test_fenced_and_indented_headings_are_not_sections():
    lines = ("# Root\n\n```md\n## Fake\n````\n\n~~~\n## Also fake\n~~~\n"
             "    ## Indented\n## Déjà: next!\nActual\n## Repeat\n## Repeat\n"
             "Setext\n------\n").splitlines(keepends=True)
    result = context.headings(lines)
    assert [h["title"] for h in result] == ["Root", "Déjà: next!", "Repeat", "Repeat", "Setext"]
    assert [h["anchor"] for h in result] == ["root", "déjà-next", "repeat", "repeat-1", "setext"]


@pytest.mark.parametrize("path", ["../secret.md", "/secret.md", "C:/secret.md", "appchi-design/../secret.md",
                                  "appchi-design\\spec.md", "unknown/spec.md", "appchi-design/./spec.md"])
def test_rejects_unsafe_catalog_paths(workspace, path):
    root, data = workspace
    data["sources"]["baseline"]["path"] = path
    save(root, data)
    with pytest.raises(context.ContextError, match="path|Path"):
        context.Context(root)


def test_rejects_symlink_escape_before_reading(workspace, tmp_path):
    root, _ = workspace
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("Private content", encoding="utf-8")
    link = root / "appchi-design/link.md"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Host does not permit symlinks: {exc}")
    with pytest.raises(context.ContextError, match="escapes"):
        context.safe_path(root, "appchi-design/link.md")


def test_resolved_escape_guard_runs_without_host_symlink_privileges(workspace, monkeypatch):
    root, _ = workspace
    original = Path.resolve
    candidate = root / "appchi-design/link.md"

    def resolve(path, *args, **kwargs):
        if path == candidate:
            return root.parent / "outside.md"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(context.ContextError, match="escapes"):
        context.safe_path(root, "appchi-design/link.md")


def test_wildcard_repository_globs_remain_bounded_to_declared_repositories(workspace):
    root, data = workspace
    data["routes"][2]["path_globs"] = ["*/pyproject.toml"]
    save(root, data)
    catalog = context.Context(root)
    assert [r["id"] for r in catalog.plan(paths=["appchi-api/pyproject.toml"])["routes"]] == ["foundation", "database"]
    with pytest.raises(context.ContextError, match="declared Appchi"):
        catalog.plan(paths=["keepHQ/pyproject.toml"])
    data["routes"][2]["path_globs"] = ["*/../private/*"]
    save(root, data)
    with pytest.raises(context.ContextError, match="unsafe path glob"):
        context.Context(root)


def test_rejects_missing_and_ambiguous_headings(workspace):
    root, _ = workspace
    path = root / "appchi-design/spec.md"
    path.write_text("# Spec\n## Required\nA\n## Required\nB\n", encoding="utf-8")
    with pytest.raises(context.ContextError, match="found 2"):
        context.Context(root).source("baseline")
    path.write_text("# Spec\n## Other\nA\n", encoding="utf-8")
    with pytest.raises(context.ContextError, match="found 0"):
        context.Context(root).source("baseline")


def test_unknown_route_and_source_are_errors(workspace):
    root, _ = workspace
    catalog = context.Context(root)
    with pytest.raises(context.ContextError, match="Unknown route"):
        catalog.plan(routes=["missing"])
    with pytest.raises(context.ContextError, match="Unknown source"):
        catalog.source("missing")


@pytest.mark.parametrize("mutation, expected", [
    (lambda data: data["routes"][0]["requires"].append("incident"), "cycle"),
    (lambda data: data["routes"][0]["requires"].append("missing"), "unknown route"),
    (lambda data: data["routes"][0]["sources"].append("missing"), "unknown source"),
    (lambda data: data["routes"][0]["repositories"].append("keepHQ"), "undeclared repository"),
    (lambda data: data["sources"]["baseline"].update(include_preamble=False), "include_preamble"),
    (lambda data: data["sources"]["baseline"].update(authority="guess"), "authority"),
    (lambda data: data["sources"]["baseline"].update(authority=[]), "authority"),
    (lambda data: data.update(schema_version=True), "schema_version"),
    (lambda data: data["routes"].append(copy.deepcopy(data["routes"][0])), "Duplicate route"),
    (lambda data: data["routes"][0]["expand"].append({"when": "Maybe", "route": "missing"}), "unknown route"),
])
def test_catalog_validation_is_strict(workspace, mutation, expected):
    root, data = workspace
    mutation(data)
    save(root, data)
    with pytest.raises(context.ContextError, match=expected):
        context.Context(root)


def test_missing_source_reports_dependency_and_does_not_succeed_empty(workspace):
    root, _ = workspace
    (root / "appchi-design/spec.md").unlink()
    with pytest.raises(context.ContextError, match="Missing or non-regular"):
        context.Context(root).plan(routes=["incident"])


def test_generated_views_are_deterministic_linked_and_checked_for_drift(workspace):
    root, _ = workspace
    catalog = context.Context(root)
    with pytest.raises(context.ContextError, match="drift"):
        catalog.generated()
    catalog.generated(write=True)
    first = {p: p.read_bytes() for p in (root / "appchi-design/context").rglob("*.md")}
    catalog.generated(write=True)
    assert first == {p: p.read_bytes() for p in first}
    text = (root / "appchi-design/context/routes/incident.md").read_text(encoding="utf-8")
    assert "[Foundation](foundation.md)" in text
    assert "When Schema changes: [Database](database.md)" in text
    assert "../.." in text and "#d%C3%A9j%C3%A0-next" in text
    assert catalog.check()["evaluation_cases"] == 20
    target = root / "appchi-design/context/map.md"
    target.write_text(target.read_text() + "Edited view", encoding="utf-8")
    with pytest.raises(context.ContextError, match="drift"):
        catalog.generated()


def test_render_refuses_unowned_files_before_any_writes(workspace):
    root, _ = workspace
    target = root / "appchi-design/context/map.md"
    target.write_text("My manually maintained map", encoding="utf-8")
    with pytest.raises(context.ContextError, match="unowned"):
        context.Context(root).generated(write=True)
    assert not (root / "appchi-design/context/routes").exists()
    assert target.read_text() == "My manually maintained map"


def test_stale_generated_files_need_explicit_review_not_deletion(workspace):
    root, _ = workspace
    catalog = context.Context(root)
    catalog.generated(write=True)
    stale = root / "appchi-design/context/routes/old.md"
    stale.write_text(context.GENERATED + "\nOld route", encoding="utf-8")
    with pytest.raises(context.ContextError, match="Stale generated route"):
        catalog.generated(write=True)
    assert stale.exists()


def test_routing_evaluations_enforce_negative_expectations(workspace):
    root, _ = workspace
    path = root / context.EVALUATIONS
    data = json.loads(path.read_text())
    data["cases"][0]["must_exclude_routes"] = ["incident"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(context.ContextError, match="forbidden routes: incident"):
        context.Context(root).evaluations()


def test_cli_unmatched_is_explicit_and_nonzero(workspace, capsys):
    root, _ = workspace
    assert context.main(["--workspace", str(root), "plan", "--task", "unclassified task", "--format", "json"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "unresolved"
    assert output["unresolved"]
    assert output["sources"] == []


def test_cli_read_errors_nonzero_and_prints_provenance(workspace, capsys):
    root, _ = workspace
    assert context.main(["--workspace", str(root), "read", "--source", "baseline"]) == 0
    assert "SHA-256:" in capsys.readouterr().out
    assert context.main(["--workspace", str(root), "read", "--source", "missing"]) == 1
    assert "Unknown source" in capsys.readouterr().err


def test_plan_read_command_works_from_documented_delivery_directory(workspace):
    root, _ = workspace
    delivery = root / 'appchi-delivery'
    (delivery / 'scripts').mkdir(parents=True)
    (delivery / 'scripts/context.py').write_bytes(SCRIPT.read_bytes())
    plan = subprocess.run(
        [sys.executable, 'scripts/context.py', 'plan', '--route', 'incident'],
        cwd=delivery, capture_output=True, text=True, encoding='utf-8', check=True,
    )
    command = next(line.split('`')[1] for line in plan.stdout.splitlines() if '  - Read: `' in line)
    read = subprocess.run(
        [sys.executable, *command.split()[1:]], cwd=delivery,
        capture_output=True, text=True, encoding='utf-8', check=True,
    )
    assert 'Later correction: no timer release.' in read.stdout
    unicode_read = subprocess.run(
        [sys.executable, 'scripts/context.py', 'read', '--source', 'detail'], cwd=delivery,
        capture_output=True, text=True, encoding='utf-8', check=True,
    )
    assert 'Déjà: next!' in unicode_read.stdout
