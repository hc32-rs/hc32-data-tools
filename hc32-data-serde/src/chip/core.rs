use serde::{Deserialize, Serialize};

pub mod peripheral;

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Peripheral {
    pub name: String,
    pub address: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub registers: Option<peripheral::Registers>,
    /// MODE 拆分定时器的 SVD 视图列表（与 `registers` 互斥；见 peripheral::RegisterView）。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub views: Vec<peripheral::RegisterView>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rcc: Option<peripheral::Rcc>,
    /// 外设功能（内核）时钟来源，与 rcc 门控解耦；见 peripheral::KernelClock。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub kernel_clock: Option<peripheral::KernelClock>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub pins: Vec<peripheral::Pin>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub interrupts: Option<Vec<peripheral::Interrupt>>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub dma_channels: Vec<peripheral::DmaChannel>,
}

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Interrupt {
    pub name: String,
    pub number: u8,
}

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct DmaChannels {
    pub name: String,
    pub dma: String,
    pub channel: u8,
}
