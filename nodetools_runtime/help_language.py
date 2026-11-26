"""Chinese help for argparse entry points. Command syntax stays identical."""

import argparse
from contextlib import ExitStack

from .help_zh import MESSAGES


def _translate(value):
    if not value or value == argparse.SUPPRESS:
        return value

    def line_text(line):
        content = line.lstrip()
        translated = MESSAGES.get(content)
        if translated is None:
            role, _, subject = content.partition(" ")
            if role in ("compute", "nvswitch", "powershelf") and subject in MESSAGES:
                translated = MESSAGES[role] + MESSAGES[subject]
        return line[: len(line) - len(content)] + (translated if translated is not None else content)

    return "\n".join(line_text(line) for line in value.split("\n"))


class ChineseHelp(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        base = (
            argparse.RawDescriptionHelpFormatter
            if option_string == "--help-full-zh"
            else parser.formatter_class
        )

        class ChineseFormatter(base):
            def add_usage(self, usage, actions, groups, prefix=None):
                super().add_usage(usage, actions, groups, "用法：")

        def localize(stack, obj, attribute, replacement=None):
            original = getattr(obj, attribute)
            stack.callback(setattr, obj, attribute, original)
            setattr(obj, attribute, replacement if replacement is not None else _translate(original))

        with ExitStack() as stack:
            localize(stack, parser, "formatter_class", ChineseFormatter)
            localize(stack, parser, "description")
            localize(stack, parser, "epilog")
            for group in parser._action_groups:
                localize(stack, group, "title")
            for action in parser._actions:
                localize(stack, action, "help")
                if isinstance(action, argparse._SubParsersAction):
                    for choice in action._choices_actions:
                        localize(stack, choice, "help")
            parser.print_help()
        parser.exit()


class LocalizedArgumentParser(argparse.ArgumentParser):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.add_argument("--help-zh", action=ChineseHelp, nargs=0, help="show help in Chinese")
