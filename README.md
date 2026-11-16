# hc32-data-tools

小华半导体 HC32 全系列芯片数据流水线：从 SDK 文档、SVD 与小华官网提取数据，
生成 [ch32-data](https://github.com/ch32-rs/ch32-data) 风格的芯片 YAML，并进一步生成
[`hc32-metapac`](https://github.com/hc32-rs/hc32-metapac)（PAC + 元数据 crate），供 HAL 构建使用。

| 仓库 | 角色 |
|---|---|
| [hc32-ddl-data](https://github.com/hc32-rs/hc32-ddl-data) | DDL 系列数据仓库（20 个老驱动系列，含 `svd/` 归档） |
| [hc32-ll-data](https://github.com/hc32-rs/hc32-ll-data) | LL 系列数据仓库（27 个系列，含 `svd/` 归档） |
| **hc32-data-tools**（本仓库） | 提取/转换脚本（Python，`scripts/`）+ 生成链（Rust，四个 crate） |
| [hc32-metapac](https://github.com/hc32-rs/hc32-metapac) | 生成产物：全系列 PAC + 元数据 crate（自动生成，勿手工修改） |

寄存器处理使用 [embassy-rs/chiptool](https://github.com/embassy-rs/chiptool)
（HC32 为 ARM Cortex-M，无需 ch32-rs fork 的 QingKe 扩展；Rust 生成链锁定
rev [`e890244`](https://github.com/embassy-rs/chiptool/tree/e8902440a6eee471a9b14b38ee1cd2e526d2c495)）。

## 流水线总览

**① Python 提取链（`scripts/`）：SDK / 官网 / SVD → CSV → YAML**

```
xh_{ddl,ll}_sdk ──extract_{ddl,ll}_csv.py──▶ 两侧 data/csv/{chips,interrupts,dma,peripherals,memory}.csv
小华官网 ────────hdsc_fetch_products.py───▶ 两侧 data/csv/website_chips.csv
两仓库 svd/ ─────chiptool_registers.py────▶ 两侧 data/registers/<kind>_<version>.yaml + register_versions.csv
                └─svd_to_registers_csv.py─▶ 两侧 data/csv/registers.csv（核对基准）+ svd_meta.csv
以上全部 CSV ───────csv_to_yaml.py────────▶ 两侧 data/{chips,family,interrupts,dma,memory}/*.yaml
data/{family,interrupts} ─patch_svd_interrupts.py─▶ 两侧 svd/（中断节点回流，改前登记 svd/CHANGELOG.md）
```

**② Rust 生成链（四个 crate）：YAML → metapac**

```
hc32-{ddl,ll}-data/data ─▶ hc32-data-gen ─▶ build/data/{chips,registers}/*.json
                               │
                               ▼
                          hc32-metapac-gen ─▶ build/hc32-metapac/
                                                （nightly CI 强推到 hc32-rs/hc32-metapac）
```

## 目录布局

本仓库须与数据仓库、SDK 放在同一父目录下（脚本/生成链均按自身位置定位工作区，
如 `scripts/*.py` 内为 `parents[2]`）：

```
hc32-rs-workspace/
├── hc32-data-tools/     ← 本仓库
├── hc32-ddl-data/       ← DDL 系列数据仓库
├── hc32-ll-data/        ← LL 系列数据仓库
├── xh_ddl_sdk/          ← 小华 DDL SDK（含 documents/DS_*.pdf、RM_*.pdf，只读）
├── xh_ll_sdk/           ← 小华 LL SDK（只读）
└── build/               ← Python 链运行期缓存（chiptool 原始输出、修补 SVD），可随时删除
```

仓库内部（`build/` 为 Rust 生成链输出，同样可随时删除重建）：

```
hc32-data-tools/
├── scripts/             ← 提取/转换 Python 脚本与 CSV_SCHEMA.md
├── hc32-data-serde/     ← 芯片/外设 YAML 的 serde 类型（Rust）
├── hc32-data-gen/       ← 双仓库 YAML → build/data/*.json（Rust）
├── hc32-data-macros/    ← EnumDebug derive（生成元数据用）
├── hc32-metapac-gen/    ← build/data → build/hc32-metapac（PAC 生成，Rust）
├── d                    ← 工作流入口脚本（gen / ci / test-metapac）
└── README.md
```

## 使用

```bash
# 依赖：python3 + pypdf/pyyaml；Rust stable + chiptool（见下）
python3 scripts/extract_ddl_csv.py         # 1. DDL SDK → CSV
python3 scripts/extract_ll_csv.py          # 2. LL SDK → CSV
python3 scripts/hdsc_fetch_products.py     # 3. 官网型号 → website_chips.csv
python3 scripts/chiptool_registers.py      # 4. SVD → data/registers/（chiptool）
python3 scripts/svd_to_registers_csv.py    #    （核对基准 registers.csv + svd_meta.csv）
python3 scripts/csv_to_yaml.py             # 5. CSV → chips/family/interrupts/dma/memory（末尾自动校验）
python3 scripts/patch_svd_interrupts.py    # 6. family+interrupts YAML → SVD 注入/替换 <interrupt>（--dry-run 预览；改前登记 CHANGELOG）
./d gen                                    # 7. 双仓库 YAML → build/data + build/hc32-metapac（Rust 生成链）
./d ci                                     # 8. 生成 + M0+/M4F 构建与 host 元数据测试
```

辅助/审计脚本（`scripts/`）：

- `chiptool_trial.py`：对全部 SVD 重跑 chiptool 试验提取（输出到临时目录）；
- `compare_chiptool.py`：chiptool 输出 vs `registers.csv` 的差异报告。

chiptool 安装（GitHub 直连受限时先 clone 再本地安装）：

```bash
git clone https://github.com/embassy-rs/chiptool /tmp/chiptool-src
cargo install --path /tmp/chiptool-src --locked
```

## Python 提取链：各 YAML 的生成方式

### `data/registers/<kind>_<version>.yaml`（寄存器块）

> 以下描述**当前生成链**。外设功能版本的新命名还在文档评审阶段，尚未接入生成器。
> 拟议文件名为 `<kind>_ddl_vN[_M].rs` / `<kind>_ll_vN[_M].rs`，DDL、LL 独立编号，
> 同档不同布局使用数字后缀。完整外设名恢复 I2C/I2S、TMR0/2/4 等被旧 kind 截断的名称。
> 规则与范围见 [DDL 版本说明](../hc32-ddl-data/docs/layout-versions.md)和
> [LL 版本说明](../hc32-ll-data/docs/layout-versions.md)。LL README 的 M4 扩展项本次排除。

两仓库的 `docs/peripheral-version-map.json` 显式分配目标版本，
`docs/peripheral-versions.csv` 列出系列/实例、当前来源及拟议目标文件。
表中 `current_version` 是各数据仓库的版本，**不是**下游 `version_remaps` 临时分配的合并版本。
使用下面的独立文档导出命令（Python 3 + PyYAML），不运行 PAC 生成，不修改 `data/` 或 SVD：

```sh
# 从工作区根目录运行
python3 hc32-data-tools/scripts/export_peripheral_versions.py
python3 hc32-data-tools/scripts/export_peripheral_versions.py --check
```

导出器同时更新两个 CSV、版本总览及外设专题的标记表格。它不自动编号；
来源 SHA-256、实例覆盖、唯一目标、排除项、编号连续性或文档引用不一致时会报错。
两仓库都通过校验后才写出。`--check` 只读，并检查产物是否需要更新。
相同档位的变体附带相对基础布局的差异提示；没有检测出寄存器坐标/内联字段差异也不自动合并。
文档及对照表确认后，才迁移数据合并与 metapac 生成链中的文件名、模块和元数据引用。

- **来源**：两仓库 `svd/*.svd`（来源为 SDK 内 SVD）。行尾由各仓库 `.gitattributes`
  统一管控：仓库内与检出均为 LF。
- **工具**：`chiptool_registers.py` = chiptool `extract-all` + 聚合。
- **过程**：
  1. 14 个老代 SVD 的 `<addressBlock>` 缺 `<usage>` 标签（严格 svd-parser 拒绝），
     脚本自动复制补丁副本到 `build/svd_fixed/`（原始 SVD 不动）再解析；
  2. chiptool 把每个 SVD 拆成每外设一个 IR YAML（`block/<外设>` + `fieldset/<寄存器>` 段，
     含 access/bit_size/数组），缓存在 `build/chiptool/<仓库>/<系列>/`；
  3. 聚合器把全部系列的块按 `(kind, 布局签名)` 去重合并成
     `<kind>_<version>.yaml`：一个布局若覆盖该 kind 出现的**全部**系列 → `common`，
     否则按首次出现序 `v1、v2…`；布局代表取系列名排序最前者，`derivedFrom`
     外设不单独产出（与父实例同布局）。
- **产物**：`data/registers/<kind>_<version>.yaml` + `data/csv/register_versions.csv`
  （`series,instance,kind,version,block` 实例→版本映射，family YAML 引用它）。
- **核对**：`svd_to_registers_csv.py` 产出实例级 `registers.csv` 作为核对基准；
  与 chiptool 输出做过全量对比（寄存器/字段 100% 一致，`compare_chiptool.py` 可复验）。

### `data/chips/<PART>.yaml`（型号）

- **来源**：各 SDK `documents/DS_*.pdf` 的“型号功能对比表/订货信息”（pypdf 解析），
  不足处由**小华官网**（www.xhsc.com.cn 产品线页）补齐。
- **工具**：`extract_ddl_csv.py` / `extract_ll_csv.py`（DS → `chips.csv`），
  `hdsc_fetch_products.py`（官网 → `website_chips.csv`）。
- **合并规则**：DS 行优先；website 行补缺字段、补缺型号（F120/M120 无 DS，
  全靠官网行）；website 系列名经 `SERIES_ALIAS` 映射（HC32L072→HC32L07x 等）。
- **内容**：name / packages / `include_memory` / `memory_sizes` / docs /
  cores（`include_peripherals` + `include_interrupts` + `include_dma_channels`）/
  `_raw`（原始 CSV 字段留档）。CPU 类型来自 `svd_meta.csv`（SVD `<cpu>`）。

### `data/family/<series>.yaml`（外设实例）

- **来源**：SVD（实例名+基地址）+ 设备头（FCG/PWC 时钟门控位）+ IRQn 枚举。
- **工具**：`extract_ddl_csv.py` / `extract_ll_csv.py` 产出 `peripherals.csv`
  （`series,instance,kind,base_address,bus,fcg_reg,fcg_field,interrupts`），
  `csv_to_yaml.py` 生成 YAML 并从 `register_versions.csv` 挂上 `registers` 引用。
- **注意**：引脚复用（pins）暂缺，待从 DS GPIO 章节补齐（文件头有 TODO 注释）。

### `data/interrupts/<series>.yaml`（中断向量）

- **来源**：设备头 `IRQn_Type` 枚举（多数 HC32 SVD 的中断节点缺失/为 0，不可用）。
  负数内核中断（NMI/HardFault 等）不列入。
- **回流**：`patch_svd_interrupts.py` 用它 + family YAML 的外设 `interrupts:` 段把
  中断节点补回两仓库 SVD（文本级插入，最小 diff；名字查不到编号的条目跳过并报告；
  LL 共享 INTxxx 重定向向量系列整系列跳过）。修改前须登记 `svd/CHANGELOG.md`。

### `data/dma/<series>_<DMA>.yaml`（DMA 触发源）

- **来源**：驱动头（DDL `DMA_TRISRC_*` / LL `EVT_SRC_*`）+ RM 触发源表核对。
- **语义按仓库不同**（文件头注释已写明）：
  - DDL：值 = `DMAC_CONFAx.TRI_SEL` 全局触发源索引，请求与物理通道为运行时软件路由；
  - LL：值 = 设备头 `EVT_SRC_*` 9 位事件号，写 `AOS->DMAx_TRGSELy`。

### `data/memory/<series>_F<n>S<n>.yaml`（内存布局）

- **来源**：设备头 `FLASH_BASE`/`SRAM_BASE` `#define` 或 SDK 链接脚本
  （`memory.csv` 的 notes 列记录出处）；Flash/SRAM 大小按 chips.csv 的
  型号组合去重成 `<系列>_F<大小>S<大小>.yaml`。

## Rust 生成链：四个 crate

从 [ch32-data](https://github.com/ch32-rs/ch32-data) 移植，`./d gen` 一键运行：

- **`hc32-data-serde`**：`data/chips/*.yaml` 顶层结构的 serde 类型。与 ch32 版差异：
  无 subfamily/NV（非易失描述符）/ram-code 内存选项；`rcc` 只有 `enable`
  （PWC/FCG 时钟门控位）；内存区命名 FLASH/SRAM；request 号为 `u16`。
  含双仓库全部 chips YAML 的解析往返测试（`cargo test -p hc32-data-serde`）。
- **`hc32-data-gen`**：读**两个**数据仓库根（默认
  `ddl=../hc32-ddl-data/data:ll=../hc32-ll-data/data`，环境变量
  `HC32_DATA_ROOTS` 可覆盖），解析 include 链后产出 `build/data/*.json`。要点：
  - 两仓库寄存器版本号各自独立编号；同名不同布局时按 DDL→LL 顺序确定性地
    改派空闲版本号（`version_remaps`），芯片侧重定向，跨仓库布局一致则去重；
  - DMA 触发按 HC32 语义挂 `request` 号到外设（DDL=TRI_SEL 全局索引 /
    LL=EVT_SRC 事件号），不推断物理通道；信号名按“忽略下划线的最长前缀”
    匹配外设实例（`LP_UART0_RX`→`LPUART0`、`ADCJQR`→`ADC`）；
  - IRQn 同号别名/共享重定向向量按枚举顺序保留首个主名；
  - 无内存布局的芯片跳过并告警。
- **`hc32-data-macros`**：`EnumDebug` derive（元数据 IR 的 Debug 输出用）。
- **`hc32-metapac-gen`**：ARM-only（HC32 中断号已是 CMSIS 0 基，无需 ch32 的
  -16 调整）。同一芯片内同一外设 kind 允许多版本（MODE 拆分的定时器），
  模块名统一 `<kind>_<version>`。每芯片生成 `pac.rs`（寄存器访问）、
  `metadata.rs`（`METADATA` 元数据）、`device.x`（中断向量）、`memory_x/`
  （memory.x 链接脚本素材）。
  chiptool 渲染结果是未排版的 proc-macro2 token 流，`./d gen` 末尾用
  `rustfmt --edition 2024`（生成 crate 为 edition 2024）对
  `build/hc32-metapac/src` 全量排版后产物才算定稿；本地与 nightly CI
  都走这一步，保证两边输出一致。

生成的 `build/hc32-metapac` 即 `hc32-metapac` crate（当前 218 个芯片，feature 为
完整型号小写，如 `hc32f003c4pa`）。可用 feature：`pac`（默认）、`metadata`、`rt`、
`memory-x`。用法详见其 [README](https://github.com/hc32-rs/hc32-metapac)。

生成验证：`./d ci` = `./d gen` + M0+（`hc32f003c4pa@thumbv6m`）与
M4F（`hc32f460jcta@thumbv7em`）构建/doc + host 元数据测试。全量 218 芯片
`cargo check` 100% 通过；重跑生成字节级一致。

## 数据缺口与降级规则

提取侧缺口（补齐后生成侧自动恢复）：

1. **pins 引脚复用**未提取（DS GPIO 章节表格解析），family YAML 留 TODO；
2. **SVD MODE 拆分实例**（`TIM0_MODE0/1/23` vs 设备头 `TIM0`）映射待在
  csv_to_yaml.py 里建立——建立前这些实例在生成侧按缺版本降级；
3. **F120/M120** 无 DS，官网行为主源；**F155** DS 版式特殊；**A472** 同缺内存
  布局——生成侧跳过；
4. 寄存器描述为 SVD 原文（自动生成的占位文本，质量有限）；
5. HC32 SVD 无 enumeratedValues，`enum/` 段暂为空；后续可用
  `chiptool transform`（`MakeRegisterArray`/`MergeEnums`）收敛寄存器版本号；
6. `core.dma_channels`（DMA 控制器通道清单）未生成，待补每系列通道数表；
7. 各系列提取细节见各数据仓库 `data/csv/EXTRACTION_NOTES.md`。

生成侧降级（`./d gen` 日志逐条告警，当前数量）：

| 现象 | 数量 | 处理 |
|---|---|---|
| 外设挂不上寄存器版本（SVD/设备头实例名不一致，见缺口 2） | 367 | 降级为纯地址外设 |
| 芯片无内存布局（见缺口 3） | 16 | 跳过不生成 |
| IRQn 同号别名/共享向量（如 `INT000`/`SWINT0`、`BTIM3_4_5`/`GTIM`） | 1192 | 保留枚举首个主名 |
| DMA 触发源无外设实例（`PORT_EIRQ`/`LVD`/`EVENT_PORT` 等端口系统事件） | 4362 | 跳过挂载 |

## CSV 列定义

统一列契约见 [scripts/CSV_SCHEMA.md](scripts/CSV_SCHEMA.md)。补充：

- `website_chips.csv` 与 `chips.csv` 同列头（source=website）；
- `registers.csv`（审计用）：`series,instance,kind,register,offset,description,
  field,bit_offset,bit_size,access,field_description`；
- `register_versions.csv`：`series,instance,kind,version,block`；
- `svd_meta.csv`：`series,cpu,device_name,peripheral_count`。

## CI（.github/workflows/）

- **build**（push/PR main）：`cargo check --workspace` + serde 往返测试（CI 内
  clone 两个数据仓库）+ `./d gen` + M0+/M4F 构建与 host 元数据测试；
- **metapac nightly**（push main）：重新生成并强推 `hc32-rs/hc32-metapac`
  （带 `hc32-data-tools-<commit>` 溯源 tag）。需要在仓库 Secrets 配置
  `METAPAC_TOKEN`（对 `hc32-rs/hc32-metapac` 有 Contents 写权限的 PAT，
  细节见 workflow 文件头注释）。
