# Py Extra Wheels

**English** | [简体中文](README.zh-CN.md)

Py Extra Wheels builds and hosts prebuilt Python wheels for Android. Use them with the [Chaquopy](https://chaquo.com/chaquopy/) Python SDK or when embedding Python in an Android app. See [Using Python on Android](https://docs.python.org/3.14/using/android.html#using-python-on-android) for embedding details.

Package index: [https://ririv.github.io/py-extra-wheels/](https://ririv.github.io/py-extra-wheels/)

The previous index at [https://ririv.github.io/android-wheels/](https://ririv.github.io/android-wheels/) remains available. Updates are synchronized through events after publication, with no scheduled polling.

## Usage

Install a package with `pip`, using this repository as an additional package index. Replace `<package-name>` with the package you want to install:

```bash
pip install --extra-index-url https://ririv.github.io/py-extra-wheels/ <package-name>
```

For example, to install `zstandard`:

```bash
pip install --extra-index-url https://ririv.github.io/py-extra-wheels/ zstandard
```

### requirements.txt

You can also add the index and package names to `requirements.txt`:

```txt
--extra-index-url https://ririv.github.io/py-extra-wheels/

zstandard
xxhash
```

## Build targets

- Python: **3.10–3.15**. Python 3.10 is included as an additional compatibility target.
- Android API level: **24** by default.
- ABIs: **`arm64-v8a`** and **`x86_64`**.

Most recipes request all six Python versions by default; the current scientific stack explicitly selects Python 3.12–3.15 because of its dependencies. The separate workflow for building an unlisted source repository defaults to Python **3.13**. Each package and its build dependencies must support the requested version. Upstream-declared or verified incompatibilities are recorded and skipped; other build failures are reported. Builds use the version selected in the recipe without automatically choosing an older release.

The matrix validated on **2026-10-29** contains **624 wheels across 54 packages**, including **104 wheels across 52 packages for Python 3.15**. These results cover builds and static checks; device and emulator runtime tests have not been performed. See the [complete matrix and verification record](docs/python-matrix.md).

On **2026-10-31**, NumPy 2.5.4 adds **eight Android wheels for Python 3.12–3.15**. Its Python 3.13 wheels passed numerical smoke tests on both ABIs using Android API 34 emulators. See [NumPy and OpenBLAS validation](docs/numpy.md).

ContourPy 1.4.0, pandas 3.1.0, and Matplotlib 3.11.2 add **24 wheels for Python 3.12–3.15**. The final Python 3.13 stack passed Android API 34 smoke tests on both ABIs, including DataFrame operations and headless PNG/SVG/PDF output. See [scientific stack validation](docs/scientific.md).

Shapely 2.2.0, DuckDB 2.0.0, polars-runtime-32 2.1.0, Thrift 0.25.0, and SciPy 1.18.1 add **44 wheels**, bringing the recipe set to **63 packages**. SciPy targets ARM64 and API 26; the other four target both ABIs and API 24. Python coverage follows upstream and dependency requirements. The Python 3.13 data suite passed on both ABIs, and SciPy passed numerical checks on ARM64. Polars excludes desktop clipboard support. See [data processing and scientific extensions](docs/data-processing.md).

scikit-learn 1.9.1, PyArrow 26.0.0, and psycopg2-binary 2.9.13 add **24 wheels**, bringing the recipe set to **66 packages**. All three target API 26. scikit-learn covers Python 3.12–3.15 on ARM64; PyArrow covers 3.12–3.15 on both ABIs; psycopg2-binary covers 3.10–3.15 on both ABIs. The learning and analytics suites passed on ARM64 with Python 3.13–3.15 using Android API 34; analytics also passed on x86_64 with Python 3.13. See [machine learning, Arrow, and PostgreSQL validation](docs/analytics.md).

## Build and publish

GitHub Actions runs the shared pipeline:

1. **Plan:** read `recipes/*.toml`, select affected packages, pin Git commits or official PyPI source distributions, and generate independent package × Python × ABI targets.
2. **Build:** `build-wheels.yml` calls `build-wheel-batch.yml`, which dispatches targets through `build-android-wheel-dispatch.yml`. Targets run in parallel using maturin, cibuildwheel, or Chaquopy.
3. **Publish:** verify the complete planned artifact set, source provenance, hashes, wheel tags, and ELF alignment, then update the `wheels` branch in one commit.
4. **Deploy:** publish the HTML package index through `pages.yml` and notify the compatibility site to synchronize. Failed notifications can be retried independently.

## Add a package

For a package using a supported build backend, add `recipes/<package-name>.toml`. Declare its Git repository and ref or a fixed official PyPI sdist version, project directory, build backend, and Android targets. TOML also supports external environment settings, PEP 517 parameters, and native dependencies. New build backends require corresponding builder support first.

All recipes share the same workflows. Recipe changes check the affected package; shared code changes check all recipes. Targets with matching build fingerprints and published wheel blobs are skipped. Use `rebuild=force` to rebuild selected targets. Historical wheels need one recorded build before they can be reused.

For example, build two Python versions and retain the results as Actions artifacts:

```sh
gh workflow run build-wheels.yml \
  -f recipes=pydantic-core \
  -f python_versions=3.10,3.14 \
  -f publication=build
```

`publication=build` retains artifacts only. The default, `publication=publish`, also publishes verified wheels and updates the package indexes. Configuration and manual build options are described in the [recipe guide](docs/recipes.md).

## Packaging standards

Wheel filenames follow [PEP 427](https://peps.python.org/pep-0427/#recommended-installer-features), usually as `{distribution}-{version}-{python tag}-{abi tag}-{platform tag}.whl`.

| Standard | Purpose |
| --- | --- |
| [PEP 427](https://peps.python.org/pep-0427/) | Wheel file format and filenames. |
| [PEP 503](https://peps.python.org/pep-0503/) | HTML package index and normalized package names. |
| [PEP 738](https://peps.python.org/pep-0738/#packaging) | Android platform support and wheel tags. |

The current package index serves HTML pages.

## Documentation

The detailed guides below are maintained in Chinese:

- [Recipes and parallel builds](docs/recipes.md)
- [Pipeline architecture and development checks](docs/pipeline.md)
- [Android Python runtimes and build routes](docs/python-builds.md)
- [Complete Python matrix and verification](docs/python-matrix.md)
- [Scientific stack and headless plotting](docs/scientific.md)
- [Package selection and native-extension ranking audit](docs/package-selection.md)
- [Index migration and compatibility address](docs/index-migration.md)
