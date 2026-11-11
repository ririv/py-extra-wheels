# 构建流水线

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `scripts/recipes` | 严格解析 TOML、选择受影响包、解析手动选择 |
| `scripts/pipeline/model.py` | 固定源码、构建目标、构建器枚举与计划 |
| `scripts/pipeline/planning.py` | 把 Actions 事件转成完整目标矩阵 |
| `scripts/pipeline/fingerprint.py` | 按构建器、本地导入模块和声明配置计算目标指纹 |
| `scripts/pipeline/incremental.py` | 根据发布记录移除可复用目标，提供强制重建模式 |
| `scripts/pipeline/published.py` | 读取 Git 树及发布记录，将新记录绑定到实际 wheel blob |
| `scripts/pipeline/source.py` | 解析 Git SHA 或官方 sdist，核验明确目录的后端及发行元数据 |
| `scripts/pipeline/sdists.py` | 校验源码包 SHA-256，安全解包并保留上游发行内容 |
| `scripts/pipeline/cargo_env.py` | 组合 cibuildwheel 所需的 Cargo 环境配置，保留已有配置 |
| `scripts/build` | Android 运行库、maturin/Chaquopy 构建与 wheel 修复 |
| `scripts/build/chaquopy_adapter.py` | 组合旧版目标环境、原生依赖与共用 wheel 修复 |
| `scripts/build/pep517_settings.py` | 将相同构建参数传入依赖发现和 wheel 构建 |
| `scripts/build/native_prepare.py` | 在后端依赖探测之前编译或校验目标原生库 |
| `scripts/build/native_prefix.py` | 核对并复制固定 SDK 到不同构建器的目标 Python 前缀 |
| `scripts/build/native_cache.py` | 生成与 Python 无关的 SDK 缓存身份，核对文件、权限和符号链接 |
| `scripts/build/pg_config.py` | 提供可迁移的宿主查询工具，定位交叉编译的 libpq |
| `scripts/build/cibuildwheel_prepare.py` | 核对并安装已编译依赖，组合宿主工具与 Cargo 环境 |
| `scripts/build/native_model.py` | 原生依赖名、互斥构建系统和固定来源数据 |
| `scripts/build/native_libraries.py` | 验证并直接编译固定原生依赖，不改源码 |
| `scripts/build/link_flags.py` | 组合固定页大小链接参数，保留项目 Rust 设置 |
| `scripts/build/wheel_alignment.py` | 核验 ELF 加载段的 16 KB 对齐和地址关系 |
| `scripts/build/wheel_native.py` | 将依赖来源和许可证写入 wheel 并更新 RECORD |
| `scripts/build/requirements.py` | 读取选定项目的宿主构建依赖声明 |
| `scripts/build/wheel_abi.py` | 根据实际 ELF 的版本专用 libpython 依赖约束 wheel 兼容标签 |
| `scripts/pipeline/artifacts.py` | 记录并核验完整产物集合、来源与文件哈希 |
| `scripts/pipeline/wheel.py` | 核验 wheel 文件名、WHEEL 标签和全部 RECORD 条目 |
| `scripts/pipeline/notifications.py` | 根据下游实际发布状态发出可重试的同步事件 |
| `scripts/tooling.py` | 从项目配置读取固定工具版本并生成依赖清单 |
| `scripts/pipeline/publish.py` | 将完整构建汇总为一个 Git 提交 |
| `scripts/index` | 批量读取 Git 元数据，生成确定的 HTML 索引 |

模块通过明确的数据和文件交互，支持声明式单行环境、PEP 517 字符串或字符串数组参数和固定原生依赖，不提供任意 Shell hook 或源码补丁入口。

## Actions 职责

主仓库保留九个工作流：recipe 入口、有界矩阵批次、单目标 Android 构建器、临时仓库入口、索引部署、统一 CI，以及按需运行的 NumPy 验证、科学计算依赖栈验证和产物重打包。两个构建入口共享源码准备与发布 composite action；运行验证和重打包不会重复编译原生扩展。

构建计划记录在 `build-plan` artifact 内的 `build-plan.json`，源码使用 `GitSource | SdistSource` 和类型建模。Git ref 在规划时解析为完整 SHA；PyPI 源明确固定版本、地址、文件名和 SHA-256。不同 Python 和 ABI 使用同一固定来源，构建器在编译前重新校验 checkout 或源码包哈希、后端、`PKG-INFO`。sdist 的产物版本还必须等于所选发行版本。临时仓库入口未填写 ref 时，显式读取上游默认分支后固定其提交。

每个目标使用独立 runner 编译。后端与 Python 版本决定 maturin、cibuildwheel 或 Chaquopy 路由，不改变原有交叉编译能力。驱动 Python 与目标 Python 分开设置。cibuildwheel 使用仓库独立配置文件，避免上游针对其他平台的发布选项影响本流水线；不改写上游源码或构建元数据。maturin 产物显式链接目标 Python，与 cibuildwheel 共用 auditwheel 修复步骤来打包外部运行库；实际依赖版本专用 `libpython3.x.so` 时，把过宽的 `abi3` 标签收窄为该解释器版本，同时更新 WHEEL 与 RECORD。

cibuildwheel 与 Chaquopy 的原生 SDK 按 ABI、API、固定依赖、工具链和构建实现缓存，与 Python 版本分离；缓存命中后完整校验文件哈希、权限和符号链接，校验失败直接终止。SDK 成功后立即保存，使后续 Python 绑定构建失败不会丢弃已完成的 SDK。两个构建器通过同一安装模块，将 SDK 复制到各自目标 Python 前缀。

规划先按 Git diff 选择候选包，再核对目标指纹和 `wheels/.build-records/` 中的成功发布记录。Git 树与记录来自同一提交，读取记录使用批量 Git 查询，不下载 wheel。只有指纹相同且 wheel blob 仍存在时才从矩阵移除。构建目标沿用原有并行矩阵；新增 Python/ABI 不会改变其他目标的指纹。构建时重新核对当前代码与计划指纹，产物清单记录该指纹，汇总再次检查。历史计划没有指纹时仍可验证和重打包，但不会被追认为可复用的新构建。详见 [构建范围与强制重建](recipes.md#自动选择构建范围)。

所有待构建目标成功后才进入汇总发布。发布会验证每个预期 artifact，缺少产物、源码错配、哈希不符、目标标签错误、同名内容冲突都会失败。校验通过后以一个提交同时更新 wheel 与对应指纹记录，保留已有文件。不同工作流运行并发提交时，仅在远端确实前进后重新组合提交，最多三次；网络或权限失败直接报告。

`publication=build` 不进入发布。完整产物与远端相同时不创建新提交；发布步骤仍核对下游状态，只在尚未同步时补发事件。正常构建的仓库写权限和 Actions 触发权限只授予汇总发布任务。

## 索引与兼容地址

Pages 将代码与 wheel 元数据分目录检出，使用稀疏检出读取 Git 树和历史，不检出 wheel 二进制。索引元数据使用两次批量 Git 查询：当前文件列表和全部 wheel 修改日期；另读取一次 Git 树 SHA 写入状态标记，不再对每个 wheel 重复查询。保留提交历史是为了显示历史修改日期，稀疏克隆避免拉取 wheel 文件内容。删除或不存在的文件不会出现在新索引中；日期缺失或没有 wheel 时直接失败。

主索引的 `index-state.json` 记录已发布的 wheels Git 树；兼容索引的 `sync-state.json` 记录已同步的 gh-pages Git 树。发送端在事件处理时读取下游公开状态并比较：一致就结束，不一致就通知。通知失败后状态仍不一致，因此可以重跑发布或部署任务来补发，不依赖本次是否产生新提交。

首次尚无状态文件的 HTTP 404 被明确建模为未发布；其他 HTTP、网络和格式错误直接失败。每次事件只读取一次下游状态，不做轮询或定时扫描。不同构建运行的索引通知可合并，部署始终读取最新 wheels。详见 [地址迁移](index-migration.md)。

## 开发检查

工具版本统一保存在 `pyproject.toml`：依赖组由 `uv.lock` 锁定，`scripts.tooling` 按组生成构建器所需的 pip 清单；驱动 Python、Chaquopy 提交、uv 和 actionlint 由同一份工具配置提供。CI 在 Python 3.11 与 3.15 上运行组件测试，对整个 `scripts/` 检查 Ruff，并校验 TOML 与 actionlint。构建器以 `python -m scripts.build.<模块>` 运行，只接受明确的 `PROJECT_DIR`，不再保留平铺导入与目录缺省行为。

```sh
uv sync --locked --no-install-project
uv run python -B -m scripts.recipes validate
uv run ruff check scripts
uv run ruff format --check scripts
uv run python -B -m unittest discover -s scripts/build/tests -v
uv run python -B -m unittest discover -s scripts/recipes/tests -v
uv run python -B -m unittest discover -s scripts/pipeline/tests -v
uv run python -B -m unittest discover -s scripts/index/tests -v
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.7
```

测试覆盖目标路由、固定 SHA、sdist 哈希与发行元数据、两种来源的计划往返、产物缺失与篡改、稳定 ABI、Cargo 配置组合、汇总提交、并发更新重试、无变化不发布，以及不检出 wheel 时的索引生成。测试、字节码、发布与通知逻辑变化不触发 native 构建；这些变化由组件测试和工作流检查验证。编译、源码准备、目标规划和公共构建配置变化会检查全部 recipe，再按目标指纹筛选；实际影响所有目标的修改仍需全量重建。

历史排障笔记位于 `docs/archive/`，不作为当前构建配置或使用说明。
