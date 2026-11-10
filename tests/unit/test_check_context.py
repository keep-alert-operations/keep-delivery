"""The context gate must catch a design heading parser that drifts from the resolver."""
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "qualification" / "check_context.py"
SPEC = importlib.util.spec_from_file_location("context_gate", SCRIPT)
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


def design(tmp_path, source):
    scripts = tmp_path / "keep-design" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "check_docs.py").write_text(source, encoding="utf-8")
    (tmp_path / "keep-design" / "spec.md").write_text("# Spec\n\n## Section\n", encoding="utf-8")
    return tmp_path


def test_missing_mirrored_parser_is_an_error(tmp_path):
    root = design(tmp_path, "raise SystemExit('checks ran at import')\n")
    assert "no heading_titles" in GATE.design_parser_errors(root)[0]


def test_parser_that_ignores_fences_is_caught_without_running_the_script(tmp_path):
    naive = (
        "raise SystemExit('checks ran at import')\n"
        "def heading_titles(lines):\n"
        "    return [m[1] for line in lines if (m := re.match(r'^#{1,6} (.*)$', line))]\n"
    )
    errors = GATE.design_parser_errors(design(tmp_path, naive))
    assert errors == ["parser sample: design check_docs.py reads headings differently from context.py"]
