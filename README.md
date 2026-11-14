# hc32-data-tools

小华半导体 HC32 系列的数据提取与 YAML 生成工具链。从 SDK 文档（数据手册 PDF、
设备头、驱动头）、SVD 文件与小华官网提取数据为 CSV，再转换为
[ch32-rs/ch32-data](https://github.com/ch32-rs/ch32-data) 风格的 YAML，供
PAC（寄存器访问层）与 HAL 生成使用。

- 数据仓库：[hc32-ddl-data](https://github.com/hc32-rs/hc32-ddl-data)（20 个老 DDL 驱动系列）
  与 [hc32-ll-data](https://github.com/hc32-rs/hc32-ll-data)（27 个 LL 驱动系列）
- 寄存器处理使用 [embassy-rs/chiptool](https://github.com/embassy-rs/chiptool)
  （HC32 为 ARM Cortex-M，无需 ch32-rs fork 的 QingKe 扩展）

## 目录布局要求

本仓库须与数据仓库、SDK 放在同一父目录下（脚本默认 `../` 定位）：

```
hc32-rs-workspace/
├── hc32-data-tools/     ← 本仓库（脚本）
├── hc32-ddl-data/       ← DDL 系列数据仓库
├── hc32-ll-data/        ← LL 系列数据仓库
├── xh_ddl_sdk/          ← 小华 DDL SDK（含 documents/DS_*.pdf、RM_*.pdf）
├── xh_ll_sdk/           ← 小华 LL SDK
└── build/               ← 运行期缓存（chiptool 原始输出、修补 SVD），可随时删除
```

## 流水线总览

```
xh_ddl_sdk ──extract_ddl_csv.py──▶ hc32-ddl-data/data/csv/{chips,interrupts,dma,peripherals,memory}.csv ─┐
xh_ll_sdk ───extract_ll_csv.py───▶ hc32-ll-data/data/csv/（同上）───────────────────────────────────────┤
                                                                                                        ├─▶ csv_to_yaml.py
小华官网 ────hdsc_fetch_products.py──▶ 两侧 data/csv/website_chips.csv ─────────────────────────────────┤    （chips/family/
两仓库 svd/ ──chiptool_registers.py─▶ 两侧 data/registers/ + register_versions.csv ────────────────────┤     interrupts/dma/
            ─svd_to_registers_csv.py─▶ 两侧 data/csv/registers.csv（核对基准）+ svd_meta.csv ──────────┘     memory 生成）
```

## 各 YAML 的生成方式

### `data/registers/<kind>_<version>.yaml`（寄存器块）

- **来源**：两仓库 `svd/*.svd`（来源为 SDK 内 SVD，git 行尾归一为 LF）。
- **工具**：`chiptool_registers.py` = [chiptool](https://github.com/embassy-rs/chiptool)
  `extract-all` + 聚合。
- **过程**：
  1. 14 个老代 SVD 的 `<addressBlock>` 缺 `<usage>` 标签（严格 svd-parser 拒绝），
     脚本自动复制补丁副本到 `build/svd_fixed/`（原始 SVD 不动）再解析；
  2. `chiptool extract-all` 把每个 SVD 拆成每外设一个 IR YAML
     （`block/<外设>` + `fieldset/<寄存器>` 段，含 access/bit_size/数组），
     缓存在 `build/chiptool/<仓库>/<系列>/`；
  3. 聚合器把全部系列的块按 `(kind, 布局签名)` 去重合并成
     `<kind>_<version>.yaml`：一个布局若覆盖该 kind 出现的**全部**系列 →
     `common`，否则按首次出现序 `v1、v2…`；布局代表取系列名排序最前者，
     `derivedFrom` 外设不单独产出（与父实例同布局）。
- **产物**：`data/registers/<kind>_<version>.yaml` + `data/csv/register_versions.csv`
  （`series,instance,kind,version,block` 实例→版本映射，family YAML 引用它）。
- **核对**：`svd_to_registers_csv.py` 产出实例级 `registers.csv` 作为核对基准；
  与 chiptool 输出做过全量对比（寄存器/字段 100% 一致，报告见
  `chiptool-trial` 试验，`compare_chiptool.py` 可复验）。

### `data/chips/<PART>.yaml`（型号）

- **来源**：各 SDK `documents/DS_*.pdf` 的"型号功能对比表/订货信息"（pypdf 解析），
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

- **来源**：设备头 `IRQn_Type` 枚举（多数 HC32 SVD 的中断节点缺失/为 0，
  不可用）。负数内核中断（NMI/HardFault 等）不列入。

### `data/dma/<series>_<DMA>.yaml`（DMA 触发源）

- **来源**：驱动头（DDL `DMA_TRISRC_*` / LL `EVT_SRC_*`）+ RM 触发源表核对。
- **语义按仓库不同**（文件头注释已写明）：
  - DDL：值 = `DMAC_CONFAx.TRI_SEL` 全局触发源索引，请求与物理通道为运行时软件路由；
  - LL：值 = 设备头 `EVT_SRC_*` 9 位事件号，写 `AOS->DMAx_TRGSELy`。

### `data/memory/<series>_F<n>S<n>.yaml`（内存布局）

- **来源**：设备头 `FLASH_BASE`/`SRAM_BASE` `#define` 或 SDK 链接脚本
  （`memory.csv` 的 notes 列记录出处）；Flash/SRAM 大小按 chips.csv 的
  型号组合去重成 `<系列>_F<大小>S<大小>.yaml`。

## 使用

```bash
# 依赖：python3 + pypdf/pyyaml；chiptool（见下）
python3 extract_ddl_csv.py         # 1. DDL SDK → CSV
python3 extract_ll_csv.py          # 2. LL SDK → CSV
python3 hdsc_fetch_products.py     # 3. 官网型号 → website_chips.csv
python3 chiptool_registers.py      # 4. SVD → data/registers/（chiptool）
python3 svd_to_registers_csv.py    #    （核对基准 registers.csv + svd_meta.csv）
python3 csv_to_yaml.py             # 5. CSV → chips/family/interrupts/dma/memory（末尾自动校验）
```

辅助/审计脚本：

- `chiptool_trial.py`：对全部 SVD 重跑 chiptool 试验提取（输出到临时目录）；
- `compare_chiptool.py`：chiptool 输出 vs `registers.csv` 的差异报告。

chiptool 安装（GitHub 直连受限时先 clone 再本地安装）：

```bash
git clone https://github.com/embassy-rs/chiptool /tmp/chiptool-src
cargo install --path /tmp/chiptool-src --locked
```

## CSV 列定义

统一列契约见 [CSV_SCHEMA.md](CSV_SCHEMA.md)。补充：

- `website_chips.csv` 与 `chips.csv` 同列头（source=website）；
- `registers.csv`（审计用）：`series,instance,kind,register,offset,description,
  field,bit_offset,bit_size,access,field_description`；
- `register_versions.csv`：`series,instance,kind,version,block`；
- `svd_meta.csv`：`series,cpu,device_name,peripheral_count`。

## 已知缺口与待办

1. **pins 引脚复用**未提取（DS GPIO 章节表格解析），family YAML 留 TODO；
2. 寄存器描述为 SVD 原文（自动生成的占位文本，质量有限）；
3. HC32 SVD 无 enumeratedValues，`enum/` 段暂为空；后续可用
   `chiptool transform`（`MakeRegisterArray`/`MergeEnums`）收敛寄存器版本号；
4. HC32F155 的 DS 版式特殊，部分型号属性缺失；F120/M120 无 DS，官网行为主源；
5. 各系列提取细节见各数据仓库 `data/csv/EXTRACTION_NOTES.md`。
