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
表 2-1 按封装分列。PETB/PCTB 对应 LQFP100，PEHB 对应 VFBGA100，
JETA/JCTA 对应 LQFP48，JEUA 对应 QFN48。CSV 的 `part_or_package` 记录封装，
`sample_part` 标注已登记的样本型号。

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

- 两封装共有 38 个 GPIO 焊盘，如 PA0 JCTA pin10 ↔ PCTB pin23、
  PB0 18↔35、PC13 2↔7、PH2 1↔6、PA13 34↔72
- 大封装独有 45 个 GPIO 焊盘，如 PD3 pin84、PE7 pin38、
  PC6 pin63、PD15 pin62、PE15 pin46（整组 PortD/PortE 及 PC0~PC12）
- LQFP48 的 38 个 GPIO 是 LQFP100 的 83 个 GPIO 的子集，端口位图
  LQFP48 = A0-15 + B0-15 + C13-15 + H0-2，与表 2-3 完全一致

`csv_to_yaml.py` 将焊盘集合与复用路由表求交集，JCTA 和 PCTB 分别生成
539 和 1,048 条型号级路由。 [焊盘复用探针](../pin-mux-probe/README.md)

## 未确认项

1. VFBGA100 表 2-1 行 19/27/28（VCC/AVSS/AVCC，LQFP100 冗余电源脚）列为 "-"：
   该封装无对应球，图 2-1 网格 100 球中 3 球（含 VREFL 独立球）无法从文本
   无歧义定位，均为电源类，不影响 GPIO 路由，留待人工比对图 2-1。
2. DS 对采用同一封装的订货号共用一列。扩展到其他订货号前，还需核对型号登记和最终路由。
3. `pad` 保留 DS 的 ADC/CMP/XTAL 等复用后缀。数字 UART/SPI 路由已由复用探针
   提供，其他外设类别待补录。

## 复现

```sh
python3 docs/pin-probe/extract_f460_pins.py   # 断言全绿后重写 CSV（路径见脚本内 PDF/OUT）
```
