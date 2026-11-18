use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

pub mod chip;
pub mod register_name;
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

    /// MODE 拆分定时器外设（views 列表）serde 往返：
    /// family YAML → Peripheral → YAML，views 键序与字段保持稳定。
    #[test]
    fn test_peripheral_views_roundtrip() {
        let src = r"- name: TIM0
  address: 0x40000C00
  views:
  - name: TIM0_MODE0
    mode: MODE0
    kind: tim
    version: ddl_v2
    block: TIM
  - name: TIM0_MODE1
    mode: MODE1
    kind: tim
    version: ddl_v3_1
    block: TIM
  - name: TIM0_MODE23
    mode: MODE23
    kind: tim
    version: ddl_v4_3
    block: TIM
  rcc:
    enable:
      register: PERI_CLKEN
      field: SysctrlPeripheralBaseTim
  interrupts:
  - signal: TIM0
    interrupt: TIM0
";
        let list: Vec<chip::core::Peripheral> = serde_yaml::from_str(src).unwrap();
        let p = list.into_iter().next().unwrap();
        assert_eq!("TIM0", p.name);
        assert!(p.registers.is_none());
        assert_eq!(3, p.views.len());
        assert_eq!("TIM0_MODE1", p.views[1].name);
        assert_eq!("MODE1", p.views[1].mode);
        assert_eq!("ddl_v3_1", p.views[1].version);
        let out = serde_yaml::to_string(&p).unwrap();
        let p2: chip::core::Peripheral = serde_yaml::from_str(&out).unwrap();
        assert_eq!(p, p2);
        // 多视图项不再输出无 version 的 registers 块
        assert!(!out.contains("registers:"));
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
