"""Context selection must preserve requirements without loading unrelated domains."""
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


def topic(topic_id, *, aliases=(), requires=(), sources=(), paths=(), related=(), orientation=(), evidence=()):
    return {"id": topic_id, "area": "Product", "title": topic_id.title(), "covers": f"Everything about {topic_id}.",
            "ask_like": [f"How does {topic_id} work?", f"Change {topic_id}"], "aliases": list(aliases),
            "path_globs": list(paths), "requires": list(requires), "orientation": list(orientation),
            "sources": list(sources), "evidence": list(evidence), "related": list(related),
            "repositories": ["appchi-design"], "checks": ["Run applicable repository checks"]}


def intent(intent_id, cues, *, override=False, show=("code", "related", "checks"), sources=()):
    return {"id": intent_id, "title": f"{intent_id} work", "override": override, "cues": list(cues),
            "show": list(show), "sources": list(sources), "steps": [f"Do the {intent_id} steps."],
            "finish": f"{intent_id} finished.", "skills": []}


@pytest.fixture
def workspace(tmp_path):
    directory = tmp_path / "appchi-design"
    (directory / "context").mkdir(parents=True)
    (directory / "AGENTS.md").write_text("# Instructions\n", encoding="utf-8")
    (directory / "spec.md").write_text(
        "# Specification\n\nLater correction: no timer release.\n\n"
        "## Required\nRequired rule.\n\n### Child\nChild rule.\n\n"
        "## Unrelated\nDo not load this.\n\n## Déjà: next!\nUnicode rule.\n\n"
        "## Scheduler cadence\nClock protection rule.\n",
        encoding="utf-8")
    (directory / "overview.md").write_text("# Overview\nServices and data flow.\n", encoding="utf-8")
    agents = tmp_path / "appchi-delivery/workspace/AGENTS.md"
    agents.parent.mkdir(parents=True)
    agents.write_text(f"# Workspace\n\nBefore.\n\n{context.INDEX_BEGIN}\n{context.INDEX_END}\n\nAfter.\n",
                      encoding="utf-8")
    data = {
        "schema_version": 2,
        "default_intent": "question",
        "areas": ["Product"],
        "intents": [intent("question", ["how", "what", "?"], show=("orientation", "code", "evidence", "related")),
                    intent("change", ["add", "change", "improve"]),
                    intent("review", ["review"], override=True),
                    intent("investigate", ["error", "stopped"], override=True, sources=["baseline"])],
        "scope_sections": {},
        "find_roots": [{"path": "appchi-design", "authority": "normative"}],
        "sources": {
            "baseline": {"path": "appchi-design/spec.md", "authority": "scope",
                         "sections": ["Required"], "include_preamble": True},
            "detail": {"path": "appchi-design/spec.md", "authority": "normative",
                       "sections": ["Déjà: next!"], "include_preamble": True},
            "overview": {"path": "appchi-design/overview.md", "authority": "normative",
                         "sections": [], "include_preamble": True},
        },
        "topics": [topic("foundation", sources=["baseline"], orientation=["overview"]),
                   topic("incident", aliases=["incident", "correlation"], requires=["foundation"],
                         sources=["detail"], paths=["appchi-api/incidents/*"],
                         related=[{"when": "Schema changes", "topic": "database"}]),
                   topic("database", aliases=["database"], requires=["foundation"]),
                   topic("ui", aliases=["spacing", "ui"], requires=["foundation"]),
                   topic("search", aliases=["search", "count"], requires=["foundation"]),
                   topic("live", aliases=["live", "stream"], requires=["foundation"])],
    }
    save(tmp_path, data)
    evaluations = {"schema_version": 2, "cases": [
        {"id": f"incident-{i}", "task": "Edit incidents", "paths": [], "topics": [],
         "must_include_topics": ["foundation", "incident"], "must_exclude_topics": ["database"],
         "must_include_sources": ["baseline", "detail"]} for i in range(20)]}
    (directory / "context/evaluation-cases.json").write_text(json.dumps(evaluations), encoding="utf-8")
    return tmp_path, data


def save(root, data):
    (root / context.CATALOG).write_text(json.dumps(data), encoding="utf-8")


def ids(items):
    return [item["id"] for item in items]


def test_plan_required_closure_deduplicates_and_keeps_related_topic_unselected(workspace):
    root, _ = workspace
    plan = context.Context(root).plan(topics=["incident", "ui"], task="incident", intent="change")
    assert ids(plan["topics"]) == ["foundation", "incident", "ui"]
    assert [(s["id"], s["role"]) for s in plan["sources"]] == [("detail", "authority"), ("baseline", "background")]
    assert plan["related"][0]["topic"] == "database"
    assert not plan["related"][0]["already_selected"]
    assert plan["status"] == "candidate"
    assert "does not establish completeness" in plan["guidance"]


def test_matching_uses_word_boundaries_stems_and_normalized_paths(workspace):
    root, _ = workspace
    catalog = context.Context(root)
    assert catalog.plan(task="build incidental helpers")["status"] == "unresolved"
    assert ids(catalog.plan(task="Improve UI spacing")["topics"]) == ["foundation", "ui"]
    assert "incident" in ids(catalog.plan(task="How are alerts correlated?")["topics"])
    plan = catalog.plan(paths=[r"appchi-api\incidents\routes.py"])
    assert ids(plan["topics"]) == ["foundation", "incident"]
    assert "Path:" in plan["topics"][1]["reasons"][0]


def test_stems_join_plural_and_verb_forms_but_never_prefixes():
    assert context.stem("correlated") == context.stem("correlation") == context.stem("correlate")
    assert context.stem("processing") == context.stem("processor") == context.stem("processes")
    assert context.stem("incidents") == "incident" != context.stem("incidental")
    assert context.stem("status") == "status" and context.stem("process") == "process"


def test_automatic_selection_is_capped_and_paths_always_win(workspace):
    root, _ = workspace
    catalog = context.Context(root)
    plan = catalog.plan(task="incident database ui search live")
    automatic = [t for t in plan["topics"] if "Required" not in t["reasons"][0]]
    assert len(automatic) == context.AUTO_TOPIC_LIMIT
    assert ids(plan["suggestions"]) == ["search", "live"]
    plan = catalog.plan(task="database ui search live", paths=["appchi-api/incidents/x.py"])
    assert "incident" in ids(plan["topics"])


def test_explicit_topics_keep_other_automatic_candidates_as_suggestions(workspace):
    root, _ = workspace
    plan = context.Context(root).plan(topics=["ui"], task="incident UI spacing")
    assert ids(plan["topics"]) == ["foundation", "ui"]
    assert ids(plan["suggestions"]) == ["incident"]


@pytest.mark.parametrize("task, expected, reason", [
    ("How does incident search work?", "question", "first cue 'how'"),
    ("Add an incident field", "change", "first cue 'add'"),
    ("Incident tags?", "question", "first cue '?'"),
    ("How do I add a field? Review it", "review", "cue 'review'"),
    ("Live updates stopped after reconnect", "investigate", "cue 'stopped'"),
    ("incident tags", "question", "no intent cue; default intent"),
    ("", "question", "no request text; default intent"),
])
def test_intent_inference_prefers_override_cues_then_earliest_cue(workspace, task, expected, reason):
    root, _ = workspace
    assert context.Context(root).infer_intent(task) == (expected, reason)


def test_intent_controls_orientation_extra_sources_steps_and_finish(workspace):
    root, _ = workspace
    catalog = context.Context(root)
    question = catalog.plan(topics=["incident"], intent="question")
    assert [(s["id"], s["role"]) for s in question["sources"]] == [
        ("overview", "orientation"), ("detail", "authority"), ("baseline", "background")]
    assert question["intent"]["finish"] == "question finished."
    change = catalog.plan(topics=["incident"], intent="change")
    assert ids(change["sources"]) == ["detail", "baseline"]
    investigate = catalog.plan(topics=["ui"], task="UI error", intent=None)
    assert investigate["intent"]["id"] == "investigate"
    assert investigate["sources"][0]["role"] == "intent"
    assert investigate["sources"][0]["required_by"] == ["intent:investigate", "foundation"]
    with pytest.raises(context.ContextError, match="Unknown intent"):
        catalog.plan(topics=["ui"], intent="guess")


def test_unresolved_plans_offer_find_candidates_instead_of_nothing(workspace):
    root, _ = workspace
    plan = context.Context(root).plan(task="scheduler cadence")
    assert plan["status"] == "unresolved" and plan["topics"] == []
    best = plan["candidates"][0]
    assert (best["path"], best["title"], best["authority"], best["topics"]) == (
        "appchi-design/spec.md", "Scheduler cadence", "normative", [])
    assert context.Context(root).plan(task="zebra xylophone")["candidates"] == []


def test_find_ranks_title_matches_and_labels_cited_headings_with_their_source(workspace):
    root, _ = workspace
    results = context.Context(root).find("required rule")
    best = results[0]
    assert (best["title"], best["authority"], best["topics"]) == ("Required", "scope", ["foundation"])
    assert all(best["score"] >= r["score"] for r in results)


def test_content_option_matches_read_output(workspace):
    root, _ = workspace
    catalog = context.Context(root)
    plan = catalog.plan(topics=["incident"], intent="change", content=True)
    assert [s["content"] for s in plan["sources"]] == [catalog.source(s)["content"] for s in ("detail", "baseline")]
    assert "content" not in catalog.plan(topics=["incident"], intent="change")["sources"][0]
    assert "## Source: baseline" in context.plan_markdown(plan)
    assert "## Verification checks" in context.plan_markdown(plan)


def test_scope_sections_join_every_partial_read_of_their_document(workspace):
    root, data = workspace
    data["scope_sections"] = {"appchi-design/spec.md": ["Scheduler cadence"]}
    save(root, data)
    source = context.Context(root).source("detail")
    assert "Clock protection rule." in source["content"]
    assert [(s["title"], s["scope"]) for s in source["sections"]] == [("Déjà: next!", False),
                                                                      ("Scheduler cadence", True)]
    data["scope_sections"] = {"appchi-design/spec.md": ["Missing"]}
    save(root, data)
    with pytest.raises(context.ContextError, match="found 0"):
        context.Context(root).source("detail")


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
    data["scope_sections"] = {"appchi-design/spec.md": ["Scheduler cadence"]}
    save(root, data)
    assert context.Context(root).source("baseline")["content"] == (root / "appchi-design/spec.md").read_text(encoding="utf-8")


def test_source_hash_changes_with_current_bytes_even_on_same_context_instance(workspace):
    root, _ = workspace
    catalog = context.Context(root)
    before = catalog.plan(topics=["incident"], intent="change")["sources"][0]["sha256"]
    path = root / "appchi-design/spec.md"
    path.write_text(path.read_text(encoding="utf-8") + "Changed source bytes.\n", encoding="utf-8")
    after = catalog.plan(topics=["incident"], intent="change")["sources"][0]["sha256"]
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
    data["topics"][2]["path_globs"] = ["*/pyproject.toml"]
    save(root, data)
    catalog = context.Context(root)
    assert ids(catalog.plan(paths=["appchi-api/pyproject.toml"])["topics"]) == ["foundation", "database"]
    with pytest.raises(context.ContextError, match="declared Appchi"):
        catalog.plan(paths=["keepHQ/pyproject.toml"])
    data["topics"][2]["path_globs"] = ["*/../private/*"]
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


def test_unknown_topic_and_source_are_errors(workspace):
    root, _ = workspace
    catalog = context.Context(root)
    with pytest.raises(context.ContextError, match="Unknown topic"):
        catalog.plan(topics=["missing"])
    with pytest.raises(context.ContextError, match="Unknown source"):
        catalog.source("missing")


@pytest.mark.parametrize("mutation, expected", [
    (lambda data: data["topics"][0]["requires"].append("incident"), "cycle"),
    (lambda data: data["topics"][0]["requires"].append("missing"), "unknown topic"),
    (lambda data: data["topics"][0]["sources"].append("missing"), "unknown source"),
    (lambda data: data["topics"][0]["orientation"].append("missing"), "unknown source"),
    (lambda data: data["topics"][0]["repositories"].append("keepHQ"), "undeclared repository"),
    (lambda data: data["topics"][0].update(area="Elsewhere"), "undeclared area"),
    (lambda data: data["topics"][0].update(covers="x" * 161), "covers"),
    (lambda data: data["topics"][0].update(ask_like=["Only one"]), "ask_like"),
    (lambda data: data["topics"][0].update(aliases=["the and"]), "matchable"),
    (lambda data: data["sources"]["baseline"].update(include_preamble=False), "include_preamble"),
    (lambda data: data["sources"]["baseline"].update(authority="guess"), "authority"),
    (lambda data: data["sources"]["baseline"].update(authority=[]), "authority"),
    (lambda data: data.update(schema_version=1), "schema_version"),
    (lambda data: data.update(schema_version=True), "schema_version"),
    (lambda data: data["topics"].append(copy.deepcopy(data["topics"][0])), "Duplicate topic"),
    (lambda data: data["topics"][0]["related"].append({"when": "Maybe", "topic": "missing"}), "unknown topic"),
    (lambda data: data.update(default_intent="missing"), "default_intent"),
    (lambda data: data["intents"][0].update(show=["everything"]), "unknown fields"),
    (lambda data: data["intents"][0].update(sources=["missing"]), "unknown source"),
    (lambda data: data["intents"][0].update(steps=[]), "steps"),
    (lambda data: data["intents"].append(copy.deepcopy(data["intents"][0])), "Duplicate intent"),
    (lambda data: data.update(scope_sections={"keepHQ/spec.md": ["Required"]}), "declared Appchi"),
    (lambda data: data.update(find_roots=[]), "find_roots"),
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
        context.Context(root).plan(topics=["incident"])


def test_generated_views_and_index_are_deterministic_linked_and_checked_for_drift(workspace):
    root, _ = workspace
    catalog = context.Context(root)
    with pytest.raises(context.ContextError, match="drift"):
        catalog.generated()
    catalog.generated(write=True)
    pages = list((root / "appchi-design/context").rglob("*.md")) + [root / context.INDEX_TARGET]
    first = {p: p.read_bytes() for p in pages}
    catalog.generated(write=True)
    assert first == {p: p.read_bytes() for p in first}
    text = (root / "appchi-design/context/topics/incident.md").read_text(encoding="utf-8")
    assert "[Foundation](foundation.md)" in text
    assert "Schema changes [Database](database.md)" in text
    assert "../.." in text and "#d%C3%A9j%C3%A0-next" in text
    agents = (root / context.INDEX_TARGET).read_text(encoding="utf-8")
    assert agents.startswith("# Workspace\n\nBefore.\n") and agents.endswith("\nAfter.\n")
    assert "| Product | `incident` | Everything about incident. |" in agents
    assert "`question` (question work)" in agents
    assert catalog.check()["evaluation_cases"] == 20
    target = root / "appchi-design/context/map.md"
    target.write_text(target.read_text() + "Edited view", encoding="utf-8")
    with pytest.raises(context.ContextError, match="drift"):
        catalog.generated()


def test_index_needs_markers_and_stays_within_its_line_limit(workspace):
    root, data = workspace
    agents = root / context.INDEX_TARGET
    original = agents.read_text(encoding="utf-8")
    agents.write_text("# Workspace without markers\n", encoding="utf-8")
    with pytest.raises(context.ContextError, match="markers"):
        context.Context(root).generated(write=True)
    agents.write_text(original, encoding="utf-8")
    data["topics"] += [topic(f"extra-{n}") for n in range(context.INDEX_LINE_LIMIT)]
    save(root, data)
    with pytest.raises(context.ContextError, match="within 40"):
        context.Context(root).generated(write=True)


def test_render_refuses_unowned_files_before_any_writes(workspace):
    root, _ = workspace
    target = root / "appchi-design/context/map.md"
    target.write_text("My manually maintained map", encoding="utf-8")
    with pytest.raises(context.ContextError, match="unowned"):
        context.Context(root).generated(write=True)
    assert not (root / "appchi-design/context/topics").exists()
    assert target.read_text() == "My manually maintained map"


def test_stale_generated_pages_need_explicit_review_not_deletion(workspace):
    root, _ = workspace
    catalog = context.Context(root)
    catalog.generated(write=True)
    for folder in ("topics", "routes"):
        stale = root / f"appchi-design/context/{folder}/old.md"
        stale.parent.mkdir(exist_ok=True)
        stale.write_text(context.GENERATED + "\nOld page", encoding="utf-8")
        with pytest.raises(context.ContextError, match="Stale generated page"):
            catalog.generated(write=True)
        assert stale.exists()
        stale.unlink()


def test_routing_evaluations_enforce_negative_and_intent_expectations(workspace):
    root, _ = workspace
    path = root / context.EVALUATIONS
    data = json.loads(path.read_text())
    data["cases"][0]["must_exclude_topics"] = ["incident"]
    data["cases"][1]["expect_intent"] = "review"
    data["cases"][2].update(task="scheduler cadence", must_include_topics=[], must_include_sources=[],
                            expect_status="unresolved", must_find=["spec.md#Nowhere"])
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(context.ContextError) as error:
        context.Context(root).evaluations()
    message = str(error.value)
    assert "forbidden topics: incident" in message
    assert "intent question, expected review" in message
    assert "no find candidate matches 'spec.md#Nowhere'" in message


def test_cli_unmatched_is_explicit_and_nonzero(workspace, capsys):
    root, _ = workspace
    assert context.main(["--workspace", str(root), "plan", "--task", "unclassified task", "--format", "json"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "unresolved"
    assert output["unresolved"]
    assert output["sources"] == []


def test_cli_route_alias_find_and_list(workspace, capsys):
    root, _ = workspace
    assert context.main(["--workspace", str(root), "plan", "--route", "ui", "--format", "json"]) == 0
    assert ids(json.loads(capsys.readouterr().out)["topics"]) == ["foundation", "ui"]
    assert context.main(["--workspace", str(root), "find", "scheduler", "cadence"]) == 0
    assert "Scheduler cadence" in capsys.readouterr().out
    assert context.main(["--workspace", str(root), "find", "zebra"]) == 2
    capsys.readouterr()
    assert context.main(["--workspace", str(root), "list"]) == 0
    listing = capsys.readouterr().out
    assert "question: question work" in listing and "incident: Everything about incident." in listing


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
        [sys.executable, 'scripts/context.py', 'plan', '--topic', 'incident', '--intent', 'change'],
        cwd=delivery, capture_output=True, text=True, encoding='utf-8', check=True,
    )
    assert "## What to do" in plan.stdout and "Finish line: change finished." in plan.stdout
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
