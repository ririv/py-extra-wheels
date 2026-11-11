# Recipe 与并行构建

## 配置与调度分离

每个包的源码、后端和默认构建目标存放在 `recipes/<包名>.toml`。统一入口 `.github/workflows/build-wheels.yml` 调用 `scripts.pipeline`，将 Git ref 固定为完整提交 SHA，或将 PyPI sdist 固定为发行版本、下载地址与 SHA-256，并生成包 × Python × ABI 的独立任务矩阵。

TOML 描述包配置；Python 负责规划、源码校验、环境组合、产物校验与发布；YAML 负责事件、权限、runner、并行矩阵及 Actions 产物传输。现有 maturin、cibuildwheel 和旧版 Chaquopy 构建能力继续使用。

```text
recipes/*.toml
       ↓
固定 Git 提交或官方 sdist 哈希，生成完整构建计划
       ↓
包 × Python × ABI 独立并行编译
       ↓
验证每个 wheel，生成来源与 SHA-256 清单
       ↓
全部目标成功后，统一校验并提交 wheels
       ↓
内容变化时触发一次索引部署，再事件通知旧索引
```

默认请求 Python 3.10–3.15，不切换旧包版本。规划器从固定 Git 提交的项目元数据或固定 sdist 的 `PKG-INFO` 读取 `Requires-Python`，不满足的版本记录在 `build-plan.json` 的 `skipped` 中。`build-compatibility.toml` 另记录已经实际复现的上游不兼容：每条绑定包名、Python 版本及不可变 Git SHA / sdist SHA-256，并附原因和日志证据。源码更新后旧记录不再匹配，会重新尝试；不会仅凭一次失败永久封禁整个包。没有声明上限不代表已经验证 3.15：需要实际构建验证，编译失败仍然失败，不自动忽略或降级。

统一计划按最多 96 个目标分批，每批再展开独立任务，避开单个 Actions 矩阵 256 个任务和输出大小限制。批次全部并行，发布在所有批次成功后进行。

稳定 ABI（`abi3`）与纯 Python wheel 跨目标重建时，使用构建号区分文件名：例如 `310`、`315`；纯 Python 产物再包含目标架构。ABI 标签保持上游真实兼容范围，来源清单保留实际构建的 Python 版本。

矩阵设置 `fail-fast: false`，一个目标失败不会取消其他编译。发布要求本次计划的全部目标成功、产物齐全且校验通过；不会把不完整构建当成成功发布。成功目标的 Actions 产物仍保留，可以重新运行失败任务。实际并发数受 GitHub runner 和账户配额限制。

## TOML 格式

```toml
name = "pydantic-core"
repository = "pydantic/pydantic-core"
ref = "main"
directory = "."
backend = "maturin"

[android]
python = ["3.10", "3.11", "3.12", "3.13", "3.14", "3.15"]
abis = ["arm64-v8a", "x86_64"]
api = 24
```

Git 源必须同时声明 `repository` 与 `ref`。也可以明确选择 PyPI 官方源码发行包（sdist）：

```toml
name = "msgpack"
pypi-version = "1.2.4"
directory = "."
backend = "setuptools.build_meta"

[android]
python = ["3.10", "3.11", "3.12", "3.13", "3.14", "3.15"]
abis = ["arm64-v8a", "x86_64"]
api = 24
```

`pypi-version` 与 `repository` / `ref` 互斥。规划器要求指定版本只有一个未撤回的官方 sdist，下载并核验 SHA-256、`PKG-INFO` 包名和版本、实际后端；构建器再次核验后直接解包构建。不会在 Git 构建失败时自动切换来源，也不生成或修改上游源码。

来源、后端和 Android 目标必须显式提供；`environment`、`config-settings` 与 `native-dependencies` 是可选配置，省略表示不设置额外环境或原生依赖。未知字段、重复目标和非法值会使规划阶段失败。

| 字段 | 含义 |
| --- | --- |
| `name` | 规范化包名，必须与 TOML 文件名一致 |
| `repository` | GitHub `owner/repo`，不带 URL 前缀 |
| `ref` | Git 源的明确分支、tag 或提交 SHA |
| `pypi-version` | PyPI sdist 源的固定发行版本，与 Git 源字段互斥 |
| `directory` | Python 项目在源码仓库内的相对目录；根目录填写 `.` |
| `backend` | 项目实际的 PEP 517 后端，与源码检测结果不一致时直接失败 |
| `android.python` | 默认构建集合，不是上游支持版本的声明；手动任务可覆盖 |
| `android.abis` | ABI 构建集合，支持 `arm64-v8a`、`x86_64` |
| `android.api` | Android API 整数，至少为 24 |

目前显式建模的后端为 `maturin`、`setuptools.build_meta`、`_pyyaml_pep517`、`pep517_backend.hooks`、`mesonpy`、`hatchling.build`、`setuptools.build_meta:__legacy__`、Pillow 的 `backend`、`scikit_build_core.build` 和 DuckDB 的 `duckdb_packaging.build_backend`。新增已有后端的包只需添加 TOML；其他后端须先扩展枚举并验证构建路径。

固定版本 PyPI sdist 若没有声明 `build-backend`，且保留 `setup.py`，可显式选择 `setuptools.build_meta:__legacy__`。选择其他后端时仍要求源码声明与之吻合；不会因为缺少 `pyproject.toml` 自动改用 legacy，也不会生成或修改元数据。

`jiter` 使用 `directory = "crates/jiter-python"`。迁移保留了原有默认分支的行为，包括 `regex` 的 `hg`、`orjson` 和 `xxhash` 的 `master`；`ormsgpack` 使用原仓库重定向后的规范地址 `ormsgpack/ormsgpack`。

cibuildwheel 明确读取仓库的 `scripts/build/cibuildwheel.toml`，目标、环境和产物处理由流水线提供；不读取上游项目的 cibuildwheel 发布配置。PEP 517 构建后端仍读取原始 `pyproject.toml`，recipe 只接受下述声明式环境、PEP 517 参数和受支持的原生依赖，不提供任意 Shell hook 或源码补丁入口。

配置只支持当前已实现的 Android 目标；未来添加其他平台时应新增对应的目标类型和构建器，不能依赖缺省字段把其他平台当作 Android 执行。

## 编译环境与原生依赖

包专用的上游开关可放入 `[environment]`，值必须是单行字符串，按字面值传递，不执行 Shell 表达式：

```toml
[environment]
SODIUM_INSTALL = "system"
```

`PATH`、`CC`、`CFLAGS`、`LDFLAGS`、目标 Python/ABI、`CIBW_*`、`GITHUB_*` 等流水线变量不能被 recipe 覆盖。环境值会进入构建计划和产物来源清单；maturin 与 cibuildwheel 使用同一份声明。不能在变量里换行添加其他设置。

PEP 517 后端的命令参数放入 `[config-settings]`，值可以是单行字符串或非空字符串数组。数组逐项传入同一个参数名，适用于 Meson 的多个 `setup-args`；包含空格的单个字符串仍保持一个参数，不会隐式拆分。例如 Pillow 明确关闭宿主路径猜测，并要求 JPEG、PNG 所需扩展成功构建：

```toml
[config-settings]
platform-guessing = "disable"
jpeg = "enable"
zlib = "enable"
```

带点号的参数名必须用 TOML 引号包住，例如：

```toml
[config-settings]
"cmake.define.CMAKE_C_STANDARD_LIBRARIES" = "-lpython{python}"
```

这是后端参数名中的点号，不是 TOML 子表。参数按独立的 `-C key=value` 边界交给后端，不拼接 Shell 命令，空格和特殊字符保持字面含义；键和值写入构建计划与来源清单。cibuildwheel 和 Chaquopy 都把同一组参数传入依赖探测与构建钩子。值中的 `{python}` 和 `{abi}` 在规划时显式替换成目标版本和 ABI；`{workspace}` 在执行构建时替换成当前仓库的绝对路径，可引用仓库内维护的外部配置文件。NumPy 通过 `--cross-file={workspace}/scripts/build/meson/{abi}.ini` 声明 Android 的机器属性和 `long double` 格式，避免在宿主上运行目标程序，不修改包源码。

maturin 仅接入两种显式 Cargo 参数，不把任意 PEP 517 参数传给命令行：

```toml
[config-settings]
no-default-features = "true"
features = ["csv", "parquet", "polars-python/streaming"]
```

`no-default-features` 必须为单个 `"true"` 或 `"false"` 字符串；`features` 逐项声明 feature，可使用 `依赖名/feature`。构建器生成独立的 `--no-default-features`、`--features` 参数，不拼接 Shell，也不隐式拆分字符串。完整选择写入构建计划与来源清单。

需要外部原生库时，在 TOML 顶层声明：

```toml
name = "cffi"
pypi-version = "2.1.1"
directory = "."
backend = "setuptools.build_meta"
native-dependencies = ["libffi"]

[android]
python = ["3.10", "3.11", "3.12", "3.13", "3.14", "3.15"]
abis = ["arm64-v8a", "x86_64"]
api = 24
```

依赖名由 `NativeLibrary` 枚举限制，固定源码 URL、版本、SHA-256、构建系统、配置参数和许可证文件在 `scripts/build/native-libraries.toml`。目前提供 libffi、libsodium、OpenSSL、CRC32C、libjpeg-turbo、libiconv、libxml2、libxslt、OpenBLAS、GEOS、Arrow C++、libpq，并为 OpenSSL 分别提供静态与共享配置；它们是构建依赖，不计作新增 Python 包。

原生依赖的公共参数放在 `args`；需要区分架构时，使用该依赖的 `abi-args` 表，显式给出 `arm64-v8a` 和 `x86_64` 两组参数，构建时只追加当前 ABI 的一组。原有未声明该表的依赖继续只使用公共参数，来源清单保持原格式。OpenBLAS 显式选择 LP64、上游自带的 C 版 LAPACK，以及 ARMV8/NEHALEM CPU 目标，不需要 Fortran 编译器或源码补丁。

每个矩阵任务显式使用目标 NDK、ABI 和 API。cibuildwheel 与 Chaquopy 在后端依赖探测前编译或恢复 SDK，目标 Python 开发环境建立后核对来源清单并复制到各自开发前缀。maturin 使用任务独立前缀。原生库编译时只使用下载并校验的固定源码，不改源码。Autotools、OpenSSL Configure、CMake、固定 Android 工具链的 CMake superbuild、仅安装 PostgreSQL 客户端的构建系统使用互斥枚举分派；命令以参数数组调用；原生配置参数只支持显式 `{prefix}` 安装路径占位。依赖编译失败立即终止，不切换系统库或纯 Python 实现。静态库供 Python 扩展链接，非系统共享运行库仍由 auditwheel 打包。

原生依赖可通过 `source-directory` 显式选择压缩包内的构建子目录，省略时为压缩包根目录；绝对路径与 `..` 被拒绝，许可证仍从原压缩包根目录读取。CMake superbuild 的子项目共用固定 ABI/API 工具链，并在安装时剥离调试符号。cibuildwheel 与 Chaquopy 的 SDK 缓存按固定依赖、目标和工具链复用，命中后校验完整文件清单与哈希，详见 [机器学习与数据连接扩展](analytics.md)。

Android 交叉编译的宿主构建钩子使用显式 `/system/bin/sh` 到宿主 `/bin/sh` 的映射；NDK sysroot 的头文件与 ABI/API 库目录显式提供给构建器，避免搜索宿主 `/usr/include` 和 `/usr/lib`。

所有新构建的 ELF 加载段都必须满足 16 KB 对齐。C/C++ 原生库和 Rust 链接显式设置页大小参数，auditwheel 使用任务临时的 patchelf CLI 适配器保留新增段的对齐；不修改第三方工具源码。发布校验逐个检查 wheel 内的 ELF 加载段，发现 4 KB 段或文件偏移与虚拟地址不匹配时直接失败。[Android 官方说明](https://developer.android.com/guide/practices/page-sizes) 同时要求检查代码中的页大小假设；默认构建流水线只进行构建和静态检查。NumPy 另有 [独立按需运行验证](numpy.md)，科学计算依赖栈另有 [组合运行检查](scientific.md)，覆盖 Python 3.13 的双 ABI、Android API 34 和 4 KB 页；尚未进行 16 KB 页设备运行验证。

最终 wheel 的 `.dist-info/native-libraries.json` 记录实际依赖来源，`.dist-info/licenses/native/` 保存许可证；发布阶段核验这些记录与固定配置一致，并照常验证 RECORD 哈希。原生依赖接入三个构建器。旧版 Chaquopy 在建立目标 Python 开发前缀后核对并复制预先准备的 SDK，再执行 PEP 517 依赖探测；最终 wheel 使用共用的 Android repair 与 16 KB 对齐校验。

上游在 wheel 中漏带捆绑库许可证时，使用 `scripts/build/bundled-licenses.toml` 显式固定 sdist、原生库压缩包和许可文件哈希；打包阶段补入文件及来源清单，保持二进制原样，发布阶段复核。目前用于 Matplotlib、DuckDB、SciPy、scikit-learn 和 PyArrow，详见 [科学计算依赖栈](scientific.md)、[数据处理扩展](data-processing.md) 与 [机器学习与数据连接扩展](analytics.md)。

maturin 构建还会读取原始 `build-system.requires`，在匹配目标版本的宿主 Python 环境安装其构建依赖，继续使用统一固定的 maturin 构建器。驱动 Python 与宿主、目标 Python 分离，保留已有 Python 3.10 构建路径。

## 外部构建工具

需要 Fortran 的 recipe 在顶层显式声明 `build-tools = ["fortran"]`，工具名由 `BuildTool` 枚举限制。Android cibuildwheel 使用其内置 Flang 工具链准备过程；旧版 Chaquopy 通过独立启动器将同一过程绑定到实际 NDK 编译器，避免把编译器 wrapper 误当作 NDK 路径。目标 API、ABI 和包源码保持原声明，工具准备失败即停止。

旧版构建器显式启用 `-O2`，保留既有平台参数。目标 CPython 的头文件与动态库通过项目独立的 `python3.pc`、`python-X.Y.pc` 提供给 Meson；声明 NumPy 构建依赖的项目通过构建环境中官方 `numpy-config --pkgconfigdir` 提供 Meson 查询目录。maturin 的当前项目目录中提供链接脚本，将旧 `libgcc` 名称映射到 NDK `libunwind`，将 `libpython3` 映射到目标版本动态库；不会修改 NDK 或宿主 SDK。

## 自动选择构建范围

只有相关文件推送到 `main` 才启动统一构建入口，没有定时扫描。

- 修改某个 TOML，只检查对应包的默认目标；已发布且构建指纹一致的目标跳过。新增 Python 或 ABI 时只补齐新目标。
- 修改共享代码或构建工作流，检查全部现存 recipe；只重新构建指纹变化或缺少有效发布记录的目标。真正影响全部构建器的公共编译修改仍会触发全量构建。
- 删除 recipe 不会重新构建已删除的包；仅删除文件时，空矩阵直接跳过构建。
- 文档变化不会触发 native wheel 构建。

仅修改测试、发布模块或通知动作不触发 native 构建；这些变化仍运行 CI。构建指纹按实际构建器及其本地 Python 导入模块计算，并纳入固定源码、Python/ABI/API、环境、命令参数、工具配置、选用的原生依赖及许可证配置、明确引用的仓库配置文件。例如修改 Chaquopy 适配器不使 maturin/cibuildwheel 目标失效，修改 OpenBLAS 版本不使没有使用它的包失效。

成功发布时，指纹记录与 wheel 在同一个提交写入 `wheels/.build-records/`。下一次规划通过稀疏检出只读取记录，再以同一提交的 Git 树验证 wheel 路径和 blob 身份，不下载 wheel。记录损坏、网络或权限错误直接报错；缺少记录、wheel 被删除或内容已替换时重新构建。历史产物没有指纹，不能仅凭文件名认定构建输入相同，需要首次构建后才能参与增量跳过。

Actions 摘要显示待构建、已发布和上游不支持的目标数；`build-plan.json` 的 `already-published` 条目记录具体跳过目标。所有目标命中时不创建编译任务，仍按需补发索引同步事件。

规划会先校验全部 recipe，再根据 Git diff 选择目标。新增或重命名的 TOML 可以被识别，不依赖 GitHub push 事件里可能被截断的文件列表。

规划任务采用浅克隆，并显式获取推送前的提交，只读取比较前后源码树所需的对象。

## 手动构建

单包、单 Python 版本并覆盖 Git tag：

```sh
gh workflow run build-wheels.yml \
  -f recipes=pydantic-core \
  -f python_versions=3.10 \
  -f version_tag=v2.41.5
```

多包、多版本并行，仅保留 Actions 产物：

```sh
gh workflow run build-wheels.yml \
  -f recipes=pydantic-core,xxhash \
  -f python_versions=3.10,3.13 \
  -f publication=build
```

`recipes` 可为逗号分隔的包名或 `all`；`python_versions=recipe` 使用各 TOML 的默认集合，也可填写无空格的版本列表。覆盖 `version_tag` 时必须只选择一个使用 Git 源的包，可以同时构建该包的多个 Python 版本。PyPI 源通过修改 TOML 中的 `pypi-version` 升级，不接受 Git ref 覆盖。

`publication=publish` 是默认值，使用 `rebuild=changed` 跳过已验证的相同目标，提交新 wheel 并触发现有索引发布链路。需要重新编译已发布目标时显式设置 `-f rebuild=force`，仍只作用于选中的包和 Python 版本。

`publication=build` 总是构建所选目标，确保提供完整的 Actions artifacts，不更新 `wheels` 或发布记录，也不触发索引发布。临时仓库入口和本地 `plan-local` 同样保持完整构建计划。GitHub 失败任务可使用“Re-run failed jobs”保留同一次运行的成功产物；只有整次计划校验并发布成功后才会写入发布记录。

指纹标识仓库声明的构建输入；`ubuntu-latest`、系统工具以及上游未锁定的构建依赖仍可能随时间变化。如需刷新这些外部环境，使用 `rebuild=force`，不会凭旧产物名称推断环境已经更新。

`manual-wheel-build.yml` 保留为临时仓库构建入口，用于尚未收录进 recipe 的源码；必须明确项目目录（根目录填写 `.`），该目录必须有显式声明受支持后端的 `pyproject.toml`，不再递归猜测子项目；原来的 `wheel-*.yml` 包专用入口由 `build-wheels.yml` 替代。

## Artifact 隔离

artifact 名称由目标规划模块统一生成，三个构建器使用同一字段，收集阶段使用同一前缀：

```text
pydantic-core-android-3.10-wheel-arm64-v8a
pydantic-core-android-3.10-wheel-x86_64
pydantic-core-android-3.13-wheel-arm64-v8a
pydantic-core-android-3.13-wheel-x86_64
```

每个 artifact 包含一个 wheel 和 `manifest.json`，记录明确的源码类型、固定 Git SHA 或 sdist 版本与 SHA-256、目标信息及 wheel SHA-256。统一发布任务按 `build-plan.json` 核验完整产物集合、来源、哈希、文件名、平台、Python/ABI 标签、METADATA、WHEEL 内部标签和每个 RECORD 文件的哈希及大小，再用一个提交更新 `wheels` 分支。缺失、多余、错配或冲突的产物会使发布失败；多个目标生成同名且内容不同的 wheel 时也直接失败。

编译任务只有读权限，写入仓库和触发索引的权限仅授予汇总发布任务。发布后核对下游已发布的 wheels 树，仅在尚未同步时通知；`publication=build` 始终跳过发布。

## 本地校验

```sh
uv sync --locked --no-install-project
uv run python -B -m scripts.recipes validate
python -B -m unittest discover -s scripts/recipes/tests -v
uv run python -B -m scripts.pipeline plan-local \
  --recipes pydantic-core,xxhash \
  --python-versions 3.10,3.13 \
  --publication build \
  --github-output /tmp/wheel-matrix-output
```

本地规划使用与 Actions 相同的规划器，输出固定源码的独立任务 JSON，不在本地编译。需要已登录的 `gh` 读取上游公开源码信息。`scripts.recipes` 仅保留配置校验入口。运行这些命令的 Python 至少为 3.11，且不需要安装 TOML 第三方库。

整体模块边界、工作流职责与测试方式见 [构建流水线](pipeline.md)。
