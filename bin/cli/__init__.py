"""bin/cli package -- Gaia unified CLI plugin directory."""

import argparse


def refuse_abbreviations(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Turn option abbreviation off on *parser* and every subparser: the secret-read hook matches option names whole."""
    parser.allow_abbrev = False
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                refuse_abbreviations(child)
    return parser
