use std::{collections::BTreeMap, path::Path};

/// 处理 cores[].include_dma_channels（`DMA1: ../dma/<SERIES>_DMA1.yaml`）。
///
/// HC32 的语义与 ch32 的固定通道模型不同，两代驱动分别是：
/// - DDL：值 = DMAC_CONFAx.TRI_SEL 全局触发源索引；
/// - LL：值 = EVT_SRC_* 事件号（9 位），写 AOS->DMAx_TRGSELy。
/// 两者都只把 request 号挂到外设上，不推断物理通道
/// （控制器通道清单由 csv_to_yaml.py 写入 core.dma_channels，此处透传）。
///
/// 未匹配信号的告警分两类输出（见 classify_unmatched）：
/// - 系统事件/幽灵模板项：计数归类，不逐条刷屏；
/// - 真正来源不明的：逐条列出待人工核对。
pub fn handle_chip_dma_include<P: AsRef<Path>>(
    path: P,
    chip: &mut hc32_data_serde::Chip,
) -> anyhow::Result<(Vec<(String, String)>, usize)> {
    let meta_yaml_path = path.as_ref();
    let mut unmatched = Vec::new();
    let mut aliased = 0;

    for core in &mut chip.cores {
        let Some(dma_channels_inc) = core.include_dma_channels.take() else {
            continue;
        };

        for (dma, inc_path) in dma_channels_inc {
            let dma_yaml_path = meta_yaml_path.parent().unwrap().join(&inc_path);
            let content = std::fs::read_to_string(&dma_yaml_path)?;
            let dma_map: BTreeMap<String, u16> = serde_yaml::from_str(&content)?;

            for (signal, request) in dma_map {
                let target = find_peripheral(&core.peripherals, &signal).or_else(|| {
                    let name = alias_target(&chip.family, &signal)?;
                    let index = core.peripherals.iter().position(|p| p.name == name)?;
                    aliased += 1;
                    Some((index, signal.clone()))
                });
                if let Some((index, signal_name)) = target {
                    core.peripherals[index].dma_channels.push(
                        hc32_data_serde::chip::core::peripheral::DmaChannel {
                            signal: signal_name,
                            dma: Some(dma.clone()),
                            channel: None,
                            request: Some(request),
                        },
                    );
                } else {
                    unmatched.push((dma.clone(), signal));
                }
            }
        }

        // HashMap 遍历顺序不定，排序保证重跑生成是幂等的
        for p in &mut core.peripherals {
            p.dma_channels.sort_by_key(|ch| {
                (
                    ch.dma.clone().unwrap_or_default(),
                    ch.request,
                    ch.signal.clone(),
                )
            });
        }
    }

    Ok((unmatched, aliased))
}

/// 已确认的「SDK 触发枚举名 → SVD 实例名」系列别名表。
///
/// 证据（hc32-rs-workspace 工作区内 SDK）：
/// - HC32A052_DLL_Rev1.0.1 / HC32F052_DDL_Rev1.1.0 `dmac.h`：
///   `DmaUart0RxTrig = 0x41 /* USART0接收Buf非空 */`（TX 同）；
/// - RM_HC32A052_Rev1.10 第 12.4.3 章 TRI_SEL 表：
///   `0x40 USART0 发送 Buf 空 / 0x41 USART0 接收 Buf 非空`；
/// - 两系列 SVD 实例名为 USART0/1 + LPUART0/1（无 UART0/1 实例）。
///
/// 仅列出的系列生效：触发名 UART0/UART1 在这些系列指向 USART0/USART1。
/// LP_UART0/1 与实例名 LPUART0/1 只差一个下划线，最长前缀规则本身即可命中
/// （find_peripheral 忽略下划线），无需别名。其他系列 UART0/1 是真实实例名，
/// 走原名匹配。新系列加入前必须先给出同级别的 SDK/RM 证据。
fn alias_target(family: &str, signal: &str) -> Option<&'static str> {
    match (family, signal) {
        ("HC32A052" | "HC32F052", "UART0_RX" | "UART0_TX") => Some("USART0"),
        ("HC32A052" | "HC32F052", "UART1_RX" | "UART1_TX") => Some("USART1"),
        _ => None,
    }
}

/// 在信号名里找最长匹配的外设实例前缀（忽略下划线差异：
/// `LP_UART0_RX` 对 `LPUART0`、`ADCJQR` 对 `ADC`）。
/// 返回 (外设, 规范化后的信号名)；`DAC0` → (DAC0, "DAC0")。
fn find_peripheral(
    peripherals: &[hc32_data_serde::chip::core::Peripheral],
    signal: &str,
) -> Option<(usize, String)> {
    let snorm: String = signal.chars().filter(|c| *c != '_').collect();

    let mut best: Option<(usize, usize)> = None; // (periph_index, norm_len)
    for (i, p) in peripherals.iter().enumerate() {
        let pn: String = p.name.chars().filter(|c| *c != '_').collect();
        if snorm == pn || snorm.starts_with(&pn) {
            let better = match &best {
                Some((_, len)) => pn.len() > *len,
                None => true,
            };
            if better {
                best = Some((i, pn.len()));
            }
        }
    }
    best.map(|(i, len)| {
        let signal_name = if len == snorm.len() {
            signal.to_string()
        } else {
            snorm[len..].to_string()
        };
        (i, signal_name)
    })
}

/// 匹配不到外设时，仅归类已核实的系统事件、共享事件和 SDK 模板项。
pub fn is_known_unmatched(family: &str, signal: &str) -> bool {
    let norm: String = signal.chars().filter(|c| *c != '_').collect();
    norm.starts_with("PORTEIRQ")
        || norm.starts_with("EVENTPORT")
        || matches!(norm.as_str(), "LVD1" | "LVD2")
        || matches!(
            (family, norm.as_str()),
            (
                "HC32A136"
                    | "HC32F030"
                    | "HC32F072"
                    | "HC32F17x"
                    | "HC32F19x"
                    | "HC32L07x"
                    | "HC32L13x"
                    | "HC32L16x"
                    | "HC32L17x"
                    | "HC32L18x"
                    | "HC32L19x",
                "TIM4A" | "TIM4B" | "TIM5A" | "TIM5B" | "TIM6A" | "TIM6B"
            ) | (
                "HC32F030" | "HC32F17x" | "HC32F19x",
                "LPUART0RX" | "LPUART0TX" | "LPUART1RX" | "LPUART1TX"
            ) | ("HC32F030", "LCDTX")
                | ("HC32F431" | "HC32M413", "HALCAP" | "HALOVF")
                | ("HC32F334" | "HC32F336", "HRPWMEEDET")
        )
}
