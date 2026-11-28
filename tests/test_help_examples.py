"""Audit help examples by parsing only; never execute example operations."""

import argparse
import importlib
from pathlib import Path
import re
import shlex
from unittest.mock import patch

import pytest

from nodetools_runtime import arguments, cli, firmware_cli, switch_health

ROOT = Path(__file__).resolve().parents[1]


class ParserCaptured(Exception):
    pass


def capture(factory):
    found = []
    def stop(parser, *args, **kwargs):
        found.append(parser)
        raise ParserCaptured()
    with patch.object(argparse.ArgumentParser, "parse_args", stop):
        with pytest.raises(ParserCaptured):
            factory()
    assert len(found) == 1
    return found[0]


def catalog():
    roots = {"nodetools": arguments.build_parser(), "nodefw": firmware_cli.parser("nodefw")}
    for name in ("nodebmc", "nodetask", "nodefru"):
        roots[name] = capture(lambda: cli.main([name, "--help"]))
    for path in (ROOT / "nodetools_runtime").glob("*.py"):
        text = path.read_text()
        if '__name__ == "__main__"' not in text or "ArgumentParser(" not in text:
            continue
        if path.stem == "switch_health":
            continue
        name = "nodetools_runtime." + path.stem
        module = importlib.import_module(name)
        roots[name] = capture(lambda: module.main(["--help"]))
        if " " not in roots[name].prog:
            roots.setdefault(roots[name].prog, roots[name])
    for path in ROOT.iterdir():
        if not path.is_file() or path.is_symlink() or not path.stat().st_mode & 0o111:
            continue
        text = path.read_text()
        match = re.search(r"exec python3 -m nodetools_runtime\.switch_health (\w+)", text)
        if match:
            name = match[1]
            roots[name] = capture(lambda: switch_health.main(name, ["--help"]))
        elif path.name.startswith("gen_") and "<<'PY'\n" in text:
            source = text.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
            roots[path.name] = capture(lambda: exec(compile(source, str(path), "exec"), {}))
    return roots


def walk(parser, seen=None):
    seen = set() if seen is None else seen
    if id(parser) in seen:
        return
    seen.add(id(parser))
    yield parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                yield from walk(child, seen)


def routes(roots):
    result = dict(roots)
    for action in roots["nodetools"]._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name in action.choices:
                result.setdefault(name, roots["nodetools"])
    return result


def examples(parser):
    for text in (parser.description, parser.epilog):
        active = False
        for line in (text or "").splitlines():
            if line.strip() == "Examples":
                active = True
                continue
            if not active or not line.startswith("  ") or not line.strip():
                continue
            tokens = shlex.split(line.strip())
            if tokens and not re.match(r"^\w+=", tokens[0]):
                yield line.strip(), tokens


def parse_example(tokens, lookup):
    parts, parsed = [[]], []
    for token in tokens:
        if token == "|":
            parts.append([])
        else:
            parts[-1].append(token)
    for part in parts:
        name, args = part[0].removeprefix("./"), part[1:]
        if name in ("python", "python3") and args[:1] == ["-m"]:
            name, args = args[1], args[2:]
        assert name in lookup, "unknown example command: " + name
        parser = lookup[name]
        if parser.prog == "nodetools" and name != "nodetools":
            args = [name, *args]
        namespace = parser.parse_args(args)
        current, matched = parser, {id(parser)}
        while True:
            sub = next((a for a in current._actions if isinstance(a, argparse._SubParsersAction)), None)
            choice = getattr(namespace, sub.dest, None) if sub else None
            if not sub or choice not in sub.choices:
                break
            current = sub.choices[choice]
            matched.add(id(current))
        parsed.append((namespace, matched))
    return parsed


def test_all_public_help_examples_are_visible_and_parse():
    roots = catalog()
    lookup = routes(roots)
    parsers = {id(p): p for root in roots.values() for p in walk(root)}
    count = 0
    for parser in parsers.values():
        visible, lines = parser.format_help(), list(examples(parser))
        assert lines, "missing help examples: " + parser.prog
        own_example = False
        for text, tokens in lines:
            assert text in visible, "example is not visible: " + parser.prog
            try:
                parsed = parse_example(tokens, lookup)
            except SystemExit as exc:
                pytest.fail(f"{parser.prog}: example rejected ({exc.code}): {text}")
            own_example |= any(id(parser) in matched for _, matched in parsed)
            count += 1
        assert own_example, "examples do not demonstrate this command: " + parser.prog
    print(f"Audited {len(parsers)} help scopes and {count} example lines")


def test_package_entrypoints_are_in_help_catalog():
    lookup = routes(catalog())
    section = (ROOT / "pyproject.toml").read_text().split("[project.scripts]", 1)[1].split("\n[", 1)[0]
    names = set(re.findall(r"(?m)^(\w+)\s*=", section))
    assert names and names <= lookup.keys()


def test_bmc_help_covers_every_action_and_user_workflow():
    roots = catalog()
    root = roots["nodeos"]
    parser = next(a for a in root._actions
                  if isinstance(a, argparse._SubParsersAction)).choices["bmc"]
    namespaces = [ns for _, tokens in examples(parser)
                  for ns, _ in parse_example(tokens, routes(roots))]
    choices = next(a.choices for a in parser._actions if a.dest == "action")
    assert set(choices) <= {ns.action for ns in namespaces}
    assert any(ns.action == "adduser" and ns.account_password for ns in namespaces)
    assert any(ns.action == "moduser" and ns.account_password for ns in namespaces)
    assert any(ns.action == "deluser" and ns.account and not ns.account_password for ns in namespaces)
    assert any(ns.action == "listuser" and not ns.account for ns in namespaces)
    assert any(ns.action == "listuser" and ns.account for ns in namespaces)
    assert any("[" in ns.nodes for ns in namespaces)
    assert "--channel" not in parser.format_help()
    assert "--id" not in parser.format_help()


@pytest.mark.parametrize("program", [
    "gen_hosts.sh", "gen_passwd.sh", "ibswhc", "ibswmon", "spxswhc", "spxswmon",
])
def test_auxiliary_help_prints_examples_without_operations(program, tmp_path):
    import os
    import subprocess
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(["/bin/bash", str(ROOT / program), "--help"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Examples" in result.stdout and "./" + program in result.stdout
    assert not list(tmp_path.iterdir())


def test_all_supported_help_variants_preserve_examples():
    from contextlib import redirect_stdout
    from io import StringIO
    roots = catalog()
    parsers = {id(p): p for root in roots.values() for p in walk(root)}
    count = 0
    for parser in parsers.values():
        flags = {flag for action in parser._actions for flag in action.option_strings
                 if flag.startswith("--help")}
        for flag in flags:
            output = StringIO()
            with redirect_stdout(output), pytest.raises(SystemExit) as result:
                parser.parse_args([flag])
            assert result.value.code == 0, (parser.prog, flag)
            text = output.getvalue()
            for example, _ in examples(parser):
                assert example in text, (parser.prog, flag, example)
            count += 1
    print(f"Audited {count} supported help variants")


@pytest.mark.parametrize("program", ["nodetools", "nodefw", "nodebmc", "nodetask", "nodefru"])
def test_every_main_device_command_scope_supports_debug(program):
    for parser in walk(catalog()[program]):
        options = parser._option_string_actions
        assert {"-x", "--debug"} <= options.keys(), parser.prog
        assert options["-x"] is options["--debug"]
        assert options["-x"].dest == "debug"
