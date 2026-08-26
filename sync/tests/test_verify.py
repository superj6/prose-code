from prosesync.config import load_config
from prosesync.verify import get_verifiers, run_verifiers
from prosesync.verify.python_ast import PythonAstVerifier
from prosesync.verify.treesitter import TreeSitterVerifier


def test_treesitter_flags_errors_in_several_languages():
    v = TreeSitterVerifier()
    assert v.check("python", "def f(:\n    pass\n").ok is False
    assert v.check("python", "def f():\n    pass\n").ok is True
    assert v.check("typescript", "function f( {\n").ok is False
    assert v.check("go", "package main\nfunc main() {\n").ok is False
    assert v.check("brainfudge", "++--") is None


def test_python_ast_reports_line():
    r = PythonAstVerifier().check("python", "x = 1\ny = (\n")
    assert r.ok is False and r.line == 1
    assert PythonAstVerifier().check("go", "x") is None


def test_registry_and_command_verifier(tmp_path):
    cfg = load_config(overrides=["verify.commands.python=['python3','-c','import sys,ast;ast.parse(open(sys.argv[1]).read())','{file}']"])
    vs = get_verifiers(cfg, "python")
    assert [v.name for v in vs] == ["treesitter", "python_ast", "command"]
    assert run_verifiers(vs, "python", "x = 1\n").ok
    bad = run_verifiers(vs, "python", "x = (\n")
    assert not bad.ok and bad.verifier == "treesitter"
    assert run_verifiers(get_verifiers(cfg, "go"), "go", "package main\n").verifier == "treesitter"
