"""Offline smoke run: no token, Telegram access or production database required."""
import argparse
import json
import random

from bot.config import load_json
from bot.domain import advance, initial_state, validate_rules
from bot.presentation import session_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--rules", default="config/rules.json")
    args = parser.parse_args()
    rules = load_json(args.rules)
    validate_rules(rules)
    state = initial_state(rules)
    result = advance(state, args.days, rules, random.Random(42))
    print(session_text(result, state))


if __name__ == "__main__":
    main()
