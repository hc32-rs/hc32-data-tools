use std::{collections::BTreeMap, path::Path};

/// 处理 cores[].include_dma_channels（`DMA1: ../dma/<SERIES>_DMA1.yaml`）。
///
/// HC32 的语义与 ch32 的固定通道模型不同，两代驱动分别是：
/// - DDL：值 = DMAC_CONFAx.TRI_SEL 全局触发源索引；
/// - LL：值 = EVT_SRC_* 事件号（9 位），写 AOS->DMAx_TRGSELy。
/// 两者都只把 request 号挂到外设上，不推断物理通道。
///
/// 注意：core.dma_channels（控制器通道清单）暂不生成——通道数因系列而异，
/// 待从数据手册补一张每系列通道数表后再填。
pub fn handle_chip_dma_include<P: AsRef<Path>>(
    path: P,
    chip: &mut hc32_data_serde::Chip,
) -> anyhow::Result<Vec<String>> {
    let meta_yaml_path = path.as_ref();
    let mut unmatched: Vec<String> = Vec::new();

    for core in &mut chip.cores {
        let Some(dma_channels_inc) = core.include_dma_channels.take() else {
            continue;
        };

        for (dma, inc_path) in dma_channels_inc {
            let dma_yaml_path = meta_yaml_path.parent().unwrap().join(&inc_path);
            let content = std::fs::read_to_string(&dma_yaml_path)?;
            let dma_map: BTreeMap<String, u16> = serde_yaml::from_str(&content)?;

            for (signal, request) in dma_map {
                match find_peripheral(&mut core.peripherals, &signal) {
                    Some((periph, signal_name)) => {
                        periph.dma_channels.push(
                            hc32_data_serde::chip::core::peripheral::DmaChannel {
                                signal: signal_name.to_string(),
                                dma: Some(dma.clone()),
                                channel: None,
                                request: Some(request),
                            },
                        );
                    }
                    None => unmatched.push(format!("{}:{}", dma, signal)),
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

    Ok(unmatched)
}

/// 在信号名里找最长匹配的外设实例前缀（忽略下划线差异：
/// `LP_UART0_RX` 对 `LPUART0`、`ADCJQR` 对 `ADC`）。
/// 返回 (外设, 规范化后的信号名)；`DAC0` → (DAC0, "DAC0")。
fn find_peripheral<'a>(
    peripherals: &'a mut [hc32_data_serde::chip::core::Peripheral],
    signal: &str,
) -> Option<(&'a mut hc32_data_serde::chip::core::Peripheral, String)> {
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
        (&mut peripherals[i], signal_name)
    })
}
