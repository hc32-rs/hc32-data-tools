use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

pub mod core;
pub mod memory;

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Package {
    pub name: String,
    pub package: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Memory {
    pub name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<memory::Kind>,
    #[serde(
        default,
        deserialize_with = "crate::deserialize_opt_size_with_suffix",
        skip_serializing_if = "Option::is_none"
    )]
    pub address: Option<u32>,
    #[serde(
        default,
        deserialize_with = "crate::deserialize_opt_size_with_suffix",
        skip_serializing_if = "Option::is_none"
    )]
    pub size: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub access: Option<memory::Access>,
}

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Doc {
    pub r#type: String,
    pub title: String,
    pub name: String,
    pub url: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Core {
    /// 内核名（cortex-m0 / cortex-m3 / cortex-m4f 等），来自 SVD `<cpu>`。
    pub name: String,
    #[serde(default)]
    pub peripherals: Vec<core::Peripheral>,
    #[serde(default)]
    pub interrupts: Vec<core::Interrupt>,
    #[serde(default)]
    pub dma_channels: Vec<core::DmaChannels>,

    // include fields, for common peripherals
    #[serde(skip_serializing_if = "Option::is_none")]
    pub include_interrupts: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub include_dma_channels: Option<BTreeMap<String, String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub include_peripherals: Option<Vec<String>>,
    /// 引脚路由（型号×封装粒度，data/pins/&lt;series&gt;_&lt;PART&gt;_&lt;PKG&gt;.yaml）。
    /// 文件格式：{ <外设实例>: { pins: [{pin, signal, func}] } }，
    /// data-gen 合并时按实例名挂到 core.peripherals 对应项。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub include_pins: Option<Vec<String>>,
}
