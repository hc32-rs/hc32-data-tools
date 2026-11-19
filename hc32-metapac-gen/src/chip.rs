use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fmt::Write as _;
use std::fs;
use std::fs::File;
use std::io::Write;
use std::path::Path;

use chiptool::{generate, ir, transform};
use regex::Regex;

use crate::data::{Chip, Core};
use crate::memory::gen_memory_files;
use crate::{Gen, gen_opts, stringify};

fn build_chiptool_ir(
    core: &Core,
    core_index: usize,
) -> (
    ir::IR,
    BTreeSet<(String, String)>,
    String,
    BTreeMap<String, String>,
) {
    let mut ir = ir::IR::new();

    let mut dev = ir::Device {
        interrupts: Vec::new(),
        peripherals: Vec::new(),
        nvic_priority_bits: None, // FIXME: not extracted for hc32 yet
    };

    // (kind, version) 对：同一芯片内同一外设 kind 允许多版本（如 MODE 拆分的定时器），
    // 模块名统一用 `<kind>_<version>` 避免冲突。
    let mut peripheral_versions: BTreeSet<(String, String)> = BTreeSet::new();

    // MODE 视图 const 的 doc 文本（视图名 → 模式切换约束说明），postprocess 注入。
    let mut view_docs: BTreeMap<String, String> = BTreeMap::new();

    for p in &core.peripherals {
        if !p.views.is_empty() {
            // MODE 拆分定时器：每视图一条同基址类型化 IR 外设（const 名 = SVD 视图名），
            // 每个视图提供类型化入口，同时保留原物理实例的 PAC 地址常量。
            for v in &p.views {
                peripheral_versions.insert((v.registers.kind.clone(), v.registers.version.clone()));
                let ir_peri = ir::Peripheral {
                    name: v.name.clone(),
                    array: None,
                    base_address: p.address,
                    block: Some(format!(
                        "{}_{}::{}",
                        v.registers.kind, v.registers.version, v.registers.block
                    )),
                    description: None,
                    interrupts: std::collections::BTreeMap::new(),
                };
                dev.peripherals.push(ir_peri);
                view_docs.insert(
                    v.name.clone(),
                    format!(
                        "Register-layout view `{v_name}` of physical timer `{p_name}` \
                         (mode {v_mode}, same base address).\n\
                         All MODE views alias the same physical timer: the working mode is \
                         selected at runtime via the control register MD bit-field, and the \
                         address, clock gate and interrupts are shared with `{p_name}`.\n\
                         Use the view that matches the mode you configured; do not access \
                         two views of the same timer concurrently.",
                        v_name = v.name,
                        p_name = p.name,
                        v_mode = v.mode,
                    ),
                );
            }
        }

        let mut ir_peri = ir::Peripheral {
            name: p.name.clone(),
            array: None,
            base_address: p.address,
            block: None,
            description: None,
            interrupts: std::collections::BTreeMap::new(),
        };

        if let Some(bi) = &p.registers {
            peripheral_versions.insert((bi.kind.clone(), bi.version.clone()));
            ir_peri.block = Some(format!("{}_{}::{}", bi.kind, bi.version, bi.block));
        }

        dev.peripherals.push(ir_peri);
    }

    // HC32 的中断号来自设备头 IRQn 枚举，已是 CMSIS 0 基外部中断号，
    // 无需 ch32 的 -16 调整。
    for irq in &core.interrupts {
        dev.interrupts.push(ir::Interrupt {
            name: irq.name.clone(),
            description: None,
            value: irq.number,
        });
    }

    ir.devices.insert("".to_string(), dev);

    let mut extra = String::new();

    for (module, version) in &peripheral_versions {
        writeln!(
            &mut extra,
            "#[path=\"../../peripherals/{}_{}.rs\"] pub mod {}_{};",
            module, version, module, version
        )
        .unwrap();
    }
    writeln!(&mut extra, "pub const CORE_INDEX: usize = {};", core_index).unwrap();

    writeln!(
        &mut extra,
        "pub const MEMORY_LAYOUT: crate::mem_layout::MemoryLayout = \
         crate::mem_layout::MemoryLayout {{ regions: crate::memory_select::MEMORY }};",
    )
    .unwrap();

    // (ir, peripheral_versions, extra, view_docs) 4 元组：视图 doc 由 gen_chip 注入。
    (ir, peripheral_versions, extra, view_docs)
}

/// 把视图 doc 文本注入 pac.rs：在 `pub const <VIEW> :` 前插入 `#[doc = "..."]`。
/// chiptool 不渲染外设 description 为 doc，故在此后处理。
fn inject_view_docs(pac: String, view_docs: &BTreeMap<String, String>) -> String {
    let mut out = pac;
    for (name, doc) in view_docs {
        let pat = format!("pub const {} :", name);
        let esc = doc.replace('\\', "\\\\").replace('"', "\\\"");
        let replacement = format!("#[doc = \"{}\"] {}", esc, pat);
        out = out.replace(&pat, &replacement);
    }
    out
}

fn postprocess_pac_rs(s: String) -> String {
    let data = s.replace("] ", "]\n");
    // The metapac does not depend on cortex-m-rt; bins bring their own runtime.
    let data = data.replace("pub use cortex_m_rt :: interrupt ;", "");

    // Remove inner attributes like #![no_std]
    let data = Regex::new("# *! *\\[.*\\]")
        .unwrap()
        .replace_all(&data, "")
        .to_string();

    // chiptool emits edition-2024 syntax (`unsafe extern`, unsafe attributes),
    // so no further rewriting is needed here.

    data
}

fn render_metadata_rs(
    chip: &Chip,
    core: &Core,
    peripheral_versions: &BTreeSet<(String, String)>,
    metadata_dedup: &mut HashMap<String, String>,
    out_dir: &Path,
) -> String {
    let mut data = String::new();
    write!(
        &mut data,
        "
                pub(crate) static PERIPHERALS: &[Peripheral] = {};
                pub(crate) static INTERRUPTS: &[Interrupt] = {};
                pub(crate) static DMA_CHANNELS: &[DmaChannel] = {};
            ",
        stringify(&core.peripherals),
        stringify(&core.interrupts),
        stringify(&core.dma_channels),
    )
    .unwrap();

    let n = metadata_dedup.len();
    let deduped_file = metadata_dedup.entry(data.clone()).or_insert_with(|| {
        let ir_regex = Regex::new("\":ir_for:([a-z0-9_]+):\"").unwrap();
        let mut data = ir_regex.replace_all(&data, "&$1::REGISTERS").to_string();

        // Debug 输出的枚举变体是裸名（`Fixed {`），Rust 2024 要求路径限定。
        // 给 kernel_clock 的变体补 `PeripheralKernelClock::` 前缀。
        let kc_regex =
            Regex::new(r"(?m)^(\s*)(Fixed|Selective|Selectable) \{").unwrap();
        data = kc_regex
            .replace_all(&data, "${1}PeripheralKernelClock::$2 {")
            .to_string();

        for (module, version) in peripheral_versions {
            writeln!(
                &mut data,
                "#[path=\"../registers/{}_{}.rs\"] pub mod {}_{};",
                module, version, module, version
            )
            .unwrap();
        }

        let file = format!("metadata_{:04}.rs", n);
        let path = out_dir.join("src/chips").join(&file);
        fs::write(path, data).unwrap();

        file
    });

    format!(
        "include!(\"../{}\");
            pub static METADATA: Metadata = Metadata {{
                name: {:?},
                family: {:?},
                memory: crate::memory_select::MEMORY,
                peripherals: PERIPHERALS,
                interrupts: INTERRUPTS,
                dma_channels: DMA_CHANNELS,
            }};",
        deduped_file, &chip.name, &chip.family,
    )
}

impl Gen {
    pub(crate) fn gen_chip(
        &mut self,
        chip_core_name: &str,
        chip: &Chip,
        core: &Core,
        core_index: usize,
    ) {
        let (mut ir, peripheral_versions, extra, view_docs) = build_chiptool_ir(core, core_index);

        for (module, version) in &peripheral_versions {
            self.all_peripheral_versions
                .insert((module.clone(), version.clone()));
        }

        // Cleanups!
        transform::sort::Sort {}.run(&mut ir).unwrap();
        crate::sanitize().run(&mut ir).unwrap();

        let chip_dir = self
            .opts
            .out_dir
            .join("src/chips")
            .join(chip_core_name.to_ascii_lowercase());
        fs::create_dir_all(&chip_dir).unwrap();

        // pac.rs
        let rendered = generate::render(&ir, &gen_opts()).unwrap().to_string();
        let mut pac = postprocess_pac_rs(rendered);
        pac = inject_view_docs(pac, &view_docs);
        let mut file = File::create(chip_dir.join("pac.rs")).unwrap();
        file.write_all(pac.as_bytes()).unwrap();
        file.write_all(extra.as_bytes()).unwrap();

        // device.x
        let mut device_x = String::new();
        for irq in &core.interrupts {
            writeln!(&mut device_x, "PROVIDE({} = DefaultHandler);", irq.name).unwrap();
        }
        File::create(chip_dir.join("device.x"))
            .unwrap()
            .write_all(device_x.as_bytes())
            .unwrap();

        // metadata.rs
        let out_dir = self.opts.out_dir.clone();
        let metadata = render_metadata_rs(
            chip,
            core,
            &peripheral_versions,
            &mut self.metadata_dedup,
            &out_dir,
        );
        let mut file = File::create(chip_dir.join("metadata.rs")).unwrap();
        file.write_all(metadata.as_bytes()).unwrap();

        // memory_x/default/{regions,memory.rs} + _default
        gen_memory_files(&chip_dir, chip);
    }
}
