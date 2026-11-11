"""Identify declared build inputs independently of matrix and publication policy."""

import ast
import hashlib
import json
import re
import tomllib
from pathlib import Path

from scripts.build.native_libraries import library_specs
from scripts.pipeline.model import Builder, GitSource, Target
from scripts.tooling import requirements

SCHEMA = 1
COMMON_MODULES = (
    "scripts.pipeline.artifacts",
    "scripts.pipeline.source",
    "scripts.pipeline.wheel",
    "scripts.tooling",
)
BUILDER_MODULES = {
    Builder.MATURIN: (
        "scripts.build.build_android_wheel",
        "scripts.build.requirements",
    ),
    Builder.CHAQUOPY: ("scripts.build.build_chaquopy_wheel",),
    Builder.CIBUILDWHEEL: (
        "scripts.build.cibuildwheel_prepare",
        "scripts.pipeline.cargo_env",
        "scripts.build.repair_android_wheel",
    ),
}
COMMON_FILES = (
    ".github/workflows/build-android-wheel-dispatch.yml",
    ".github/actions/prepare-source/action.yml",
    ".github/actions/setup-tools/action.yml",
    "scripts/pipeline/__main__.py",
    "scripts/pipeline/fingerprint.py",
)


def module_files(root: Path, modules: tuple[str, ...]) -> tuple[str, ...]:
    """Follow local imports, including package initializers and relative imports."""
    pending = list(modules)
    found = set()
    while pending:
        module = pending.pop()
        relative = module.replace(".", "/")
        path = root / (relative + ".py")
        if not path.is_file():
            path = root / relative / "__init__.py"
        if not path.is_file():
            raise FileNotFoundError(f"构建模块不存在：{module}")
        name = path.relative_to(root).as_posix()
        if name in found:
            continue
        found.add(name)
        parts = module.split(".")
        for n in range(1, len(parts)):
            initializer = root.joinpath(*parts[:n], "__init__.py")
            if initializer.is_file():
                pending.append(".".join(parts[:n]))
        package = parts if path.name == "__init__.py" else parts[:-1]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = (
                    ".".join(package[: len(package) - node.level + 1]) + "."
                    if node.level
                    else ""
                )
                base = prefix + (node.module or "")
                imports = [base, *(base + "." + alias.name for alias in node.names)]
            else:
                continue
            for imported in imports:
                if not (imported == "scripts" or imported.startswith("scripts.")):
                    continue
                candidate = root.joinpath(*imported.split("."))
                if (
                    candidate.with_suffix(".py").is_file()
                    or (candidate / "__init__.py").is_file()
                ):
                    pending.append(imported)
    return tuple(sorted(found))


class Fingerprinter:
    """Read shared inputs once per plan without persisting a stale process cache."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.project = tomllib.loads((self.root / "pyproject.toml").read_text())
        self.specs = library_specs(self.root / "scripts/build/native-libraries.toml")
        self.licenses = tomllib.loads(
            (self.root / "scripts/build/bundled-licenses.toml").read_text()
        )
        self._modules: dict[tuple[str, ...], tuple[str, ...]] = {}
        self._digests: dict[str, str] = {}

    def modules(self, modules: tuple[str, ...]) -> tuple[str, ...]:
        if modules not in self._modules:
            self._modules[modules] = module_files(self.root, modules)
        return self._modules[modules]

    def file_digest(self, name: str) -> str:
        if name not in self._digests:
            self._digests[name] = hashlib.sha256(
                (self.root / name).read_bytes()
            ).hexdigest()
        return self._digests[name]

    def __call__(self, target: Target) -> str:
        root = self.root
        modules = COMMON_MODULES + BUILDER_MODULES[target.builder]
        if target.native_dependencies and target.builder != Builder.MATURIN:
            modules += ("scripts.build.native_prepare", "scripts.build.native_cache")
        files = set(self.modules(modules)) | set(COMMON_FILES)
        if target.builder == Builder.CHAQUOPY:
            files.add("scripts/build/patches/chaquopy-libpython-link.patch")
        if target.builder == Builder.CIBUILDWHEEL:
            files.add("scripts/build/cibuildwheel.toml")
        for setting in (*target.config_settings, *target.environment):
            for relative in re.findall(r"\{workspace\}/([^\s\"';]+)", setting.value):
                path = (root / relative).resolve(strict=True)
                if not path.is_relative_to(root.resolve()) or not path.is_file():
                    raise ValueError(f"构建配置必须引用仓库内的明确文件：{relative}")
                files.add(path.relative_to(root.resolve()).as_posix())
        project = self.project
        tools = project["tool"]["py-extra-wheels"]
        settings = {"driver-python": tools["driver-python"]}
        if target.builder == Builder.CHAQUOPY:
            settings.update(
                {
                    key: tools[key]
                    for key in ("chaquopy-repository", "chaquopy-revision")
                }
            )
        source = target.to_dict()
        if isinstance(target.source, GitSource):
            # ref 只是解析入口；同一提交的别名不会改变编译内容。
            del source["source"]["ref"]
        specs = self.specs
        licenses = self.licenses
        payload = {
            "schema": SCHEMA,
            "target": source,
            "builder": target.builder,
            "runner": "ubuntu-latest",
            "requirements": requirements("build", project["dependency-groups"]),
            "tools": settings,
            "native": [specs[name].to_dict() for name in target.native_dependencies],
            "licenses": licenses.get(target.library),
            "files": {name: self.file_digest(name) for name in sorted(files)},
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def fingerprint(target: Target, root: Path) -> str:
    return Fingerprinter(root)(target)
