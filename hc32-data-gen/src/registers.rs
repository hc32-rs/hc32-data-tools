use std::collections::{BTreeMap, HashMap};

use anyhow::anyhow;
use chiptool::ir::IR;
use chiptool::validate;

use crate::DataRoot;

/// 合并后的寄存器表：键为文件名主干（`<kind>_<version>`）。
pub struct Registers {
    pub registers: BTreeMap<String, IR>,
    /// (数据根, kind, 原版本) → 合并后版本。
    /// 两仓库各自独立编号（v1、v2…），同名但布局不同时，
    /// 后入库的根会被改派到空闲版本号，芯片侧经此表重定向。
    pub version_remaps: HashMap<(String, String, String), String>,
}

impl Registers {
    pub fn parse(roots: &[DataRoot]) -> Result<Self, anyhow::Error> {
        let mut registers: BTreeMap<String, IR> = BTreeMap::new();
        let mut version_remaps: HashMap<(String, String, String), String> = HashMap::new();
        // 每个 kind 已占用的版本号（用于冲突时分配新号）
        let mut used_versions: HashMap<String, std::collections::BTreeSet<String>> = HashMap::new();

        for root in roots {
            let pattern = root.dir.join("registers/*.yaml");
            let mut files: Vec<_> =
                glob::glob(pattern.to_str().unwrap())?.collect::<Result<Vec<_>, _>>()?;
            files.sort();
            if files.is_empty() {
                return Err(anyhow!("no register yaml under {}", root.dir.display()));
            }

            for f in files {
                let stem = f
                    .file_name()
                    .unwrap()
                    .to_string_lossy()
                    .strip_suffix(".yaml")
                    .unwrap()
                    .to_string();
                let (kind, version) = stem.rsplit_once('_').ok_or_else(|| {
                    anyhow!("register file {f:?}: expected <kind>_<version>.yaml")
                })?;

                let ir: IR = serde_yaml::from_str(&std::fs::read_to_string(&f)?)
                    .map_err(|e| anyhow!("failed to parse {f:?}: {e:?}"))?;

                // validate yaml file
                // we allow register overlap and field overlap for now
                let validate_option = validate::Options {
                    allow_register_overlap: true,
                    allow_field_overlap: true,
                    allow_enum_dup_value: false,
                    allow_unused_enums: false,
                    allow_unused_fieldsets: false,
                };
                let err_vec = validate::validate(&ir, validate_option);
                let err_string = err_vec.iter().fold(String::new(), |mut acc, cur| {
                    acc.push_str(cur);
                    acc.push('\n');
                    acc
                });

                if !err_string.is_empty() {
                    return Err(anyhow!(format!("\n{stem}:\n{err_string}")));
                }

                let used = used_versions.entry(kind.to_string()).or_default();
                used.insert(version.to_string());

                match registers.get(&stem) {
                    Some(existing) => {
                        // 同名文件已入库：布局一致则去重，不一致则改派新版本号
                        let same = serde_json::to_string(existing)? == serde_json::to_string(&ir)?;
                        let merged_version = if same {
                            version.to_string()
                        } else {
                            let mut n = 1;
                            loop {
                                let candidate = format!("v{n}");
                                if !used.contains(&candidate) {
                                    used.insert(candidate.clone());
                                    break candidate;
                                }
                                n += 1;
                            }
                        };
                        if !same {
                            eprintln!(
                                "registers conflict: {stem} differs between data roots; \
                                 remapping {}:{} to {}:{}",
                                root.label, stem, root.label, merged_version
                            );
                        }
                        if merged_version != version {
                            registers.insert(format!("{kind}_{merged_version}"), ir);
                        }
                        version_remaps.insert(
                            (root.label.clone(), kind.to_string(), version.to_string()),
                            merged_version,
                        );
                    }
                    None => {
                        registers.insert(stem.clone(), ir);
                        version_remaps.insert(
                            (root.label.clone(), kind.to_string(), version.to_string()),
                            version.to_string(),
                        );
                    }
                }
            }
        }

        Ok(Self {
            registers,
            version_remaps,
        })
    }

    pub fn write(&self) -> Result<(), anyhow::Error> {
        std::fs::create_dir_all("build/data/registers")?;

        for (name, ir) in &self.registers {
            let dump = serde_json::to_string_pretty(ir)?;
            std::fs::write(format!("build/data/registers/{name}.json"), dump)?;
        }
        Ok(())
    }
}
