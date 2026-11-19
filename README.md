# hc32-data-tools

小华半导体 HC32 全系列芯片数据流水线：从 SDK 文档、SVD 与小华官网提取数据，
生成 [ch32-data](https://github.com/ch32-rs/ch32-data) 风格的芯片 YAML，并进一步生成
[`hc32-metapac`](https://github.com/hc32-rs/hc32-metapac)（PAC + 元数据 crate），供 HAL 构建使用。

| 仓库 | 角色 |
|---|---|
| [hc32-ddl-data](https://github.com/hc32-rs/hc32-ddl-data) | DDL 系列数据仓库（20 个老驱动系列，含 `svd/` 归档） |
| [hc32-ll-data](https://github.com/hc32-rs/hc32-ll-data) | LL 系列数据仓库（26 个系列，含 `svd/` 归档；HC32F175 暂停支持） |
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
docs/ 两探针（路由×管脚）└─emit_pins 交集─▶ 两侧 data/pins/<series>_<PART>.yaml（chips YAML include_pins 引用）
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

寄存器 YAML 与 PAC 使用统一文件主干：`<kind>_ddl_vN[_M]` / `<kind>_ll_vN[_M]`。
DDL、LL 独立编号，同档不同布局使用数字后缀；内部主块使用完整外设名的大写形式。
规则见 [DDL 版本说明](../hc32-ddl-data/docs/layout-versions.md)和
[LL 版本说明](../hc32-ll-data/docs/layout-versions.md)。LL M4 扩展排除项保留原名。

两仓库的 `docs/peripheral-version-map.json` 显式分配目标版本，
`docs/peripheral-versions.csv` 列出系列/实例、当前来源及目标文件。
表中 `current_version` 包含 ddl/ll 命名空间；`proposed_version` 列为兼容评审表保留，值为不带命名空间的功能版本。
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
命名已接入上游提取、系列 YAML、JSON、PAC 模块和元数据。旧标识仅在映射的 legacy_source 中留档。

- **来源**：两仓库 `svd/*.svd`（来源为 SDK 内 SVD）。行尾由各仓库 `.gitattributes`
  统一管控：仓库内与检出均为 LF。
- **工具**：`chiptool_registers.py` = chiptool `extract-all` + 聚合 + 枚举补录。
- **过程**：
  1. 14 个老代 SVD 的 `<addressBlock>` 缺 `<usage>` 标签（严格 svd-parser 拒绝），
     脚本自动复制补丁副本到 `build/svd_fixed/`（原始 SVD 不动）再解析；
  2. chiptool 把每个 SVD 拆成每外设一个 IR YAML（`block/<外设>` + `fieldset/<寄存器>` 段，
     含 access/bit_size/数组），缓存在 `build/chiptool/<仓库>/<系列>/`；
  3. 按 `docs/peripheral-version-map.json` 的 active 映射输出规范名称和主块名，
     校验完整实例集合、布局签名与 SHA-256。未知或变化的布局必须先复核映射，不能自动分配编号。
     `derivedFrom` 继承其已确认来源，排除项维持原名称。所有校验完成后才写文件。
  4. `docs/register-enums/<source>.yaml` 将有 RM/SDK 依据的位域枚举注入 IR，
     注入发生在布局签名与 SHA-256 校验前。复核更新补录内容时同步更新映射中的
     `source_sha256`。注入校验字段、位宽、访问语义、变体名、数值及保留编码。
     `mode_note` 会进入生成文档，供 HAL 核对运行时约束。
- **TOE 枚举试点**：`usart_ddl_v1` 的 `CR1.TOE` 保留 `toe()/set_toe(u8)`，
  并新增 `toe_val()/set_toe_val(vals::Toe)`。双轨生成仅用于该兼容字段。
  元数据中的寄存器 IR 保留枚举引用。
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
- **MODE 拆分定时器**：物理实例（`TIM0`/`ATIM0`，承载地址/门控/中断）以
  `views:` 列表挂 SVD 视图（`TIM0_MODE0/1/23` 等原样保留 + kind/version/block，
  共 17 系列 × 60 实例）；单视图外设仍用 `registers:`，两者互斥。
- **中断引用归一**：外设 `interrupts:` 段的 `signal` 保留 SVD 信号名（如
  `ADC_1`、`EFM_SWINT17`），`interrupt` 归一到向量表（`data/interrupts/`）中的
  规范名，规则与 `hc32-data-gen` 的同号去重一致——同号组取设备头枚举首个名：
  1. 信号名即头枚举名（或其同号别名）→ 该号首名（如 F002 `GTIM`→`BTIM3_4_5`，
     M458 `EFM_SWINT17`→`EFM`）；
  2. 名字不在枚举 → 按 SVD `<interrupt><value>` 的 IRQ 号查同号组取首名
     （如 F472 `ADC_1`=174→`ADC1`、`CAN_1`=132→`CAN1_INT`；值与设备头
     全量核对无冲突）；共享 INTxxx 槽系列借此保留运行时路由语义
     （F472 `GLOBAL0_SWINT0`→`INT000`，K116 `EXTINT0_1`→`EXTINT00_EXTINT01`）；
  3. 名/号都查不到 → 在审计 CSV 保留信号，省略静态引用，并在 `csv_to_yaml.py` 的
     `UNRESOLVED_INTERRUPTS` 登记后单独报告（头枚举跳号等证据不足项，
     当前 6 条：F472/A472 PLA、F334/F336 PLAINT、K116/K118 LVD）；
     登记之外的新未解析项使脚本非零退出，防止悄悄带进生成链。
- **注意**：引脚复用（pins）按**型号×封装**粒度另存 `data/pins/`（见下节），
  family YAML 只承载实例物理事实，不混路由数据。

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

### `data/pins/<series>_<PART>.yaml`（引脚路由，型号粒度）

- **来源**：两个可复现探针的交集（hc32-data-tools `docs/` 下，均带断言自检）：
  `pin-mux-probe`（路由表：pad→peripheral/signal/selector，DS 引脚复用表）×
  `pin-probe`（封装管脚表：terminal→pad，DS 引脚功能表）——路由的 pad 必须
  在该型号**每个**封装的 GPIO 焊盘集合内。
- **多封装规则**（`Peripheral.pins` 无封装维度，防止丢失封装归属）：
  `emit_pins` 逐封装求交集后按型号归组——各封装路由**完全一致**时合并为
  型号级单文件（header 记录覆盖的封装，如 `LQFP48+QFN48`）；**分歧**或任一
  封装缺管脚表基准时 `csv_to_yaml.py` 显式退出（非静默合并/丢归属），
  待引入按封装的路由结构后再接入该型号。`hc32-data-gen` 拒绝
  `include_pins` 引用多于一个文件。
- **工具**：`csv_to_yaml.py` 的 `emit_pins`（`PIN_ROUTE_SOURCES` 登记接入系列；
  行为测试见 `scripts/test_emit_pins.py`）。当前仓库中的登记型号缺失探针 CSV
  或源表无数据行时，脚本直接退出。chips YAML 的 `cores[].include_pins` 只引用
  本轮验证并生成的文件，`hc32-data-gen` 按实例名
  合并到 `core.peripherals[].pins`（ch32-data 同构 `{pin, signal, func}`，
  `func`=FSEL/Px_sel 编码，模拟/固定功能/副功能三类暂不表达，证据留档探针 README）。
- **已接入**：HC32F460 样本 JCTA（LQFP48，539 条）/ PCTB（LQFP100，1048 条），
  F003 路由表已备但封装管脚表未提取，暂不接入。新系列补两探针数据 +
  登记 `PIN_ROUTE_SOURCES` 即自动进入生成链。

### `data/memory/<series>_F<n>S<n>.yaml`（内存布局）

- **来源**：SDK 链接脚本 MEMORY 区 + 各系列 RM 内存映射表
  （`memory.csv` 的 `sram_kb`/`extra_regions`/notes 列记录出处与实测值）。
  主 SRAM 大小取链接脚本实测：DS 标称 SRAM 可能并入独立保留区
  （如 A448 标称 68K = 主区 64K + RAMB 4K），照抄标称会合成不存在的连续大区。
  独立保留区（RAMB/RET_RAM，掉电保持 SRAM，如 0x200F0000 4K）逐区输出，
  与主区物理 bank 区分（M4 主区跨 0x20000000 是 SRAMH/SRAM0 两个 bank 的
  边界，非地址别名）。
- 型号组合去重成 `<系列>_F<大小>S<大小>.yaml`，文件名中的 S 值保留 DS 标称。

## Rust 生成链：四个 crate

从 [ch32-data](https://github.com/ch32-rs/ch32-data) 移植，`./d gen` 一键运行：

- **`hc32-data-serde`**：`data/chips/*.yaml` 顶层结构的 serde 类型。与 ch32 版差异：
  无 subfamily/NV（非易失描述符）/ram-code 内存选项；`rcc` 只有 `enable`
  （PWC/FCG 时钟门控位）；内存区命名 FLASH/SRAM（独立保留区沿用 SDK 链接脚本
  命名 RAMB/RET_RAM，MemoryRole 归入 Ram）；request 号为 `u16`。
  含双仓库全部 chips YAML 的解析往返测试（`cargo test -p hc32-data-serde`）。
- **`hc32-data-gen`**：读**两个**数据仓库根（默认
  `ddl=../hc32-ddl-data/data:ll=../hc32-ll-data/data`，环境变量
  `HC32_DATA_ROOTS` 可覆盖），解析 include 链后产出 `build/data/*.json`。要点：
  - 保留上游确定的规范名称，不再跨库改派版本号。重复文件名直接报错。
    名称解析按 `_ddl_` / `_ll_` 分隔，支持外设名下划线及数字变体后缀；排除项兼容旧名称。
  - DMA 触发按 HC32 语义挂 `request` 号到外设（DDL=TRI_SEL 全局索引 /
    LL=EVT_SRC 事件号），不推断物理通道；信号名按“忽略下划线的最长前缀”
    匹配外设实例（`LP_UART0_RX`→`LPUART0`、`ADCJQR`→`ADC`）；前缀失配时
    走**已确认的系列别名表**（仅 HC32A052/F052：`UART0/1`→`USART0/1`，
    `dmac.h` 触发注释与 RM TRI_SEL 表证实，信号名保留 SDK 原拼）；
    未匹配信号分类为系统事件/共享模板幽灵项（计数 INFO）与来源不明（逐条
    WARNING）；`core.dma_channels` 由 chips YAML 透传控制器通道清单；
  - IRQn 同号别名/共享重定向向量按枚举顺序保留首个主名；
  - 无内存布局的芯片跳过并告警。
- **`hc32-data-macros`**：`EnumDebug` derive（元数据 IR 的 Debug 输出用）。
- **`hc32-metapac-gen`**：ARM-only（HC32 中断号已是 CMSIS 0 基，无需 ch32 的
  -16 调整）。同一芯片内同一外设 kind 允许多版本（MODE 拆分的定时器），
  模块名统一 `<kind>_<version>`，其中 version 包含 ddl/ll 命名空间。
  全部登记的寄存器布局都生成，MODE 视图通过对应物理实例挂接。
  重生成前清除旧模块目录。每芯片生成 `pac.rs`（寄存器访问）、
  `metadata.rs`（`METADATA` 元数据）、`device.x`（中断向量）、`memory_x/`
  （memory.x 链接脚本素材）。
  chiptool 渲染结果是未排版的 proc-macro2 token 流，`./d gen` 末尾用
  `rustfmt --edition 2024`（生成 crate 为 edition 2024）对
  `build/hc32-metapac/src` 全量排版后产物才算定稿；本地与 nightly CI
  都走这一步，保证两边输出一致。

生成的 `build/hc32-metapac` 即 `hc32-metapac` crate（当前 214 个芯片，feature 为
完整型号小写，如 `hc32f003c4pa`）。可用 feature：`pac`（默认）、`metadata`、`rt`、
`memory-x`。用法详见其 [README](https://github.com/hc32-rs/hc32-metapac)。

生成验证：`./d ci` = `./d gen` + M0+（`hc32f003c4pa@thumbv6m`）与
M4F（`hc32f460jcta@thumbv7em`）构建/doc + host 元数据测试。全量 214 芯片
`cargo check` 100% 通过；重跑生成字节级一致。

## 数据缺口与降级规则

提取侧缺口（补齐后生成侧自动恢复）：

1. ~~**pins 引脚复用**未提取~~ **小样本已接入**（HC32F460 JCTA/PCTB，见
   `data/pins/` 一节）：路由×封装交集进入 chips YAML（`include_pins`）→
   chip JSON → metapac `PeripheralPin`（`pin/signal/func`）。剩余工作按
   外设类别与系列扩展两探针（`docs/pin-mux-probe`、`docs/pin-probe`），
   `PIN_ROUTE_SOURCES` 逐系列登记；模拟复用/固定功能/副功能三类的
   结构表达待后续设计（证据已留档探针 README）；
2. ~~**SVD MODE 拆分实例**映射待建立~~ **已解决**：`csv_to_yaml.py` 现按
   `_?MODE(0|1|23)$` 后缀把 `register_versions.csv` 的 SVD 视图名
   （`TIM0_MODE0/1/23`、`ATIM0MODE0/1/23`）反向关联到 `peripherals.csv` 的
   物理实例（`TIM0`/`ATIM0`），family YAML 以 `views:` 列表挂接
   （17 系列 × 60 实例，视图 SVD 原名 + kind/version/block 逐项保留）。
   生成链贯通：物理实例承载地址/FCG 门控/中断（chips JSON 与 metadata 的
   `views` 字段），PAC 保留物理实例地址常量，并为每个视图新增同基址的类型化常量
   （常量名沿用 SVD 视图名，
   附模式切换 doc：工作模式由控制寄存器 MD 位运行时选择，地址/时钟/中断
   共用，勿并发访问两个视图）。关联完整性校验（视图 kind/block 一致性、
   与提取侧合并 note 双向互证）失败即中止，逐实例关联报告打印到 stdout；
3. **F120/M120** 无 DS，官网行为主源；**F155** DS 版式特殊；**A472** 同缺内存
   布局——生成侧跳过；
4. 寄存器描述为 SVD 原文（自动生成的占位文本，质量有限）；
5. 归档 SVD 的位域暂无 `enumeratedValues` 数据，`usart_ddl_v1.CR1.TOE`
   已通过补录链生成枚举。扩面时按布局版本核对 RM
   和 SDK 常量，确认编码、读写语义及跨系列是否一致。`MergeEnums` 可归并已有枚举；
6. ~~`core.dma_channels`（DMA 控制器通道清单）未生成~~：已接入——通道数来自
   dma.csv（LL：设备头 `AOS_DMA<x>_<n>` 宏范围；DDL：`dmac.h` `DmaCh*` 枚举
   即 channels 列，A052/F052=5、F420=6、其余=2，RM 第 12 章佐证），
   `csv_to_yaml.py` 写入 chips YAML 的 `cores[].dma_channels`；
7. 各系列提取细节见各数据仓库 `data/csv/EXTRACTION_NOTES.md`；
8. **HC32F175（LL）暂停支持**：IP 多代缝合（旧式布局、固定 DMAC 请求编号），
   由 `scripts/support_scope.py` 声明、提取与生成链跳过；SVD 归档保留，其专属
   布局已从显式映射删除（编号压实，旧名在映射的 `renamed_from` 留档）。

生成侧降级（`./d gen` 日志逐条告警，当前数量）：

| 现象 | 数量 | 处理 |
|---|---|---|
| ~~外设挂不上寄存器版本（SVD/设备头实例名不一致，见缺口 2）~~ | ~~367~~ → 0 | MODE 视图关联已建立，TIM/ATIM 经 `views` 挂接（见缺口 2），无降级 |
| 芯片无内存布局（见缺口 3） | 16 | 跳过不生成 |
| IRQn 同号别名/共享向量（如 `INT000`/`SWINT0`、`BTIM3_4_5`/`GTIM`） | 1192 | 保留枚举首个主名 |
| DMA 系统事件、SDK 共享模板多列的触发，以及下节登记的 HALL/HRPWM 事件 | 4116 | 归类计数（INFO），暂未挂载 |
| DMA 触发名 ≠ 实例名（HC32A052/F052 `UART0/1`→`USART0/1`） | 40 | 系列别名表挂载（`dmac.h` 注释 + RM TRI_SEL 表证实），信号名保留 SDK 原拼 |
| DMA 触发来源不明 | 0 | 逐条告警待人工核对 |

### 待核实的中断、DMA 和寄存器语义

| 项目 | 已确认事实与后续核对点 |
|---|---|
| K116/K118 的 LVD IRQ 28 | PWC 的 LVD 信号来自 SVD，设备头向量枚举跳过 28。用 RM 向量表、SDK 启动文件和 LVD 例程确认实际路由之前，源信号保留在 CSV，生成的静态引用省略。 |
| F334/F336 的 PLAINT 和 F472/A472 的 PLA | 设备头分别跳过 SVD 所列的 IRQ 136 和 163。PLA 在 LL 扩展排除清单中，启用该外设时复核。 |
| F431/M413 的 HALL DMA | 两系列的 family 数据都包含 `HALL`。设备头 `EVT_SRC_HAL_CAP` / `EVT_SRC_HAL_OVF` 对应 HALL 捕获／溢出，事件号为 400/401。当前按已知事件计数，尚未挂接。补齐时用两系列限定别名将事件挂到 `HALL`。 |
| F334/F336 的 `HRPWM_EEDET` | 设备头给出事件号 219，family 数据含 `HRPWM_COMMON`。启用 HRPWM 时核对该事件的共享范围和归属。 |
| A052 的 `CONFA5` | SVD 和设备头都定义了该寄存器，驱动的物理通道枚举只覆盖 0–4。保留寄存器描述，通道清单按已确认的 5 个通道生成。槽位用途待 RM 证据确认。 |
| DDL 定时器切换模式和中断回流 | PAC 的 MODE 视图共用物理地址，门控和中断由 family 物理实例承载。HAL 使用具体系列前需核对停表、写 `MD` 和重配寄存器的时序。SVD 回流仍按实例名匹配，补回流时需定义物理实例的中断如何映射到各视图。 |
| M458 的 `ADC1_ECOA` | 保留设备头采用的 IRQ 70 公开向量名。外设信号 `ADC1_EOCA` 已解析到该向量。 |
| OTP、DFLASH、QSPI 等内存窗口 | 当前内存布局生成覆盖代码 Flash、主 SRAM 和已确认的独立 RAM 区。加入其他窗口前需明确它们的角色、访问属性和链接脚本用途。 |

## CSV 列定义

统一列契约见 [scripts/CSV_SCHEMA.md](scripts/CSV_SCHEMA.md)。补充：

- `website_chips.csv` 与 `chips.csv` 同列头（source=website）；
- `registers.csv`（审计用）：`series,instance,kind,register,offset,description,
  field,bit_offset,bit_size,access,field_description`；
- `register_versions.csv`：`series,instance,kind,version,block`；
- `svd_meta.csv`：`series,cpu,device_name,peripheral_count`；
- 探针 CSV（hc32-data-tools `docs/`，pins 数据源，不在数据仓库 data/csv）：
  路由表 `series,pad,peripheral,signal,selector_kind,selector_value,source`；
  封装管脚表 `sample_part,part_or_package,terminal,pad,kind,source_table,source_page`。

## CI（.github/workflows/）

- **build**（push/PR main）：`cargo check --workspace` + serde 往返测试（CI 内
  clone 两个数据仓库）+ `./d gen` + M0+/M4F 构建与 host 元数据测试；
- **metapac nightly**（push main）：重新生成并强推 `hc32-rs/hc32-metapac`
  （带 `hc32-data-tools-<commit>` 溯源 tag）。需要在仓库 Secrets 配置
  `METAPAC_TOKEN`（对 `hc32-rs/hc32-metapac` 有 Contents 写权限的 PAT，
  细节见 workflow 文件头注释）。
