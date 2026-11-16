use serde::{Deserialize, Serialize};

pub mod peripheral;

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Peripheral {
    pub name: String,
    pub address: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub registers: Option<peripheral::Registers>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rcc: Option<peripheral::Rcc>,
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
