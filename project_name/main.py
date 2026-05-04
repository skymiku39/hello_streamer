import argparse
import sys


def build_greeting(name=None, yell=False):
    target = "World" if name is None else name.strip()
    if not target:
        raise ValueError("name must not be empty")

    greeting = f"Hello, {target}!"
    return greeting.upper() if yell else greeting


def hello():
    return build_greeting()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Generate a friendly greeting.")
    parser.add_argument("--name", help="Name to greet.")
    parser.add_argument(
        "--yell",
        action="store_true",
        help="Print the greeting in uppercase.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    try:
        message = build_greeting(name=args.name, yell=args.yell)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())