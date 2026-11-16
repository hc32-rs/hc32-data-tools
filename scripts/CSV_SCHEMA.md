# HC32 数据提取 CSV 统一模式（subagent 契约）

两个提取脚本（DDL 侧与 LL 侧）必须产出**完全相同的列头**，供 `tools/csv_to_yaml.py`
统一转换成 ch32-data 风格的 YAML（`chips/`、`family/`、`peripherals/`、`interrupts/`、
`dma/`、`memory/`，参考 `/Volumes/aigo_1t/Github/ch32-rs/ch32-data/data`）。

通用约定：

- 编码 UTF-8；逗号分隔；含逗号/引号的字段加双引号转义；空缺字段留空。
- 提取用审计 CSV 输出到 `<repo>/data/csv/`（DDL: `hc32-ddl-data`，LL: `hc32-ll-data`）。
  文档评审用版本对照独立放在 `docs/`，见文末说明。
- 每个仓库一份 `chips.csv` + `interrupts.csv` + `dma.csv` + `peripherals.csv` +
  `memory.csv`，行内都带 `series` 列区分系列。
- `series` 取 SDK 目录名前缀（如 `HC32F460_DDL_Rev3.3.0` → `HC32F460`；
  `hc32l186_ddl` → `HC32L18x`，与数据手册标题一致）。
- 一次解析失败不阻塞其它系列；失败系列在 CSV 中留空并在脚本报告中列出。

## chips.csv — 型号主表（一行 = 一个订货型号）

来源：数据手册 DS PDF 的“型号功能对比表 / 订货信息 / 特性”页，型号一般形如
`HC32F460PETB-LQFP100`。`part_no` 一律去掉 `-封装` 后缀。
列头（顺序固定）：

```
part_no,series,freq_mhz,flash_kb,sram_kb,package,pin_count,gpio,vdd_min,vdd_max,uart,spi,i2c,i2s,can,usb,adc,dac,timer,rtc,wdt,touchkey,lcd,opa,cmp,other,url,source
```

- `freq_mhz`：最大主频，纯数字（如 `200`）。
- `flash_kb` / `sram_kb`：KB 纯数字（如 `512`、`192`）。
- `package`：如 `LQFP100`、`QFN48`（多个用 `;` 分隔并按 pin_count 填第一个）。
- `gpio`：GPIO 数量，纯数字。
- `vdd_min`/`vdd_max`：如 `1.8` / `3.6`。
- 外设计数列：纯数字或留空；`adc` 复数通道时填位数可写 `other`。多单元用 `;`
  分隔（如 `TimerA=6` → `timer` 列可填 `6`，细分写进 `other`）。
- `other`：自由文本（温度范围、加密、CAN-FD、LCD、PLL 等），`|` 分隔。
- `url`：型号页 URL（未知留空）。
- `source`：`DS`（数据手册）或 `website`（官网补抓）。

## interrupts.csv — 中断向量（一行 = 一个 IRQ）

来源：设备头 `IRQn_Type` 枚举（机器可读，优先）；必要时用 RM 中断表核对。

```
series,name,irq,description
```

- `name`：枚举名去 `_IRQn` 后缀（如 `USART1_GLOBAL`）。
- `irq`：枚举数值（负数如 -1 表示内核中断也保留）。
- `description`：枚举上方注释或表中文名。

## dma.csv — DMA 请求映射（一行 = 一个 外设信号→通道 触发源）

来源：驱动头（LL `hc32_ll_dma.h` 的 `DMA_SRC_*`；DDL 各系列 `dma.h`/AOS 触发映射）
+ RM 的 DMA 触发源表核对。**必须先从 RM 弄清编码语义**（常见：高 4 位=通道号、
低 4 位=触发索引，或反之），再批量解码。

```
series,dma,channel,request
```

- `dma`：DMA 实例名（如 `DMA1`；只有一个 DMA 的系列也统一填 `DMA1`）。
- `channel`：通道号，从 0 或 1 计，遵循 RM 原始定义。
- `request`：外设信号名，规范成 `外设_方向`（如 `USART1_RX`、`ADC1`、
  `TIMA0_CMPA`），与驱动枚举名对应但去掉 `DMA_SRC_` 前缀。

## peripherals.csv — 外设实例（一行 = 一个外设实例，喂 family/peripherals YAML）

来源：SVD（`<repo>/svd/*.svd`，地址权威）+ 设备头时钟使能位（`FCG*`/`PWC_FCG*`）。

```
series,instance,kind,base_address,bus,fcg_reg,fcg_field,interrupts,notes
```

- `instance`：如 `USART1`、`TIMA0`、`GPIOA`。
- `kind`：小写外设类别（去数字后缀）：`usart`、`tima`、`gpio`、`adc`…
- `base_address`：`0x` 十六进制，取自 SVD。
- `bus`：`AHB`/`APB1`/…（RM/SVD 可查则填，否则空）。
- `fcg_reg`/`fcg_field`：时钟门控寄存器与位域（如 `FCG1`/`FCG1_USART1_CLKEN`；
  DDL M0 系列形如 `PWC_FCG1`/`PWC_FCG1_USART1EN`）。GPIO 等无门控留空。
- `interrupts`：该实例的中断名，`;` 分隔（如 `USART1_GLOBAL`），与 interrupts.csv
  的 `name` 对齐；未知留空。
- `notes`：自由文本。

## memory.csv — 内存区域基址（一行 = 一个系列）

来源：设备头 `#define FLASH_BASE ...`、`SRAM_BASE` 或 SVD `<memory>`。

```
series,flash_base,sram_base,sram_hi_base,notes
```

- `flash_base`：代码 Flash 起始（M4 一般 `0x00000000`，M0 系列看头文件）。
- `sram_base`：SRAM 起始（如 `0x1FFF8000`）。
- `sram_hi_base`：可选的高速 SRAM 区起始（如 F460 的 0x20000000 32K 区）。
- `notes`：自由文本。

## 校验要求

- 每个系列 chips.csv 行数 ≥ 数据手册“支持型号”数量；用官网型号清单交叉核对
  （若有）。
- interrupts.csv 的 IRQ 最大值 +1 应与 RM 中断表向量数一致（抽查）。
- dma.csv 的 request 名必须去重且 channel 在合法范围。
- peripherals.csv 每个系列实例数应接近 SVD 顶层 peripheral 数。

## 文档评审用外设版本对照

两数据仓库的 `docs/peripheral-versions.csv` 是独立于 `data/csv/` 审计层的命名提案，
由 `export_peripheral_versions.py` 读取 `docs/peripheral-version-map.json` 导出。
生成器尚不消费该提案。每行唯一键为 `(series, instance)`，含模式视图和派生实例。

| 列 | 含义 |
|---|---|
| series | MCU 系列，保留 HC32 前缀；不展开到封装料号 |
| instance | SVD 实例名，保留 MODE 和共享窗口后缀 |
| kind | 现有 `data/csv/register_versions.csv` 的 kind，可能是 i、tmr 等旧标识 |
| current_version | 该数据仓库内的当前版本；不包含下游跨仓库冲突重映射 |
| proposed_version | 独立分配的 vN 或 vN_M，排除项留空 |
| target_file | 完整目标名 `<kind>_ddl_vN[_M].rs` / `<kind>_ll_vN[_M].rs`，排除项留空 |
| status | proposed：待确认；excluded：LL M4 扩展范围，本次不整理 |
| notes | 来源 YAML、完整目标 kind、功能依据、同档差异提示和专题证据路径 |

文件为 UTF-8、LF。导出按旧 kind、新版本数字顺序、目标文件、系列及实例排序。
同一目标文件只对应一个显式布局记录；同布局的不同实例共享目标文件。
版本分配及来源校验信息在 JSON 中维护，禁止仅修改导出的 CSV 或标记文档表格。
