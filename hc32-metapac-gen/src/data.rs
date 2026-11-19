use hc32_data_macros::EnumDebug;
use serde::Deserialize;

pub mod ir {
    use super::*;

    #[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
    pub struct IR {
        pub blocks: Vec<Block>,
        pub fieldsets: Vec<FieldSet>,
        pub enums: Vec<Enum>,
    }

    impl IR {
        pub fn from_chiptool(ir: chiptool::ir::IR) -> Self {
            let mut blocks: Vec<Block> = ir
                .blocks
                .iter()
                .map(|(name, block)| {
                    let items = block
                        .items
                        .iter()
                        .map(|item| BlockItem {
                            name: item.name.clone(),
                            description: item.description.clone(),
                            array: item.array.as_ref().map(|array| match &array {
                                chiptool::ir::Array::Regular(regular_array) => {
                                    Array::Regular(RegularArray {
                                        len: regular_array.len,
                                        stride: regular_array.stride,
                                    })
                                }
                                chiptool::ir::Array::Cursed(cursed_array) => {
                                    Array::Cursed(CursedArray {
                                        offsets: cursed_array.offsets.clone(),
                                    })
                                }
                            }),
                            byte_offset: item.byte_offset,
                            inner: match &item.inner {
                                chiptool::ir::BlockItemInner::Block(block) => {
                                    BlockItemInner::Block(BlockItemBlock {
                                        block: block.block.clone(),
                                    })
                                }
                                chiptool::ir::BlockItemInner::Register(register) => {
                                    BlockItemInner::Register(Register {
                                        access: match register.access {
                                            chiptool::ir::Access::Read => Access::Read,
                                            chiptool::ir::Access::ReadWrite => Access::ReadWrite,
                                            chiptool::ir::Access::Write => Access::Write,
                                        },
                                        bit_size: register.bit_size,
                                        fieldset: register.fieldset.as_ref().map(|fieldset| {
                                            fieldset.strip_prefix("regs::").unwrap().to_string()
                                        }),
                                    })
                                }
                            },
                        })
                        .collect();

                    #[allow(clippy::redundant_field_names)]
                    Block {
                        name: name.to_string(),
                        items: items,
                        extends: block.extends.clone(),
                        description: block.description.clone(),
                    }
                })
                .collect();

            blocks.sort_by_key(|b| b.name.clone());

            let mut fieldsets: Vec<FieldSet> = ir
                .fieldsets
                .iter()
                .map(|(name, fieldset)| {
                    let fields = fieldset
                        .fields
                        .iter()
                        .map(|field| Field {
                            name: field.name.clone(),
                            description: field.description.clone(),
                            bit_offset: match &field.bit_offset {
                                chiptool::ir::BitOffset::Regular(offset) => {
                                    BitOffset::Regular(RegularBitOffset { offset: *offset })
                                }
                                chiptool::ir::BitOffset::Cursed(ranges) => {
                                    BitOffset::Cursed(CursedBitOffset {
                                        ranges: ranges.clone(),
                                    })
                                }
                            },
                            bit_size: field.bit_size,
                            array: field.array.as_ref().map(|array| match &array {
                                chiptool::ir::Array::Regular(regular_array) => {
                                    Array::Regular(RegularArray {
                                        len: regular_array.len,
                                        stride: regular_array.stride,
                                    })
                                }
                                chiptool::ir::Array::Cursed(cursed_array) => {
                                    Array::Cursed(CursedArray {
                                        offsets: cursed_array.offsets.clone(),
                                    })
                                }
                            }),
                            enumm: field.enumm.as_ref().map(|fieldset| {
                                fieldset.strip_prefix("vals::").unwrap().to_string()
                            }),
                        })
                        .collect();

                    #[allow(clippy::redundant_field_names)]
                    FieldSet {
                        name: name.strip_prefix("regs::").unwrap().to_owned(),
                        fields: fields,
                        extends: fieldset.extends.clone(),
                        description: fieldset.description.clone(),
                        bit_size: fieldset.bit_size,
                    }
                })
                .collect();

            fieldsets.sort_by_key(|f| f.name.clone());

            let mut enums: Vec<Enum> = ir
                .enums
                .iter()
                .map(|(name, enumm)| {
                    let variants = enumm
                        .variants
                        .iter()
                        .map(|variant| EnumVariant {
                            name: variant.name.clone(),
                            description: variant.description.clone(),
                            value: variant.value,
                        })
                        .collect();

                    #[allow(clippy::redundant_field_names)]
                    Enum {
                        name: name.strip_prefix("vals::").unwrap().to_owned(),
                        description: enumm.description.clone(),
                        bit_size: enumm.bit_size,
                        variants: variants,
                    }
                })
                .collect();

            enums.sort_by_key(|e| e.name.clone());

            Self {
                blocks,
                fieldsets,
                enums,
            }
        }
    }

    #[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
    pub struct Block {
        pub name: String,
        pub extends: Option<String>,

        pub description: Option<String>,
        pub items: Vec<BlockItem>,
    }

    // Notice:
    // BlockItem has custom Debug implement,
    // when modify the struct, make sure Debug impl reflect the change.
    #[derive(Eq, PartialEq, Clone, Deserialize)]
    pub struct BlockItem {
        pub name: String,
        pub description: Option<String>,

        pub array: Option<Array>,
        pub byte_offset: u32,

        pub inner: BlockItemInner,
    }

    // Notice:
    // Debug implement AFFECT OUTPUT METAPAC, modify with caution
    impl std::fmt::Debug for BlockItem {
        fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            f.debug_struct("BlockItem")
                .field("name", &self.name)
                .field("description", &self.description)
                .field("array", &self.array)
                .field("byte_offset", &format_args!("{:#x}", self.byte_offset))
                .field("inner", &self.inner)
                .finish()
        }
    }

    #[derive(EnumDebug, Eq, PartialEq, Clone, Deserialize)]
    pub enum BlockItemInner {
        Block(BlockItemBlock),
        Register(Register),
    }

    #[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
    pub struct Register {
        pub access: Access,
        pub bit_size: u32,
        pub fieldset: Option<String>,
    }

    #[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
    pub struct BlockItemBlock {
        pub block: String,
    }

    #[derive(EnumDebug, Eq, PartialEq, Clone, Deserialize)]
    pub enum Access {
        ReadWrite,
        Read,
        Write,
    }

    #[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
    pub struct FieldSet {
        pub name: String,
        pub extends: Option<String>,

        pub description: Option<String>,
        pub bit_size: u32,
        pub fields: Vec<Field>,
    }

    #[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
    pub struct Field {
        pub name: String,
        pub description: Option<String>,

        pub bit_offset: BitOffset,
        pub bit_size: u32,
        pub array: Option<Array>,
        pub enumm: Option<String>,
    }

    #[derive(EnumDebug, Eq, PartialEq, Clone, Deserialize)]
    pub enum Array {
        Regular(RegularArray),
        Cursed(CursedArray),
    }

    #[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
    pub struct RegularArray {
        pub len: u32,
        pub stride: u32,
    }

    #[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
    pub struct CursedArray {
        pub offsets: Vec<u32>,
    }

    #[derive(EnumDebug, Eq, PartialEq, Clone, Deserialize)]
    pub enum BitOffset {
        Regular(RegularBitOffset),
        Cursed(CursedBitOffset),
    }

    #[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
    pub struct RegularBitOffset {
        pub offset: u32,
    }

    #[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
    pub struct CursedBitOffset {
        pub ranges: Vec<core::ops::RangeInclusive<u32>>,
    }

    #[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
    pub struct Enum {
        pub name: String,
        pub description: Option<String>,
        pub bit_size: u32,
        pub variants: Vec<EnumVariant>,
    }

    #[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
    pub struct EnumVariant {
        pub name: String,
        pub description: Option<String>,
        pub value: u64,
    }
}

#[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
pub struct Chip {
    pub name: String,
    pub family: String,
    pub cores: Vec<Core>,
    pub memory: Vec<MemoryRegion>,
    pub packages: Vec<Package>,
}

// Notice:
// MemoryRegion has custom Debug implement,
// when modify the struct, make sure Debug impl reflect the change.
#[derive(Eq, PartialEq, Clone, Deserialize)]
pub struct MemoryRegion {
    pub name: String,
    pub kind: MemoryRegionKind,
    pub address: u32,
    pub size: u32,
    #[serde(default)]
    pub access: Option<Access>,
}

impl MemoryRegion {
    pub fn role(&self) -> MemoryRole {
        MemoryRole::from_name(&self.name)
    }
}

#[derive(Debug, Eq, PartialEq, Clone, Copy)]
pub enum MemoryRole {
    Application,
    System,
    OptionBytes,
    Ram,
}

impl MemoryRole {
    pub fn from_name(name: &str) -> Self {
        if name == "FLASH" || name.starts_with("USR_") {
            MemoryRole::Application
        } else if name.starts_with("SYS_") {
            MemoryRole::System
        } else if name == "OPT" {
            MemoryRole::OptionBytes
        } else if name == "SRAM" || name == "RAM" {
            MemoryRole::Ram
        } else if name == "RAMB" || name == "RET_RAM" {
            // 独立掉电保持 RAM 区（SDK 链接脚本命名，如 0x200F0000 4K），
            // 语义上仍是通用 RAM，一并归入 Ram 角色
            MemoryRole::Ram
        } else {
            panic!(
                "memory region {name:?} has no known role mapping — extend MemoryRole::from_name"
            )
        }
    }
}

// Notice:
// Debug implement AFFECT OUTPUT METAPAC, modify with caution.
impl std::fmt::Debug for MemoryRegion {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("MemoryRegion")
            .field("name", &self.name)
            .field("kind", &self.kind)
            .field("role", &self.role())
            .field("address", &format_args!("{:#x}", self.address))
            .field("size", &self.size)
            .field("access", &self.access)
            .finish()
    }
}

#[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
pub struct Access {
    pub read: bool,
    pub write: bool,
    pub execute: bool,
}

#[derive(EnumDebug, Eq, PartialEq, Clone, Deserialize)]
pub enum MemoryRegionKind {
    #[serde(rename = "flash")]
    Flash,
    #[serde(rename = "ram")]
    Ram,
}

#[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
pub struct Core {
    pub name: String,
    pub peripherals: Vec<Peripheral>,
    #[serde(default)]
    pub interrupts: Vec<Interrupt>,
    pub dma_channels: Vec<DmaChannel>,
}

#[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
pub struct Interrupt {
    pub name: String,
    pub number: u32,
}

#[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
pub struct Package {
    pub name: String,
    pub package: String,
}

// Notice:
// Peripheral has custom Debug implement,
// when modify struct, make sure the Debug impl reflect the change.
#[derive(Eq, PartialEq, Clone, Deserialize)]
pub struct Peripheral {
    pub name: String,
    pub address: u64,
    #[serde(default)]
    pub registers: Option<PeripheralRegisters>,
    #[serde(default)]
    pub views: Vec<PeripheralView>,
    #[serde(default)]
    pub rcc: Option<PeripheralRcc>,
    #[serde(default)]
    pub kernel_clock: Option<PeripheralKernelClock>,
    #[serde(default)]
    pub pins: Vec<PeripheralPin>,
    #[serde(default)]
    pub dma_channels: Vec<PeripheralDmaChannel>,
    #[serde(default)]
    pub interrupts: Vec<PeripheralInterrupt>,
}

// Notice:
// Debug implement AFFECT OUTPUT METAPAC, modify with caution
impl std::fmt::Debug for Peripheral {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Peripheral")
            .field("name", &self.name)
            .field("address", &format_args!("{:#x}", self.address))
            .field("registers", &self.registers)
            .field("views", &self.views)
            .field("rcc", &self.rcc)
            .field("kernel_clock", &self.kernel_clock)
            .field("pins", &self.pins)
            .field("dma_channels", &self.dma_channels)
            .field("interrupts", &self.interrupts)
            .finish()
    }
}

#[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
pub struct PeripheralInterrupt {
    pub signal: String,
    pub interrupt: String,
}

/// HC32 时钟门控：PWC/FCG 使能寄存器位（如 PERI_CLKEN.SysctrlPeripheralAdcBgr）。
/// polarity：high = 写 1 使能（DDL）；low = 写 0 使能（LL，复位 0xFFFF_FFFF）。
/// bus：RM 存储器映射标注的总线域（HAL 据此路由 PCLK 频率）。
#[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
pub struct PeripheralRcc {
    #[serde(default)]
    pub enable: Option<PeripheralRccRegister>,
    #[serde(default)]
    pub polarity: Option<String>,
    #[serde(default)]
    pub bus: Option<String>,
}

/// 外设功能（内核）时钟来源：fixed（由总线域唯一决定）或
/// selectable（专用选择寄存器 + 编码值→时钟节点映射）。
/// serde 别名与 hc32-data-serde 的 YAML 小写风格保持一致。
///
/// Debug 手写（派生版会把 BTreeMap 输出成 `{ "k": "v", .. }` 的 map 字面量，
/// 不是合法 Rust）；输出 metapac 公开类型所需的元组切片字面量
/// `&[("k", "v"), ..]`。stringify 后即生成代码，改动需过全量 gen + 双芯片测试。
#[derive(Eq, PartialEq, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum PeripheralKernelClock {
    Fixed { name: String },
    Selectable {
        register: String,
        field: String,
        #[serde(default)]
        options: std::collections::BTreeMap<String, String>,
    },
}

impl std::fmt::Debug for PeripheralKernelClock {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PeripheralKernelClock::Fixed { name } => {
                write!(f, "Fixed {{ name: {name:?} }}")
            }
            PeripheralKernelClock::Selectable {
                register,
                field,
                options,
            } => {
                let pairs: Vec<String> = options
                    .iter()
                    .map(|(k, v)| format!("({k:?}, {v:?})"))
                    .collect();
                write!(
                    f,
                    "Selectable {{ register: {register:?}, field: {field:?}, options: &[{}] }}",
                    pairs.join(", ")
                )
            }
        }
    }
}

#[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
pub struct PeripheralRccRegister {
    pub register: String,
    pub field: String,
}

#[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
pub struct PeripheralPin {
    pub pin: String,
    pub signal: String,
    #[serde(default)]
    pub func: Option<u8>,
}

#[derive(Debug, Eq, PartialEq, Clone, Deserialize)]
pub struct DmaChannel {
    pub name: String,
    pub dma: String,
    pub channel: u32,
}

#[derive(Debug, Eq, PartialEq, Clone, Deserialize, Hash)]
pub struct PeripheralDmaChannel {
    pub signal: String,
    pub dma: Option<String>,
    pub channel: Option<String>,
    pub request: Option<u16>,
}

#[derive(Debug, Eq, PartialEq, Clone, Deserialize, Hash)]
pub struct PeripheralRegisters {
    pub kind: String,
    pub version: String,
    pub block: String,
    #[serde(default)]
    pub ir: String,
}

/// MODE 拆分定时器的一个 SVD 视图（TIM0_MODE0/1/23、ATIM0MODE0/1/23）。
/// 物理身份（地址/门控/中断）在外设项上；视图只挂寄存器版本。
/// JSON 里视图的寄存器字段平铺为 kind/version/block（与 chips JSON 同构），
/// 读入时包成 PeripheralRegisters。
#[derive(Debug, Eq, PartialEq, Clone, Hash)]
pub struct PeripheralView {
    /// SVD 视图原名（= register_versions.csv 实例键，PAC const 名）。
    pub name: String,
    /// 工作模式档位：MODE0 / MODE1 / MODE23。
    pub mode: String,
    pub registers: PeripheralRegisters,
}

impl<'de> Deserialize<'de> for PeripheralView {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        #[derive(Deserialize)]
        struct Raw {
            name: String,
            mode: String,
            kind: String,
            version: String,
            block: String,
        }
        let raw = Raw::deserialize(deserializer)?;
        Ok(PeripheralView {
            name: raw.name,
            mode: raw.mode,
            registers: PeripheralRegisters {
                kind: raw.kind,
                version: raw.version,
                block: raw.block,
                ir: String::new(),
            },
        })
    }
}
