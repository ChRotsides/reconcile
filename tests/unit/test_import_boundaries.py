"""Static inference/evaluator boundary (PLAN.md section 2; docs/isolation.md).

Checks the source of src/reconcile/ with `ast` and plain text. This is a guard
against accidents, not isolation: real isolation is the restricted mounts.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

# Names only evaluator code may use. Matched as plain text, so comments and
# docstrings count too.
FORBIDDEN_NAMES = (
    "rca_ground_truth",
    "failure_manifest",
    "causal_edges",
    "mutation_log",
    "challenge_test",
    "links.jsonl",
    "benchmark_questions",
)

# The only modules that may name a forbidden string, and why.
ALLOWED_NAMES = {
    # Reads the upstream questions file (keeps only case_id and primary_entity),
    # writes the evaluator-side links file, and names challenge_test because it
    # refuses that split.
    "reconcile/adapters/finrca_requests.py": {
        "benchmark_questions",
        "links.jsonl",
        "challenge_test",
    },
}

# Preparation adapters: handle key files and read upstream files. Inference
# modules (everything else) must not import them.
PREPARATION = {"reconcile.adapters.finrca", "reconcile.adapters.finrca_requests"}


def _modules(root: Path) -> list[Path]:
    return sorted((root / "reconcile").rglob("*.py"))


def _imports(path: Path, root: Path) -> set[str]:
    """Absolute dotted names a module may import, including `from x import y` as x.y."""
    package = path.relative_to(root).with_suffix("").parts[:-1]
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = list(package[: len(package) - node.level + 1]) if node.level else []
            if node.module:
                base += node.module.split(".")
            names.add(".".join(base))
            names.update(".".join([*base, a.name]) for a in node.names)
    return names


def violations(root: Path) -> list[str]:
    found: list[str] = []
    for path in _modules(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_NAMES:
            if name in text and name not in ALLOWED_NAMES.get(rel, set()):
                found.append(f"{rel}: names {name!r}")
        for imported in sorted(_imports(path, root)):
            if imported.split(".")[0] == "evaluation":
                found.append(f"{rel}: imports {imported}")
            is_prep = rel.removesuffix(".py").replace("/", ".") in PREPARATION
            if imported in PREPARATION and not is_prep:
                found.append(f"{rel}: inference module imports {imported}")
    return found


def test_preparation_modules_exist() -> None:
    # A rename must update PREPARATION, or the import check silently weakens.
    for name in PREPARATION:
        assert (SRC / (name.replace(".", "/") + ".py")).is_file()


def test_inference_code_respects_the_boundary() -> None:
    assert violations(SRC) == []


def test_checker_detects_violations(tmp_path: Path) -> None:
    pkg = tmp_path / "reconcile" / "domain"
    pkg.mkdir(parents=True)
    (pkg / "bad.py").write_text(
        "import evaluation.x\n"
        "from reconcile.adapters import finrca\n"
        "from ..adapters.finrca_requests import REQUESTS_FILE\n"
        "F = 'rca_ground_truth.jsonl'\n"
    )
    found = violations(tmp_path)
    assert len(found) == 4, found
