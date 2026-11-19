use std::collections::{HashMap, HashSet};
use std::fmt::Debug;
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::str::FromStr;

use chiptool::generate;
use chiptool::generate::CommonModule;
use chiptool::transform;
use proc_macro2::TokenStream;

mod chip;
mod data;
mod memory;
mod peripheral;

use data::Chip;

pub struct Options {
    pub chips: Vec<String>,
    pub out_dir: PathBuf,
    pub data_dir: PathBuf,
}

pub struct Gen {
    pub(crate) opts: Options,
    pub(crate) all_peripheral_versions: HashSet<(String, String)>,
    pub(crate) metadata_dedup: HashMap<String, String>,
}

impl Gen {
    pub fn new(opts: Options) -> Self {
        Self {
            opts,
            all_peripheral_versions: HashSet::new(),
            metadata_dedup: HashMap::new(),
        }
    }

    fn load_chip(&mut self, name: &str) -> Chip {
        let chip_path = self
            .opts
            .data_dir
            .join("chips")
            .join(format!("{}.json", name));
        let chip = fs::read(chip_path).unwrap_or_else(|_| panic!("Could not load chip {}", name));
        serde_json::from_slice(&chip).unwrap()
    }

    pub fn gen(&mut self) {
        // These directories are generator-owned. Remove old module names even when
        // invoking this generator directly instead of the workflow wrapper.
        for directory in ["src/peripherals", "src/registers", "src/chips"] {
            let path = self.opts.out_dir.join(directory);
            if path.exists() {
                fs::remove_dir_all(&path).unwrap();
            }
            fs::create_dir_all(path).unwrap();
        }
        self.all_peripheral_versions.clear();
        self.metadata_dedup.clear();
        // Include reviewed layouts not yet referenced by chip metadata (MODE views).
        for entry in fs::read_dir(self.opts.data_dir.join("registers")).unwrap() {
            let path = entry.unwrap().path();
            if path.extension().and_then(|s| s.to_str()) != Some("json") {
                continue;
            }
            let stem = path.file_stem().unwrap().to_str().unwrap();
            let (kind, version) = hc32_data_serde::register_name::split_register_name(stem)
                .unwrap_or_else(|| panic!("invalid register filename: {stem}"));
            self.all_peripheral_versions
                .insert((kind.to_owned(), version.to_owned()));
        }

        let mut chip_core_names: Vec<String> = Vec::new();

        for chip_name in &self.opts.chips.clone() {
            println!("Generate Chip {}", chip_name);

            let mut chip = self.load_chip(chip_name);

            // Cleanup
            for core in &mut chip.cores {
                for irq in &mut core.interrupts {
                    irq.name = irq.name.to_ascii_uppercase();
                }
                for p in &mut core.peripherals {
                    for irq in &mut p.interrupts {
                        irq.interrupt = irq.interrupt.to_ascii_uppercase();
                    }

                    if let Some(registers) = &mut p.registers {
                        registers.ir = format!(":ir_for:{}_{}:", registers.kind, registers.version);
                    }
                    // MODE 视图各挂自己的寄存器表引用
                    for v in &mut p.views {
                        v.registers.ir =
                            format!(":ir_for:{}_{}:", v.registers.kind, v.registers.version);
                    }
                }
            }

            // Generate
            for (core_index, core) in chip.cores.iter().enumerate() {
                let chip_core_name = match chip.cores.len() {
                    1 => chip_name.clone(),
                    _ => format!("{}-{}", chip_name, core.name),
                };

                chip_core_names.push(chip_core_name.clone());
                self.gen_chip(&chip_core_name, &chip, core, core_index)
            }
        }

        for (module, version) in &self.all_peripheral_versions {
            peripheral::gen_peripheral(&self.opts.out_dir, &self.opts.data_dir, module, version);
        }

        // Generate Cargo.toml
        let mut contents = include_bytes!("../res/Cargo.toml").to_vec();
        for name in &chip_core_names {
            writeln!(&mut contents, "{} = []", name.to_ascii_lowercase()).unwrap();
        }
        fs::write(self.opts.out_dir.join("Cargo.toml"), contents).unwrap();

        // copy misc files
        fs::write(
            self.opts.out_dir.join("build.rs"),
            include_bytes!("../res/build.rs"),
        )
        .unwrap();
        fs::write(
            self.opts.out_dir.join(".gitignore"),
            include_bytes!("../res/.gitignore"),
        )
        .unwrap();
        fs::write(
            self.opts.out_dir.join("README.md"),
            include_bytes!("../res/README.md"),
        )
        .unwrap();
        fs::write(
            self.opts.out_dir.join("memory_x_render.rs"),
            include_bytes!("../res/memory_x_render.rs"),
        )
        .unwrap();
        fs::write(
            self.opts.out_dir.join("src/lib.rs"),
            include_bytes!("../res/src/lib.rs"),
        )
        .unwrap();
        fs::write(
            self.opts.out_dir.join("src/common.rs"),
            chiptool::generate::COMMON_MODULE,
        )
        .unwrap();
        fs::write(
            self.opts.out_dir.join("src/metadata.rs"),
            include_bytes!("../res/src/metadata.rs"),
        )
        .unwrap();
        fs::write(
            self.opts.out_dir.join("src/mem_layout.rs"),
            include_bytes!("../res/src/mem_layout.rs"),
        )
        .unwrap();

        // host 元数据验收测试（build/ 是生成缓存，模板在 res/tests/ 落盘，
        // nightly 强推后的 crate 仍带 tests/，CI 的 cargo test 才有得跑）。
        fs::create_dir_all(self.opts.out_dir.join("tests")).unwrap();
        for entry in fs::read_dir(concat!(env!("CARGO_MANIFEST_DIR"), "/res/tests")).unwrap() {
            let path = entry.unwrap().path();
            if path.extension().and_then(|s| s.to_str()) == Some("rs") {
                let dest = self
                    .opts
                    .out_dir
                    .join("tests")
                    .join(path.file_name().unwrap());
                fs::copy(&path, &dest).unwrap();
            }
        }
    }
}

pub(crate) fn stringify<T: Debug>(metadata: T) -> String {
    let mut metadata = format!("{:#?}", metadata);
    if metadata.starts_with('[') {
        metadata = format!("&{}", metadata);
    }

    metadata.replace(": [", ": &[")
}

fn gen_opts() -> generate::Options {
    generate::Options::new()
        .with_common_module(CommonModule::External(
            TokenStream::from_str("crate::common").unwrap(),
        ))
        // The generated crate has no defmt feature; keep the previous output.
        .with_defmt(generate::DefmtOption::Disabled)
}

/// Sanitizer settings matching the pre-upgrade output.
///
/// chiptool's default enum-variant case changed from upper snake to Pascal
/// along with the sanitizer rewrite. The PAC's `vals` types are part of the
/// HAL API, so keep the upper snake variants.
pub(crate) fn sanitize() -> transform::sanitize::Sanitize {
    use transform::sanitize::{Sanitize, SanitizeCase};

    Sanitize {
        enum_variant: SanitizeCase::Constant,
        ..Sanitize::default()
    }
}
