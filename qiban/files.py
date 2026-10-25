"""Restrict file tools to regular text files in the dedicated workspace."""

import re
from pathlib import Path
from urllib.parse import quote


class Files:
    def __init__(self, root):
        self.root = Path(root) / "workspace"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name):
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 120
            or name.startswith(".")
            or name.endswith((".", " "))
            or any(not (c.isalnum() or c in " _().-") for c in name)
            or re.match(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)", name, re.I)
            or Path(name).suffix.lower() not in {".txt", ".md", ".csv", ".json"}
        ):
            raise ValueError("文件名不合法，仅支持工作目录中的 txt/md/csv/json 文件。")
        target = self.root / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("不允许链接或目录。")
        return target

    def list(self):
        result = []
        for entry in self.root.iterdir():
            try:
                if self.path(entry.name).is_file():
                    result.append(entry.name)
            except ValueError:
                pass
        return sorted(result)

    def read(self, name):
        with self.path(name).open("rb") as file:
            content = file.read(200001)
        if len(content) > 200000:
            raise ValueError("文件超过 200 KB 限制。")
        return content.decode("utf-8-sig")

    def write(self, name, content):
        if not isinstance(content, str) or len(content.encode("utf-8")) > 200000:
            raise ValueError("内容超过 200 KB 限制。")
        with self.path(name).open("x", encoding="utf-8") as file:
            file.write(content)
        return {"name": name, "url": "/api/files/" + quote(name)}
