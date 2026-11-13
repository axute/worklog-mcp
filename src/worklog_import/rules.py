"""Project rules: map sessions to projects by path or keyword.

    default: unsortiert
    rules:
      - project: website
        paths: ["*/website*", "*/homepage*"]
        keywords: [landing page, "css"]

First matching rule wins. Paths are glob patterns against the working
directory; keywords are searched (case-insensitive) in title and prompts.
Read with PyYAML when installed, otherwise with a small built-in parser for
exactly this shape. JSON works too.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .sources import Session

DEFAULT_PROJECT = "unsortiert"


@dataclass
class Rule:
    project: str
    paths: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)


@dataclass
class Rules:
    default: str = DEFAULT_PROJECT
    rules: List[Rule] = field(default_factory=list)

    def projects(self) -> List[str]:
        out: List[str] = []
        for r in self.rules:
            if r.project not in out:
                out.append(r.project)
        return out

    def match_text(self, text: str) -> Optional[str]:
        text = text.lower()
        for r in self.rules:
            if any(k.lower() in text for k in r.keywords):
                return r.project
        return None

    def match(self, s: Session) -> Optional[str]:
        text = (s.title + "\n" + "\n".join(t.prompt for t in s.turns[:20])).lower()
        for r in self.rules:
            if s.cwd and any(fnmatch.fnmatch(s.cwd, p) for p in r.paths):
                return r.project
            if any(k.lower() in text for k in r.keywords):
                return r.project
        return None


def normalize_project(name: str) -> str:
    """'Heizung Smart Home' -> 'heizung-smart-home'; keeps dots ('label.presets')."""
    n = re.sub(r"[\s_/]+", "-", (name or "").strip().lower())
    n = re.sub(r"[^a-z0-9äöüß.-]", "", n)
    return re.sub(r"-{2,}", "-", n).strip("-.")


def load_rules(path: Optional[Path]) -> Rules:
    if not path:
        return Rules()
    text = Path(path).read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(text)
    else:
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(text)
        except ImportError:
            data = parse_simple_yaml(text)
    return _from_data(data or {})


def _from_data(data: Dict[str, Any]) -> Rules:
    rules = []
    for r in data.get("rules") or []:
        if not isinstance(r, dict) or not r.get("project"):
            raise ValueError(f"rule without project: {r!r}")
        rules.append(Rule(project=str(r["project"]),
                          paths=[str(p) for p in r.get("paths") or []],
                          keywords=[str(k) for k in r.get("keywords") or []]))
    return Rules(default=str(data.get("default") or DEFAULT_PROJECT), rules=rules)


def _scalar(v: str) -> Any:
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        items, buf, quote = [], "", None
        for ch in inner:
            if quote:
                if ch == quote:
                    quote = None
                else:
                    buf += ch
            elif ch in "\"'":
                quote = ch
            elif ch == ",":
                items.append(buf.strip())
                buf = ""
            else:
                buf += ch
        items.append(buf.strip())
        return [i for i in items if i != ""]
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def parse_simple_yaml(text: str) -> Dict[str, Any]:
    """Minimal YAML for the rules file: top-level scalars and a list of flat mappings."""
    data: Dict[str, Any] = {}
    current_list: Optional[List[Dict[str, Any]]] = None
    item: Optional[Dict[str, Any]] = None
    for raw in text.splitlines():
        line = raw.split(" #")[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0:
            key, _, value = stripped.partition(":")
            if value.strip():
                data[key.strip()] = _scalar(value)
                current_list = None
            else:
                current_list = data.setdefault(key.strip(), [])
            continue
        if current_list is None:
            raise ValueError(f"unexpected indentation: {raw!r}")
        if stripped.startswith("- "):
            item = {}
            current_list.append(item)
            stripped = stripped[2:].strip()
        if item is None:
            raise ValueError(f"list item expected: {raw!r}")
        key, _, value = stripped.partition(":")
        item[key.strip()] = _scalar(value)
    return data
