use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

pub mod chip;
mod resolve;
mod size;
pub use size::{deserialize_opt_size_with_suffix, parse_size_with_suffix};

/// `data/chips/<PART>.yaml` 顶层结构（由 hc32-data-tools/scripts/csv_to_yaml.py 生成）。
///
/// 与 ch32-data 的 Chip 相比：
/// - 无 subfamily/product_type/keywords，无 NV（structs）与 ram-code 内存选项；
/// - packages 无 device_id；
/// - 内存区命名沿用小华手册的 FLASH/SRAM，而非 ch32 的 USR_1/RAM。
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Chip {
    pub name: String,
    pub family: String,
    pub packages: Vec<chip::Package>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub include_memory: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub memory: Vec<chip::Memory>,
    #[serde(
        default,
        deserialize_with = "deserialize_memory_sizes",
        skip_serializing_if = "BTreeMap::is_empty"
    )]
    pub memory_sizes: BTreeMap<String, u32>,

    pub docs: Vec<chip::Doc>,
    pub cores: Vec<chip::Core>,
}

pub(crate) fn default_true() -> bool {
    true
}

fn deserialize_memory_sizes<'de, D>(deserializer: D) -> Result<BTreeMap<String, u32>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    #[derive(Deserialize)]
    #[serde(transparent)]
    struct WrappedSize(#[serde(deserialize_with = "parse_size_with_suffix")] u32);

    let raw: BTreeMap<String, WrappedSize> = BTreeMap::deserialize(deserializer)?;
    Ok(raw.into_iter().map(|(k, WrappedSize(v))| (k, v)).collect())
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::*;

    fn check_file(path: impl AsRef<Path>) {
        println!("Checking {:?}", path.as_ref());
        let original = std::fs::read(path).unwrap();
        let parsed: Chip = serde_yaml::from_slice(&original).unwrap();
        // 确认 include 链之外的必填段都能解析
        assert!(!parsed.packages.is_empty());
        assert_eq!(1, parsed.cores.len());
    }

    // 数据在兄弟仓库 ../hc32-{ddl,ll}-data 中（本仓库的上一级）
    const DDL_CHIPS_DIR: &str = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../hc32-ddl-data/data/chips/"
    );
    const LL_CHIPS_DIR: &str = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../hc32-ll-data/data/chips/"
    );

    #[test]
    fn test_one() {
        let path = Path::new(DDL_CHIPS_DIR).join("HC32F003C4PA.yaml");
        check_file(path);
    }

    #[test]
    fn test_all() {
        use rayon::prelude::*;

        for dir in [DDL_CHIPS_DIR, LL_CHIPS_DIR] {
            Path::new(dir)
                .read_dir()
                .unwrap()
                .par_bridge()
                .for_each(|chip| {
                    check_file(chip.unwrap().path());
                });
        }
    }
}
