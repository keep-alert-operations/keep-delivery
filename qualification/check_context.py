"""Validate the repository-native context layer with the shared CLI implementation."""
import ast
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from context import headings, main  # noqa: E402

# Constructs where a second heading parser is most likely to disagree.
PARSER_SAMPLE = (
    "# Title\n```\n# fenced\n```\n~~~~\n## also fenced\n~~~~\n    # indented code\n"
    "Setext one\n===\nSetext two\n---\n## Closed ##\n#\n###### Six\n####### Seven\n"
)


def design_parser_errors(workspace):
    """Design CI mirrors the resolver's heading parser; both must read identical titles."""
    script = workspace / "appchi-design" / "scripts" / "check_docs.py"
    # check_docs.py runs its checks at import time, so compile only the mirrored function.
    tree = ast.parse(script.read_text(encoding="utf-8"))
    function = next((node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == "heading_titles"), None)
    if function is None:
        return [f"{script} has no heading_titles; pin a design revision that checks catalog sections"]
    module = ast.fix_missing_locations(ast.Module([ast.Import([ast.alias("re")]), function], []))
    namespace = {}
    exec(compile(module, str(script), "exec"), namespace)
    documents = [("parser sample", PARSER_SAMPLE)] + [
        (path.relative_to(workspace).as_posix(), path.read_text(encoding="utf-8-sig"))
        for path in sorted((workspace / "appchi-design").rglob("*.md")) if ".git" not in path.parts
    ]
    errors = []
    for name, text in documents:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        expected = [heading["title"] for heading in headings(text.splitlines(keepends=True))]
        if namespace["heading_titles"](text.splitlines()) != expected:
            errors.append(f"{name}: design check_docs.py reads headings differently from context.py")
    return errors


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    # Preserve top-level --workspace support before inserting the check subcommand.
    arguments = sys.argv[1:]
    workspace = []
    if "--workspace" in arguments:
        index = arguments.index("--workspace")
        workspace = arguments[index:index + 2]
        del arguments[index:index + 2]
    status = main([*workspace, "check", *arguments])
    if status == 0:
        root = Path(workspace[1]).resolve() if workspace else Path(__file__).resolve().parents[2]
        errors = design_parser_errors(root)
        print(json.dumps({"design_heading_parser": errors or "matches context.py"}, indent=2))
        status = 1 if errors else 0
    raise SystemExit(status)
