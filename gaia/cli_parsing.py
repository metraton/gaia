"""Argument-parsing policy shared by the gaia dispatcher and the standalone bin/cli entry points."""

import argparse


def refuse_abbreviations(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Turn option abbreviation off on *parser* and every subparser: the secret-read hook matches option names whole."""
    parser.allow_abbrev = False
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                refuse_abbreviations(child)
    return parser
