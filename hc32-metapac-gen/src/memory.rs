use std::fmt::Write as _;
use std::fs;
use std::path::Path;

use crate::data::{Access, Chip, MemoryRegion, MemoryRegionKind};
use crate::stringify;

pub(crate) fn access_str(access: Option<&Access>) -> String {
    match access {
        Some(a) => format!(
            "{}{}{}",
            if a.read { 'r' } else { '-' },
            if a.write { 'w' } else { '-' },
            if a.execute { 'x' } else { '-' },
        ),
        None => "rwx".to_string(),
    }
}

pub(crate) fn kind_str(kind: &MemoryRegionKind) -> &'static str {
    match kind {
        MemoryRegionKind::Flash => "flash",
        MemoryRegionKind::Ram => "ram",
    }
}

/// 每芯片单一 "default" 内存配置；HC32 无 ch32 的 ram-code 分配等内存选项。
pub(crate) fn gen_memory_files(out_dir: &Path, chip: &Chip) {
    let mem_root = out_dir.join("memory_x");
    if mem_root.exists() {
        fs::remove_dir_all(&mem_root).unwrap();
    }
    let opt_dir = mem_root.join("default");
    fs::create_dir_all(&opt_dir).unwrap();
    write_regions(&opt_dir.join("regions"), &chip.memory);
    write_memory_rs(&opt_dir.join("memory.rs"), &chip.memory);
    fs::write(mem_root.join("_default"), "default").unwrap();
}

// see `res/memory_x_render.rs` for the line format
fn write_regions(path: &Path, memory: &[MemoryRegion]) {
    let mut s = String::new();
    for r in memory {
        writeln!(
            s,
            "{} 0x{:08x} {} {} {}",
            r.name,
            r.address,
            r.size,
            access_str(r.access.as_ref()),
            kind_str(&r.kind),
        )
        .unwrap();
    }
    fs::write(path, s).unwrap();
}

fn write_memory_rs(path: &Path, memory: &[MemoryRegion]) {
    let body = format!(
        "use crate::mem_layout::{{Access, MemoryRegion, MemoryRegionKind, MemoryRole::*}};

pub static MEMORY: &[MemoryRegion] = {};
",
        stringify(memory),
    );
    fs::write(path, body).unwrap();
}
