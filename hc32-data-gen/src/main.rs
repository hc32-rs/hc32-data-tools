//! 读取两个数据仓库（hc32-ddl-data / hc32-ll-data）的 YAML，
//! 解析 include 链后产出 `build/data/chips/<PART>.json` 与
//! `build/data/registers/<kind>_<version>.json`，供 hc32-metapac-gen 使用。

use std::path::{Path, PathBuf};

mod dma;
mod registers;

struct Stopwatch {
    start: std::time::Instant,
    section_start: Option<std::time::Instant>,
}

impl Stopwatch {
    fn new() -> Self {
        eprintln!("Starting timer");
        let start = std::time::Instant::now();
        Self {
            start,
            section_start: None,
        }
    }

    fn section(&mut self, status: &str) {
        let now = std::time::Instant::now();
        self.print_done(now);
        eprintln!("  {status}");
        self.section_start = Some(now);
    }

    fn stop(self) {
        let now = std::time::Instant::now();
        self.print_done(now);
        let total_elapsed = now - self.start;
        eprintln!("Total time: {:.2} seconds", total_elapsed.as_secs_f32());
    }

    fn print_done(&self, now: std::time::Instant) {
        if let Some(section_start) = self.section_start {
            let elapsed = now - section_start;
            eprintln!("    done in {:.2} seconds", elapsed.as_secs_f32());
        }
    }
}

/// 一个数据仓库的 data/ 目录（hc32-ddl-data 或 hc32-ll-data）。
pub struct DataRoot {
    pub label: String,
    pub dir: PathBuf,
}

/// 数据根：默认取工作区内两个兄弟仓库，可用 HC32_DATA_ROOTS 覆盖
/// （格式 `label=path`，多项用冒号分隔）。
fn data_roots() -> Vec<DataRoot> {
    if let Ok(spec) = std::env::var("HC32_DATA_ROOTS") {
        return spec
            .split(':')
            .filter(|s| !s.is_empty())
            .map(|s| {
                let (label, path) = s
                    .split_once('=')
                    .unwrap_or_else(|| panic!("HC32_DATA_ROOTS 项须为 label=path: {s}"));
                DataRoot {
                    label: label.to_string(),
                    dir: PathBuf::from(path),
                }
            })
            .collect();
    }
    [
        ("ddl", "../hc32-ddl-data/data"),
        ("ll", "../hc32-ll-data/data"),
    ]
    .into_iter()
    .map(|(label, path)| DataRoot {
        label: label.to_string(),
        dir: PathBuf::from(path),
    })
    .collect()
}

fn main() -> anyhow::Result<()> {
    let mut stopwatch = Stopwatch::new();

    let roots = data_roots();
    for root in &roots {
        anyhow::ensure!(
            root.dir.join("chips").is_dir(),
            "data root {} not found under {} (须在 hc32-data-tools 仓库根目录运行)",
            root.label,
            root.dir.display()
        );
    }

    stopwatch.section("Parsing registers");
    let registers = registers::Registers::parse(&roots)?;
    registers.write()?;

    stopwatch.section("Parsing chips");
    let mut chip_count = 0usize;
    let mut dma_unknown = Vec::new();
    let mut dma_skipped = 0;
    let mut aliased_dma = 0usize;
    let mut no_version = Vec::new();

    std::fs::create_dir_all("build/data/chips")?;

    for root in &roots {
        let mut chip_meta_files: Vec<_> = std::fs::read_dir(root.dir.join("chips"))
            .unwrap()
            .filter_map(|res| res.unwrap().file_name().to_str().map(|s| s.to_string()))
            .filter(|s| s.ends_with(".yaml"))
            .filter(|s| s.starts_with("HC32"))
            .map(|s| s.strip_suffix(".yaml").unwrap().to_string())
            .collect();
        chip_meta_files.sort();

        println!("{} chips: {}", root.label, chip_meta_files.len());

        for name in &chip_meta_files {
            let meta_yaml_path = root.dir.join(&format!("chips/{}.yaml", name));
            let content = std::fs::read_to_string(&meta_yaml_path)?;
            let mut chip: hc32_data_serde::Chip = serde_yaml::from_str(&content)?;

            resolve_memory(&meta_yaml_path, &mut chip)?;

            // 已知数据缺口（F120/M120 无 DS、F155/A472 版式特殊）：
            // 无内存布局的芯片先跳过，数据补齐后自动进入生成
            if chip.memory.is_empty() {
                eprintln!("WARNING: chip {} has no memory regions, skipped", chip.name);
                continue;
            }

            // handle include_x
            for core in &mut chip.cores {
                if let Some(inc_path) = core.include_interrupts.take() {
                    let interrupts_yaml_path = meta_yaml_path.parent().unwrap().join(&inc_path);
                    let content = std::fs::read_to_string(&interrupts_yaml_path)?;
                    // Mapping 保留文件顺序 = 设备头 IRQn 枚举顺序；
                    // 同号别名/共享向量（如 INT000/SWINT0、BTIM3_4_5/GTIM）
                    // 保留枚举里最先出现的主名
                    let interrupts: serde_yaml::Mapping = serde_yaml::from_str(&content)?;
                    let mut seen: std::collections::BTreeSet<u8> =
                        std::collections::BTreeSet::new();
                    for (k, v) in interrupts {
                        let name = k.as_str().unwrap_or_default().to_string();
                        let number = v.as_u64().unwrap() as u8;
                        if !seen.insert(number) {
                            eprintln!(
                                "WARNING: chip {} irq {} shares number {} with an earlier name, dropped",
                                chip.name, name, number
                            );
                            continue;
                        }
                        core.interrupts
                            .push(hc32_data_serde::chip::core::Interrupt { name, number });
                    }
                    core.interrupts.sort_by_key(|i| i.number);
                }

                // append peripherals from includes
                if let Some(inc_paths) = &mut core.include_peripherals.take() {
                    for inc_path in inc_paths {
                        let peripheral_yaml_path = meta_yaml_path.parent().unwrap().join(&inc_path);
                        let content = std::fs::read_to_string(&peripheral_yaml_path)?;
                        let peripherals: Vec<hc32_data_serde::chip::core::Peripheral> =
                            serde_yaml::from_str(&content)?;
                        core.peripherals.extend(peripherals);
                    }
                }

                // Names are assigned upstream; unresolved instances retain the existing address-only fallback.
                // MODE 拆分定时器走 views 列表（物理项 registers=None），
                // 每视图自带 kind/version，不是降级对象。
                for p in &mut core.peripherals {
                    if !p.views.is_empty() {
                        continue;
                    }
                    let Some(regs) = &mut p.registers else {
                        continue;
                    };
                    let Some(_version) = regs.version.as_ref() else {
                        no_version.push(format!("{} {} ({})", name, p.name, regs.kind));
                        p.registers = None;
                        continue;
                    };
                }
            }

            // DMA includes（信号 → request 号挂到外设）
            let (unmatched, aliased) = dma::handle_chip_dma_include(&meta_yaml_path, &mut chip)?;
            aliased_dma += aliased;
            for (dma, signal) in unmatched {
                if dma::is_known_unmatched(&chip.family, &signal) {
                    dma_skipped += 1;
                } else {
                    dma_unknown.push(format!("{name} {dma}:{signal}"));
                }
            }

            println!(
                "chip: {}, peripherals: {}",
                chip.name,
                chip.cores[0].peripherals.len()
            );
            let dump = serde_json::to_string_pretty(&chip)?;
            std::fs::write(format!("build/data/chips/{name}.json"), dump)?;
            chip_count += 1;
        }
    }

    stopwatch.section("Validating chip register refs");
    let mut missing: Vec<String> = Vec::new();
    for f in glob::glob("build/data/chips/*.json")?.flatten() {
        let chip: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&f)?)?;
        for core in chip["cores"].as_array().unwrap() {
            for p in core["peripherals"].as_array().unwrap() {
                let mut refs: Vec<(String, String, String)> = Vec::new();
                if let Some(r) = p["registers"].as_object() {
                    refs.push((
                        r["kind"].as_str().unwrap().to_string(),
                        r["version"].as_str().unwrap().to_string(),
                        p["name"].as_str().unwrap_or("?").to_string(),
                    ));
                }
                // MODE 视图：每视图一个 (kind, version) 引用，逐条校验
                for v in p["views"].as_array().into_iter().flatten() {
                    refs.push((
                        v["kind"].as_str().unwrap().to_string(),
                        v["version"].as_str().unwrap().to_string(),
                        format!(
                            "{}::{}",
                            p["name"].as_str().unwrap_or("?"),
                            v["name"].as_str().unwrap_or("?")
                        ),
                    ));
                }
                for (kind, version, inst) in refs {
                    let key = format!("{}_{}.json", kind, version);
                    if !Path::new("build/data/registers").join(&key).exists() {
                        missing.push(format!(
                            "{}: {} ({})",
                            f.file_name().unwrap().to_string_lossy(),
                            key,
                            inst
                        ));
                    }
                }
            }
        }
    }
    if !missing.is_empty() {
        return Err(anyhow::anyhow!(
            "chips reference missing register files:\n  {}",
            missing.join("\n  ")
        ));
    }

    // DMA 未匹配汇总：系统事件 + dmac.h 共享模板幽灵项静默计数，其余逐条列出
    if dma_skipped > 0 {
        eprintln!(
            "INFO: {} dma signals are system events or shared-template phantom entries \
             (PORT_EIRQ/EVENT_PORT/LVD/LCDTX/HAL*/HRPWM_EEDET, TIM4-6/LPUART on chips \
             without that hardware) - not attached, expected",
            dma_skipped
        );
    }
    if aliased_dma > 0 {
        eprintln!(
            "INFO: {} dma requests attached via confirmed series alias \
             (HC32A052/F052 UART0/UART1 -> USART0/USART1, dmac.h+RM TRI_SEL evidence)",
            aliased_dma
        );
    }
    if !dma_unknown.is_empty() {
        eprintln!(
            "WARNING: {} dma signals matched no peripheral (unknown source, review):\n  {}",
            dma_unknown.len(),
            dma_unknown.join("\n  ")
        );
    }
    if !no_version.is_empty() {
        eprintln!(
            "WARNING: {} peripherals have registers without version (SVD/head instance name \
             mismatch), downgraded to address-only:\n  {}",
            no_version.len(),
            no_version.join("\n  ")
        );
    }
    eprintln!("total chips: {}", chip_count);

    stopwatch.stop();

    Ok(())
}

fn resolve_memory(meta_yaml_path: &Path, chip: &mut hc32_data_serde::Chip) -> anyhow::Result<()> {
    let base_dir = meta_yaml_path.parent().unwrap();
    chip.resolve_memory(base_dir, |abs_path| std::fs::read_to_string(abs_path))
        .map_err(|msg| anyhow::anyhow!("{}", msg))
}
