# Py Extra Wheels

[English](README.md) | **简体中文**

Py Extra Wheels 为 Android 构建和托管预编译的 Python wheel，可用于 [Chaquopy](https://chaquo.com/chaquopy/) Python SDK，或将 Python 嵌入 Android 应用。嵌入方式详见 [在 Android 上使用 Python](https://docs.python.org/zh-cn/3.14/using/android.html#using-python-on-android)。

包索引：[https://ririv.github.io/py-extra-wheels/](https://ririv.github.io/py-extra-wheels/)

旧索引 [https://ririv.github.io/android-wheels/](https://ririv.github.io/android-wheels/) 继续可用。内容发布后通过事件同步，不做定时扫描。

## 使用方法

使用 `pip` 安装包，将本仓库作为额外包索引。把 `<package-name>` 替换为要安装的包名：

```bash
pip install --extra-index-url https://ririv.github.io/py-extra-wheels/ <package-name>
```

例如，安装 `zstandard`：

```bash
pip install --extra-index-url https://ririv.github.io/py-extra-wheels/ zstandard
```

### requirements.txt

也可以将索引和包名写入 `requirements.txt`：

```txt
--extra-index-url https://ririv.github.io/py-extra-wheels/

zstandard
xxhash
```

## 构建目标

- Python：**3.10–3.15**，其中 Python 3.10 为额外保留的兼容目标。
- Android API：默认 **24**。
- ABI：**`arm64-v8a`** 和 **`x86_64`**。

多数 recipe 默认请求全部六个 Python 版本；当前科学计算依赖栈因依赖要求显式选择 Python 3.12–3.15。用于构建未收录源码仓库的独立工作流默认使用 Python **3.13**。每个包及其构建依赖都必须支持所选版本。上游声明或已经验证的不兼容组合会记录原因并跳过；其他构建失败会直接报告。构建使用 recipe 中选定的包版本，不会自动选择较早发行版。

**2026-10-29** 验证的矩阵包含 **54 个包、624 个 wheel**，其中 Python 3.15 覆盖 **52 个包、104 个 wheel**。这些结果涵盖构建和静态检查，尚未进行设备或模拟器运行测试。详见 [完整矩阵与验证记录](docs/python-matrix.md)。

**2026-10-31**，NumPy 2.5.4 新增 **Python 3.12–3.15 的 8 个 Android wheel**。其 Python 3.13 产物已在 Android API 34 模拟器上通过双 ABI 数值冒烟测试。详见 [NumPy 与 OpenBLAS 验证记录](docs/numpy.md)。

ContourPy 1.4.0、pandas 3.1.0、Matplotlib 3.11.2 新增 **Python 3.12–3.15 的 24 个 wheel**。最终 Python 3.13 依赖栈在 Android API 34 双 ABI 上通过运行检查，包括 DataFrame 操作和无界面的 PNG/SVG/PDF 导出。详见 [科学计算依赖栈验证](docs/scientific.md)。

Shapely 2.2.0、DuckDB 2.0.0、polars-runtime-32 2.1.0、Thrift 0.25.0、SciPy 1.18.1 新增 **44 个 wheel**，recipe 总数达到 **63 个包**。SciPy 面向 ARM64 和 API 26；其他四个包面向双 ABI 和 API 24。Python 覆盖范围遵循上游及依赖要求。Python 3.13 数据套件在双 ABI 上通过，SciPy 在 ARM64 上通过数值检查。Polars 不包含桌面剪贴板功能。详见 [数据处理与科学计算扩展](docs/data-processing.md)。

scikit-learn 1.9.1、PyArrow 26.0.0、psycopg2-binary 2.9.13 新增 **24 个 wheel**，recipe 总数达到 **66 个包**，三个包均要求 API 26。scikit-learn 覆盖 Python 3.12–3.15 ARM64，PyArrow 覆盖 3.12–3.15 双 ABI，psycopg2-binary 覆盖 3.10–3.15 双 ABI。机器学习与数据连接套件在 Android API 34 上通过 Python 3.13–3.15 ARM64 检查；数据连接套件还通过 Python 3.13 x86_64 检查。详见 [机器学习、Arrow 与 PostgreSQL 验证](docs/analytics.md)。

## 构建与发布

GitHub Actions 执行统一流水线：

1. **规划**：读取 `recipes/*.toml`，选择受影响的包，固定 Git 提交或 PyPI 官方源码发行包，生成独立的包 × Python × ABI 构建目标。
2. **构建**：`build-wheels.yml` 调用 `build-wheel-batch.yml`，再通过 `build-android-wheel-dispatch.yml` 分派目标。各目标使用 maturin、cibuildwheel 或 Chaquopy 并行构建。
3. **发布**：核验计划内的完整产物集合、源码来源、哈希、wheel 标签及 ELF 对齐，然后以一个提交更新 `wheels` 分支。
4. **部署**：通过 `pages.yml` 发布 HTML 包索引，并通知兼容站点同步。通知失败可独立重试。

## 添加包

对于使用已支持构建后端的包，新增 `recipes/<package-name>.toml`，声明 Git 仓库和 ref 或固定版本的 PyPI 官方 sdist、项目目录、构建后端及 Android 目标。TOML 也支持配置外部环境、PEP 517 参数和原生依赖。新的构建后端需先接入相应构建器。

所有 recipe 共用统一工作流。recipe 变更后检查对应包；共享代码变更后检查全部 recipe。构建指纹一致且已发布 wheel 的 Git blob 匹配时跳过该目标，使用 `rebuild=force` 可强制重建所选目标。历史 wheel 需要先完成一次带记录的构建才能参与复用。

例如，构建两个 Python 版本，并将结果保留为 Actions 产物：

```sh
gh workflow run build-wheels.yml \
  -f recipes=pydantic-core \
  -f python_versions=3.10,3.14 \
  -f publication=build
```

`publication=build` 仅保留产物；默认的 `publication=publish` 还会发布已校验的 wheel 并更新包索引。配置与手动构建选项见 [Recipe 指南](docs/recipes.md)。

## 打包规范

wheel 文件名遵循 [PEP 427](https://peps.python.org/pep-0427/#recommended-installer-features)，通常采用 `{distribution}-{version}-{python tag}-{abi tag}-{platform tag}.whl` 格式。

| 规范 | 用途 |
| --- | --- |
| [PEP 427](https://peps.python.org/pep-0427/) | wheel 文件格式与文件名。 |
| [PEP 503](https://peps.python.org/pep-0503/) | HTML 包索引及规范化包名。 |
| [PEP 738](https://peps.python.org/pep-0738/#packaging) | Android 平台支持及 wheel 标签。 |

当前包索引提供 HTML 页面。

## 详细文档

以下详细指南以中文维护：

- [Recipe 与并行构建](docs/recipes.md)
- [流水线架构与开发检查](docs/pipeline.md)
- [Android Python 运行库与构建路由](docs/python-builds.md)
- [Python 完整矩阵与验证](docs/python-matrix.md)
- [科学计算依赖栈与无界面绘图](docs/scientific.md)
- [包收录口径与原生扩展排名审计](docs/package-selection.md)
- [索引迁移与兼容地址](docs/index-migration.md)
