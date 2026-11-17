use std::collections::BTreeMap;

use anyhow::anyhow;
use chiptool::ir::IR;
use chiptool::validate;

use crate::DataRoot;

/// 合并后的寄存器表：键为文件名主干（`<kind>_<version>`）。
pub struct Registers {
    pub registers: BTreeMap<String, IR>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let stamp = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir()
                .join(format!("hc32-register-map-{}-{stamp}", std::process::id()));
            std::fs::create_dir_all(path.join("data/registers")).unwrap();
            std::fs::create_dir_all(path.join("docs")).unwrap();
            std::fs::write(
                path.join("data/registers/adc_ddl_v1.yaml"),
                "block/ADC:\n  items: []\n",
            )
            .unwrap();
            std::fs::write(
                path.join("docs/peripheral-version-map.json"),
                r#"{
                "stage":"active", "side":"ddl", "layouts":[{
                    "source":"adc_ddl_v1", "kind":"adc", "version":"v1", "status":"active"
                }] }"#,
            )
            .unwrap();
            Self(path)
        }
        fn root(&self) -> DataRoot {
            DataRoot {
                label: "ddl".into(),
                dir: self.0.join("data"),
            }
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn unmapped_legacy_file_is_an_error_not_an_automatic_version() {
        let fixture = Fixture::new();
        std::fs::write(
            fixture.0.join("data/registers/adc_v999.yaml"),
            "block/ADC:\n  items: []\n",
        )
        .unwrap();
        let error = Registers::parse(&[fixture.root()])
            .err()
            .unwrap()
            .to_string();
        assert!(error.contains("unreviewed register file"), "{error}");
    }

    #[test]
    fn missing_and_duplicate_names_are_errors() {
        let fixture = Fixture::new();
        assert!(Registers::parse(&[fixture.root()]).is_ok());
        let error = Registers::parse(&[fixture.root(), fixture.root()])
            .err()
            .unwrap()
            .to_string();
        assert!(error.contains("duplicate register name"), "{error}");
        std::fs::remove_file(fixture.0.join("data/registers/adc_ddl_v1.yaml")).unwrap();
        assert!(Registers::parse(&[fixture.root()]).is_err());
    }
}

impl Registers {
    pub fn parse(roots: &[DataRoot]) -> Result<Self, anyhow::Error> {
        let mut registers: BTreeMap<String, IR> = BTreeMap::new();

        for root in roots {
            let manifest_path = root
                .dir
                .parent()
                .unwrap()
                .join("docs/peripheral-version-map.json");
            let manifest: serde_json::Value =
                serde_json::from_slice(&std::fs::read(&manifest_path)?)?;
            anyhow::ensure!(
                manifest["stage"] == "active",
                "inactive version map: {}",
                manifest_path.display()
            );
            let layouts = manifest["layouts"]
                .as_array()
                .ok_or_else(|| anyhow!("missing layouts in {}", manifest_path.display()))?;
            let mut expected = BTreeMap::new();
            for layout in layouts {
                let source = layout["source"]
                    .as_str()
                    .ok_or_else(|| anyhow!("missing source in version map"))?;
                anyhow::ensure!(
                    expected.insert(source.to_owned(), layout).is_none(),
                    "duplicate mapping: {source}"
                );
            }
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
                let layout = expected
                    .remove(&stem)
                    .ok_or_else(|| anyhow!("unreviewed register file: {stem}"))?;
                if layout["status"] == "active" {
                    let canonical = format!(
                        "{}_{}_{}",
                        layout["kind"].as_str().unwrap(),
                        manifest["side"].as_str().unwrap(),
                        layout["version"].as_str().unwrap()
                    );
                    anyhow::ensure!(stem == canonical, "noncanonical register name: {stem}");
                } else {
                    anyhow::ensure!(layout["status"] == "excluded", "unapproved layout: {stem}");
                }
                hc32_data_serde::register_name::split_register_name(&stem)
                    .ok_or_else(|| anyhow!("invalid register filename: {f:?}"))?;

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

                if registers.insert(stem.clone(), ir).is_some() {
                    return Err(anyhow!(
                        "duplicate register name {stem}; fix the explicit mapping"
                    ));
                }
            }
            anyhow::ensure!(
                expected.is_empty(),
                "missing register files: {:?}",
                expected.keys().collect::<Vec<_>>()
            );
        }

        Ok(Self { registers })
    }

    pub fn write(&self) -> Result<(), anyhow::Error> {
        std::fs::create_dir_all("build/data/registers")?;
        for entry in std::fs::read_dir("build/data/registers")? {
            let path = entry?.path();
            if path.extension().and_then(|s| s.to_str()) == Some("json")
                && !self
                    .registers
                    .contains_key(path.file_stem().unwrap().to_str().unwrap())
            {
                std::fs::remove_file(path)?;
            }
        }

        for (name, ir) in &self.registers {
            let dump = serde_json::to_string_pretty(ir)?;
            std::fs::write(format!("build/data/registers/{name}.json"), dump)?;
        }
        Ok(())
    }
}
