import sys

from .ui import App


def main() -> int:
    return App().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
