"""A bounded child process keeps corrupt/slow document parsers out of the API worker."""

import json
import sys
from pathlib import Path

from manimagent.content_store import ContentError
from manimagent.materials import parse_file


def main():
    task = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    task["path"] = Path(task["path"])
    try:
        candidate = parse_file(**task)
    except ContentError as exc:
        candidate = {"error": {"code": exc.error.code, "message": str(exc), "state": exc.state}}
    except Exception:
        candidate = {
            "error": {
                "code": "invalid_file",
                "message": "文件损坏或格式无法解析，请重新提供原件",
                "state": "needs_input",
            }
        }
    Path(sys.argv[2]).write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
