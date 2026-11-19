# HC32F003/F460 UART·SPI 引脚路由小样本（pin-mux probe）

以 DDL F003、LL F460 为样本，从数据手册引脚复用表提取一组 UART/USART 与
SPI 的焊盘级路由，为全系列 pins 数据缺口（hc32-data-tools README 缺口 1）
建立**字段契约 + 提取算法 + 抽查方法**。上一探针（`../pin-probe/`）解决了
terminal→pad；本探针解决 pad→(peripheral, signal, selector) 信号级路由。

## 来源

| 系列 | 文件 | 表/章 | 用途 |
|---|---|---|---|
| HC32F003 (DDL) | `xh_ddl_sdk/HC32F003_DDL_Rev2.0.0/documents/RM_HC32F005_F003系列参考手册_Rev2.50.pdf` | 表 6-2 端口复用表（p.76） | **主源**：PxADS=1 模拟列 + Px_sel=0..7 数字列，每焊盘一行 |
| HC32F003 (DDL) | `…/DS_HC32F005_F003系列数据手册_Rev3.00.pdf` | 第 4 章引脚功能说明（p.21-24） | **第二证据源**：A2 逐焊盘序列双源核对 |
| HC32F460 (LL) | `xh_ll_sdk/HC32F460_DDL_Rev3.3.0/documents/DS_HC32F460系列数据手册_Rev1.61.pdf` | 表 2-1 引脚功能表（p.33-35）+ 表 2-2 Func32~63（p.36） | **主源**：Func0..15 每焊盘独立列；Func32~63 按 Func_Grp1/2 分组共享 |
| HC32F460 (LL) | `xh_ll_sdk/…/projects/ev_hc32f460_lqfp100_v2/examples/{usart,spi}/…` | usart_uart_int/polling、spi_int | **抽查基准**（GPIO_SetFunc 调用值） |
| HC32F003 (DDL) | `xh_ddl_sdk/…/example/{uart/si_master}` + `mcu/common/board_stkhc32f003.h` | uart_int、spi_master | **抽查基准**（Gpio_SetAfMode 调用值） |

选择器机制（RM 佐证）：

- **F003**：`PxADS` 切模拟/数字（模拟时数字功能隔离）；`Px_sel=0..7` 选数字功能
  （RM 6.3.4，gpio.c `Gpio_SetAfMode` 直写 `Px_SEL`）。P00=复位固定功能，
  P27/SWDIO、P31/SWCLK 上电默认 SWD（RM 调试章节），均为**固定功能**，
  无选择位，不产出路由。
- **F460**：`PFSRxy.FSEL[5:0]`（0x0=GPO，1..15 每焊盘独立，32..63 组共享，
  RM 9.4.12）。`PSPCR.SPFE` 优先级高于 FSEL（JTAG/SWD，固定功能类）；
  `PFSRxy.BFE`+`PCCR.BFSEL[3:0]` 是双功能同开机制（副功能），本样本
  UART/SPI 不涉及；`PCRxy.DDIS` 禁数字功能（模拟类）。

## 产物与字段契约

`f003_uart_spi_routes.csv` / `f460_uart_spi_routes.csv`（列头一致）：

| 列 | 含义 | 例 |
|---|---|---|
| series | 系列（family YAML 名） | HC32F003 / HC32F460 |
| pad | 芯片逻辑焊盘名（同一焊盘允许多条合法路由） | P35 / PA9 |
| peripheral | family YAML 外设实例名 | UART1 / USART4 / SPI1 |
| signal | 规范信号名 | TX / RX / SCK / MISO / MOSI / CS / SS0 / CK / RTS / CTS |
| selector_kind | 本样本全部 `fsel`（单一选择寄存器直接可选） | fsel |
| selector_value | 选择器编码：F003=Px_sel 值（1..7）；F460=FSEL 值（1..15 独立列 / 32..63 组） | 1 / 36 |
| source | 文档出处 | RM表6-2 端口复用表 p.76 |

## 覆盖范围

| | F003 | F460 |
|---|---|---|
| 外设 | UART0/UART1、SPI（各系列全部实例） | USART1..4、SPI1..4 |
| 信号 | UART TX/RX；SPI SCK/CS/MISO/MOSI | USART TX/RX/RTS/CTS/CK；SPI MOSI/MISO/SS0/SCK/SS1..3 |
| 路由数 | 28（UART0×8、UART1×8、SPI×12） | 1048（组共享 1024 + 独立列 24） |
| 焊盘数 | 16 GPIO 焊盘中 13 个有 UART/SPI 路由 | 64 个 Func_Grp 焊盘 + 24 条独立列路由 |
| 组共享展开 | 不适用（Px_sel 每焊盘独立） | Grp1=32 焊盘、Grp2=32 焊盘（合计 64，与 DS 表 2-1 注释一致） |

F003 计数表（表 6-2 逐格核对，A3 断言）：

- UART0：RXD×4（P01,P15,P27,P36）TXD×4（P02,P14,P31,P35）
- UART1：TXD×4（P01,P23,P32,P35）RXD×4（P02,P24,P33,P36）
- SPI：SCK×3（P01,P15,P25）CS×3（P02,P03,P14）
  MISO×3（P23,P27,P35）MOSI×3（P24,P26,P36）

F460 分布（组共享每个 Func 值覆盖该组全部 32 焊盘）：USART1..4 各 131 条、
SPI1..4 各 128~134 条（含 CK/SSx 独立列路由）。

## 路由可表达性分类（对照现有 `Pin.func` 结构）

现有结构：family YAML 外设项 `pins: [{pin, signal, func}]`
（`hc32-data-serde/src/chip/core/peripheral.rs:45`，
`func: Option<u8>` 已预留，metapac 元数据链路已通）。

| 类别 | 样本证据 | Pin.func 可否表达 |
|---|---|---|
| **fsel（数字复用）** | F003 全部 28 条；F460 全部 1048 条 | **可**：`selector_value` 即 `func`（F003 Px_sel、F460 FSEL 同一语义——写一个寄存器值即得目标信号）。F460 func 值 >15，`Option<u8>` 容量足够（max 63） |
| **模拟复用**（PxADS=1 / DDIS=1） | F003 表 6-2 模拟列（AIN/VC/XTH/LVD）；F460 Analog 列 | **否**：需"禁数字功能"开关而非功能号。样本外设（UART/SPI 均数字）不产生；补 pins 数据时 ADC/VC 通道需扩展结构（如 `selector_kind: analog`） |
| **固定功能** | F003 P00=RESETB、SWD（P27/P31 上电默认）；F460 PB11/MD=NMI、PSPCR 管的 JTAG/SWD | **否**：无选择位。建议 `func: null` + 保留原始条件注释；或外设级 special-pin 留档 |
| **副功能（BFE/BFSEL）** | F460 RM 9.3.3（FSEL 与 BFSEL 双开） | **否**：双选择器同开，超出单 func 值模型。样本 UART/SPI 无此路由（表 2-1/2-2 中 USART/SPI 全部单一 FSEL 可选）；启用其他外设时按原始条件留档 |
| **组共享 Func_Grp** | F460 表 2-2：同一 FSEL 值在 Grp1/Grp2 焊盘上是不同外设信号 | **可**：按焊盘展开后每条路由独立成行（本样本做法），func 值语义不变 |

**结论**：样本内全部 UART/SPI 路由均可直接映射为
`pins: [{pin: PA9, signal: TX, func: 37}]` 形式，无结构缺口；
模拟/固定功能/副功能三类在样本外，原始条件与证据已留档于本 README。

## 未确认项

1. **F003 P33 sel5 = XTL_OUT**：RM 表 6-2 有此项，DS Rev3.00 第 4 章 P33
   未列（DS 更新）。已按 RM 产出结构但**不产出路由**（非 UART/SPI），
   A2 断言白名单跳过。待 XTL32 相关 DS/RM 勘误确认。
2. **F003 P14 sel7 = NC**：RM 表 6-2 填 NC（无功能），DS 同位置列 '/'。
   按空槽处理，不产出路由。
3. **F460 VFBGA100 专属行**（`- H3 - - - NC …` 等 3 行）：LQFP100 无对应
   管脚，无焊盘名，跳过（与 pin-probe 探针的 VFBGA 未确认项一致）。
4. **F460 PB11/MD**：输入专用焊盘（RM 9.3.1），无 GPO；其 NMI 为固定功能。
   A6 断言显式豁免。
5. **F460 PC13 f15='-'**（EVENTOUT 不可选）：文档原文如此，A6 仅在
   EVENTOUT 出现时断言列位。
6. **组共享语义边界**：DS 表 2-1 注释"64 个引脚支持 Func32~63"，实测
   Grp1×32 + Grp2×32 = 64 一致；但同组内 16 个 Func 值是否在**全部**组内
   焊盘可选（个别焊盘列 '-')，本次按表 2-1 逐行 grp 列展开，未发现组内
   部分可选项（32 焊盘全部 16 值齐备）。若后续系列有部分可选行，需增加
   per-pad 组内掩码。

## 提取算法要点（pypdf，均经断言验证）

- 纯文本模式会吞掉表格空单元格 → 全部用 visitor 文本坐标重建列位。
- **F003 表 6-2**：每行单文本流；片段起点 x 对齐 Px_sel 列锚点定跨单元格
  新段起点；段内 ≥2 空格间隙 = 空单元格（n 空格 ⇒ n-1 空槽；P03 3 空格=2 空、
  P25 2 空格=1 空，双向验证）。
- **F460 表 2-1**：统一 27-token 列模型（0 pin … 5 pad … 9 f0 … 26 grp）；
  片段起点 x→起始列，token 向连续列铺开；行内换行
  （`TIMA_1_PWM1/TIM`+`A_1_CLKA`、`EIRQ3/WKUP0_`+`3`、
  `ADC1_IN3/PGAVSS/CMP`+`1_INP4`）按同列 y0±4 片段拼接还原。

## 验收记录

- **A1-A9 断言全绿**（脚本内置，见文件头注释）；重跑输出两 CSV 字节级一致。
- **逐条人工核对**（对照 RM 表 6-2 / DS 表 2-1 原文）：
  - F003 UART1：TX= P01(sel3)/P23(6)/P32(4)/P35(1)，RX= P02(3)/P24(6)/P33(4)/P36(1) ✓
  - F003 SPI：SCK= P01(6)/P15(5)/P25(1)；MISO= P23(5)/P27(1)/P35(6)；
    MOSI= P24(5)/P26(1)/P36(6)；CS= P02(6)/P03(2)/P14(5) ✓
  - F460 USART4.TX/RX：各 32 焊盘（全部 Grp2），func 36/37 ✓
  - F460 SPI1 SCK/MISO/MOSI/SS0：各 32 焊盘（全部 Grp1），func 40/41/42/43 ✓
- **SDK 例程逐点抽查**（脚本断言 A4/A7）：
  - F003：`Gpio_SetAfMode(P35,PIN,Af1)`=UART1_TX(sel1)、P36 Af1=UART1_RX、
    P25 Af1=SPI_SCK、P03 Af2=SPI_CS、P24 Af5=SPI_MOSI、P23 Af5=SPI_MISO
    （board_stkhc32f003.h + uart_int/spi_master 例程）
  - F460：`GPIO_SetFunc(PE6,PIN,36)`=USART4_TX、PB9/37=USART4_RX、
    PA7/42=SPI1_SS0、PA8/43=SPI1_SCK、PB0/40=SPI1_MOSI、PC5/41=SPI1_MISO
    （usart_uart_int、spi_int 例程）

## 复现

```sh
# 工作区根目录运行（脚本按自身位置定位 SDK，不依赖 cwd）
python3 hc32-data-tools/docs/pin-mux-probe/extract_f003_f460_pinmux.py
# 断言全绿后重写两个 CSV，并回显 SDK 抽查结果
```

## Agent 2 消费指引

- 两 CSV 同列头，可直接按 `(series, pad)` 合并进 family YAML 的外设
  `pins:` 段：`pin`=pad（F003 的 Pxy 需按系列映射为 Pin::parse 的
  `P<port><num>`；F460 的 PA9 即原生格式），
  `signal`=signal，`func`=selector_value（均为 u8 范围内的 fsel 值）。
- 合并时注意同一 (peripheral, signal) 在 F460 有 32 个焊盘候选——HAL 侧
  引脚选择器直接读 `pins:` 列表即可，无需另建选择位表。
- 无法用 `Pin.func` 表达的三类（模拟/固定功能/副功能）如上表所列，
  原始条件已留档，补 ADC/CMP/调试引脚前勿将其并入 func 值。
