# HC32F460 管脚路由小样本（pin probe）

以 DS_HC32F460系列数据手册_Rev1.61.pdf 为单一来源的可复现提取样本，
为后续全系列 pins（引脚复用）数据缺口（README 缺口 1）建立字段契约与验证方法。

## 来源

| 文件 | 表/图 | 用途 |
|---|---|---|
| xh_ll_sdk/HC32F460_DDL_Rev3.3.0/documents/DS_HC32F460系列数据手册_Rev1.61.pdf | 表 2-1 引脚功能表（p.33-35） | 100 行 × 6 封装管脚号→焊盘名 |
| 同上 | 表 2-3 端口配置（p.37） | 每封装 GPIO 端口位图（独立第二证据源，双源核对） |
| 同上 | 图 2-1 引脚配置图（p.27-32） | 封装图交叉核对（VFBGA NC/电源球位） |

样本型号：HC32F460JCTA（LQFP48）↔ HC32F460PCTB（LQFP100）。
表 2-1 的 LQFP100 列同时覆盖 PETB/PCTB（PEHB 为 VFBGA100 封装，走 VFBGA100 列），LQFP/QFN48 列覆盖 JETA/JCTA/JEUA，
按封装而非按完整订货号分列，故 CSV 中 part_or_package 记封装名，
sample_part 列才标注具体样本型号。

## 字段契约（hc32f460_pin_terminals.csv）

| 列 | 含义 | 例 |
|---|---|---|
| sample_part | 样本型号（非样本封装行为空） | HC32F460JCTA |
| part_or_package | 封装名 | LQFP48 / LQFP100 / VFBGA100 / LQFP64 / QFN60 / QFN48 |
| terminal | 封装管脚号（VFBGA 为球坐标） | 10 / B2 |
| pad | 焊盘名（含复用功能前缀） | PA0/ADC1_IN0 |
| kind | gpio / power / reset / nc / vcap | gpio |
| source_table / source_page | 出处 | 表2-1 引脚功能表 / p.33-35 |

## 断言（extract_f460_pins.py 内置，全绿才写 CSV）

- A1 行数 = 100；A2 每封装管脚号连续唯一 1..N（VFBGA100 为 97 球全唯一，见未确认项）
- A3 小封装 GPIO 焊盘 ⊆ LQFP100；A4 各封装 GPIO 数 == 表 2-3 Total（83/83/52/50/38/38）
- A5 GPIO 焊盘 (port,bit) 与表 2-3 位图逐位一致（该断言曾证伪一版手工转录的位序错误）

## 样本验收结果

- 共有路由（两封装同焊盘不同管脚号）：38 个，如 PA0 JCTA pin10 ↔ PCTB pin23、
  PB0 18↔35、PC13 2↔7、PH2 1↔6、PA13 34↔72
- 只在大封装引出：45 个（PCTB 有、JCTA 无），如 PD3 pin84、PE7 pin38、
  PC6 pin63、PD15 pin62、PE15 pin46（整组 PortD/PortE 及 PC0~PC12）
- 小封装无幽灵焊盘：LQFP48 GPIO 38 个 ⊆ LQFP100 83 个，端口位图
  LQFP48 = A0-15 + B0-15 + C13-15 + H0-2，与表 2-3 完全一致

## 未确认项

1. VFBGA100 表 2-1 行 19/27/28（VCC/AVSS/AVCC，LQFP100 冗余电源脚）列为 "-"：
   该封装无对应球，图 2-1 网格 100 球中 3 球（含 VREFL 独立球）无法从文本
   无歧义定位，均为电源类，不影响 GPIO 路由，留待人工比对图 2-1。
2. 表 2-1 中 LQFP100 列覆盖 PETB/PCTB 两个订货号（PEHB=VFBGA100 走球坐标列）、LQFP48 列覆盖
   JETA/JCTA/JEUA：同封装不同订货号共用管脚表（DS 未按订货号分列），
   假定同列即同路由（DS 无反例）。
3. pad 名保留 DS 原文（含 ADC/CMP/XTAL 复用后缀）；未来 pins 数据的
   信号级复用需结合 RM GPIO 章节按 (port,bit) 展开，本样本只建 terminal→pad 路由。

## 复现

```sh
python3 docs/pin-probe/extract_f460_pins.py   # 断言全绿后重写 CSV（路径见脚本内 PDF/OUT）
```
