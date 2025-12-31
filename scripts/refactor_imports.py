#!/usr/bin/env python3
"""Refactor 'from x import y' to 'import x' style imports using libcst.

Usage:
    python scripts/refactor_imports.py src/runson/
"""

from __future__ import annotations

import sys
from pathlib import Path

import libcst as cst


class ImportCollector(cst.CSTVisitor):
    """First pass: collect import mappings from the file."""

    def __init__(self):
        super().__init__()
        # map: imported_name -> (module_path, original_name)
        # e.g., "cli" -> (".cli", "cli") from "from .cli import cli"
        self.import_map: dict[str, tuple[str, str]] = {}
        # modules that need aliases due to name collision
        self.needs_alias: set[str] = set()

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        if isinstance(node.names, cst.ImportStar):
            return True

        module_path = self._get_module_path(node)
        if not module_path:
            return True

        # skip non-local imports
        if not self._is_local_import(node):
            return True

        module_name = module_path.lstrip(".").split(".")[-1] if module_path.lstrip(".") else ""

        for alias in node.names:
            if isinstance(alias, cst.ImportAlias):
                original_name = alias.name.value if isinstance(alias.name, cst.Name) else None
                if not original_name:
                    continue

                local_name = original_name
                if alias.asname and isinstance(alias.asname, cst.AsName):
                    if isinstance(alias.asname.name, cst.Name):
                        local_name = alias.asname.name.value

                self.import_map[local_name] = (module_path, original_name)

                # check for name collision (imported name == module name)
                if original_name == module_name:
                    self.needs_alias.add(module_path)

        return True

    def _get_module_path(self, node: cst.ImportFrom) -> str | None:
        dots = "".join("." for _ in node.relative) if node.relative else ""
        if node.module:
            if isinstance(node.module, cst.Name):
                return f"{dots}{node.module.value}"
            elif isinstance(node.module, cst.Attribute):
                return f"{dots}{self._attr_to_str(node.module)}"
        return dots if dots else None

    def _attr_to_str(self, node: cst.Attribute) -> str:
        parts = []
        current = node
        while isinstance(current, cst.Attribute):
            parts.append(current.attr.value)
            current = current.value
        if isinstance(current, cst.Name):
            parts.append(current.value)
        return ".".join(reversed(parts))

    def _is_local_import(self, node: cst.ImportFrom) -> bool:
        if node.relative:
            return True
        module_path = self._get_module_path(node)
        return module_path is not None and module_path.startswith("runson")


class ImportTransformer(cst.CSTTransformer):
    """Second pass: transform imports and update call sites."""

    def __init__(self, import_map: dict[str, tuple[str, str]], needs_alias: set[str]):
        super().__init__()
        self.import_map = import_map
        self.needs_alias = needs_alias
        # track which modules we've already transformed (to avoid duplicates)
        self.transformed_modules: set[str] = set()
        # map: module_path -> alias_name (for modules needing aliases)
        self.module_aliases: dict[str, str] = {}
        for module_path in needs_alias:
            module_name = module_path.lstrip(".").split(".")[-1]
            self.module_aliases[module_path] = f"{module_name}_module"

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.BaseStatement | cst.RemovalSentinel:
        if isinstance(updated_node.names, cst.ImportStar):
            return updated_node

        module_path = self._get_module_path(updated_node)
        if not module_path or not self._is_local_import(updated_node):
            return updated_node

        # skip already transformed
        if module_path in self.transformed_modules:
            return cst.RemovalSentinel.REMOVE
        self.transformed_modules.add(module_path)

        # extract module name and build new import
        module_name = module_path.lstrip(".").split(".")[-1]

        if module_path in self.module_aliases:
            # from . import foo as foo_module
            alias_name = self.module_aliases[module_path]
            new_names = [
                cst.ImportAlias(
                    name=cst.Name(module_name),
                    asname=cst.AsName(
                        whitespace_before_as=cst.SimpleWhitespace(" "),
                        whitespace_after_as=cst.SimpleWhitespace(" "),
                        name=cst.Name(alias_name),
                    ),
                )
            ]
        else:
            # from . import foo
            new_names = [cst.ImportAlias(name=cst.Name(module_name))]

        # handle absolute imports like "from runson.core import ..."
        if not updated_node.relative:
            # from runson.core import x -> from runson import core
            parts = module_path.split(".")
            if len(parts) > 1:
                parent = ".".join(parts[:-1])
                return updated_node.with_changes(
                    module=cst.Attribute(
                        value=cst.Name(parts[0]) if len(parts) == 2 else self._build_attr(parts[:-1]),
                        attr=cst.Name(""),  # placeholder
                    )
                    if len(parts) > 2
                    else cst.Name(parent),
                    names=new_names,
                )

        # relative import: from .foo import x -> from . import foo
        return updated_node.with_changes(
            module=None,
            names=new_names,
        )

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.BaseExpression:
        name = updated_node.value
        if name not in self.import_map:
            return updated_node

        module_path, original_name = self.import_map[name]

        # get the module reference name
        if module_path in self.module_aliases:
            module_ref = self.module_aliases[module_path]
        else:
            module_ref = module_path.lstrip(".").split(".")[-1]

        # transform: name -> module.name
        return cst.Attribute(
            value=cst.Name(module_ref),
            attr=cst.Name(original_name),
        )

    def _get_module_path(self, node: cst.ImportFrom) -> str | None:
        dots = "".join("." for _ in node.relative) if node.relative else ""
        if node.module:
            if isinstance(node.module, cst.Name):
                return f"{dots}{node.module.value}"
            elif isinstance(node.module, cst.Attribute):
                return f"{dots}{self._attr_to_str(node.module)}"
        return dots if dots else None

    def _attr_to_str(self, node: cst.Attribute) -> str:
        parts = []
        current = node
        while isinstance(current, cst.Attribute):
            parts.append(current.attr.value)
            current = current.value
        if isinstance(current, cst.Name):
            parts.append(current.value)
        return ".".join(reversed(parts))

    def _is_local_import(self, node: cst.ImportFrom) -> bool:
        if node.relative:
            return True
        module_path = self._get_module_path(node)
        return module_path is not None and module_path.startswith("runson")

    def _build_attr(self, parts: list[str]) -> cst.BaseExpression:
        if len(parts) == 1:
            return cst.Name(parts[0])
        result = cst.Name(parts[0])
        for part in parts[1:]:
            result = cst.Attribute(value=result, attr=cst.Name(part))
        return result


def refactor_file(filepath: Path) -> str:
    """Refactor a single Python file."""
    source = filepath.read_text()
    tree = cst.parse_module(source)

    # first pass: collect imports
    collector = ImportCollector()
    tree.walk(collector)

    if not collector.import_map:
        return source  # no local imports to transform

    # second pass: transform
    transformer = ImportTransformer(collector.import_map, collector.needs_alias)
    modified = tree.visit(transformer)

    return modified.code


def main():
    if len(sys.argv) < 2:
        print("Usage: refactor_imports.py <file_or_directory>")
        sys.exit(1)

    target = Path(sys.argv[1])

    if target.is_file():
        result = refactor_file(target)
        print(result)
    elif target.is_dir():
        for pyfile in sorted(target.rglob("*.py")):
            print(f"Refactoring {pyfile}...")
            try:
                result = refactor_file(pyfile)
                pyfile.write_text(result)
                print("  ✓ Done")
            except Exception as e:
                print(f"  ✗ Error: {e}")
    else:
        print(f"Error: {target} is not a file or directory")
        sys.exit(1)


if __name__ == "__main__":
    main()
