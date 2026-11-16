use std::fmt;
use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::{chip, Chip};

/// 内存 include 文件（data/memory/<SERIES>_F<x>S<y>.yaml）的可见字段。
#[derive(Clone, Debug, Eq, PartialEq, Deserialize)]
pub struct FamilyMemory {
    #[serde(default)]
    pub include_memory: Option<String>,
    #[serde(default)]
    pub memory: Vec<chip::Memory>,
}

fn load_recursive<F, E>(
    path: &Path,
    load_family: &mut F,
    visited: &mut Vec<PathBuf>,
) -> Result<Vec<chip::Memory>, String>
where
    F: FnMut(&Path) -> Result<String, E>,
    E: fmt::Display,
{
    let key = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    if visited.contains(&key) {
        let chain = visited
            .iter()
            .map(|p| p.display().to_string())
            .collect::<Vec<_>>()
            .join(" -> ");
        return Err(format!(
            "cycle in include_memory chain: {chain} -> {}",
            path.display()
        ));
    }
    visited.push(key);

    let content =
        load_family(path).map_err(|e| format!("read memory include {:?}: {e}", path.display()))?;
    let family: FamilyMemory = serde_yaml::from_str(&content)
        .map_err(|e| format!("parse memory include {:?}: {e}", path.display()))?;

    let mut combined = if let Some(parent) = family.include_memory {
        let parent_dir = path.parent().unwrap_or_else(|| Path::new("."));
        let parent_path = parent_dir.join(&parent);
        load_recursive(&parent_path, load_family, visited)?
    } else {
        Vec::new()
    };

    for region in family.memory {
        if combined.iter().any(|r| r.name == region.name) {
            return Err(format!(
                "memory region {:?} from {:?} collides with a region from a parent include \
                 (include_memory appends; each region must be declared exactly once in a chain)",
                region.name,
                path.display()
            ));
        }
        combined.push(region);
    }

    visited.pop();
    Ok(combined)
}

impl Chip {
    pub fn resolve_memory<F, E>(
        &mut self,
        chip_dir: &Path,
        mut load_family: F,
    ) -> Result<(), String>
    where
        F: FnMut(&Path) -> Result<String, E>,
        E: fmt::Display,
    {
        let include = self.include_memory.take();

        if let Some(inc_path) = include {
            let absolute = chip_dir.join(&inc_path);
            let mut visited = Vec::new();
            let regions = load_recursive(&absolute, &mut load_family, &mut visited)?;
            for region in regions {
                if self.memory.iter().any(|r| r.name == region.name) {
                    return Err(format!(
                        "memory region {:?} on chip {:?} collides with a region from {inc_path:?} \
                         (include_memory appends; sizes go through memory_sizes)",
                        region.name, self.name
                    ));
                }
                self.memory.push(region);
            }
        }

        for (region_name, size) in &self.memory_sizes {
            let region = self
                .memory
                .iter_mut()
                .find(|r| &r.name == region_name)
                .ok_or_else(|| {
                    format!(
                        "memory_sizes references unknown region {region_name:?} for chip {:?}",
                        self.name
                    )
                })?;
            region.size = Some(*size);
        }

        for r in &self.memory {
            if r.kind.is_none() || r.address.is_none() || r.size.is_none() {
                return Err(format!(
                    "memory region {:?} on chip {:?} is missing kind/address/size after resolution",
                    r.name, self.name
                ));
            }
        }

        Ok(())
    }
}
