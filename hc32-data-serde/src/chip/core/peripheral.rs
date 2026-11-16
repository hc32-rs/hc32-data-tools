use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Registers {
    pub kind: String,
    /// SVD 实例名与设备头外设名不一致时（如 TIM0_MODE0 vs TIM0），
    /// csv_to_yaml.py 挂不上版本号 → version 缺失，data-gen 会降级为纯地址外设。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,
    pub block: String,
}

/// HC32 的时钟门控：PWC/FCG 外设时钟使能寄存器的某一位
/// （如 PERI_CLKEN 的 SysctrlPeripheralAdcBgr）。无独立 kernel_clock/reset 概念。
#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Rcc {
    pub enable: rcc::Enable,
}

pub mod rcc {
    use serde::{Deserialize, Serialize};

    #[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
    pub struct Enable {
        pub register: String,
        pub field: String,
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Pin {
    pub pin: pin::Pin,
    pub signal: String,
    /// GPIO 复用功能号（目标功能在引脚上的 func 编号），待 pins 数据补齐后启用。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub func: Option<u8>,
}

pub mod pin {
    use serde::{Deserialize, Serialize};

    #[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, PartialOrd, Ord)]
    pub struct Pin {
        pub port: char,
        pub num: u8,
    }

    impl Pin {
        pub fn parse(pin: &str) -> Option<Self> {
            let mut chars = pin.chars();
            let p = chars.next()?;
            if p != 'P' {
                return None;
            }
            let port = chars.next()?;
            let num = chars.as_str().parse().ok()?;

            Some(Self { port, num })
        }
    }

    impl std::fmt::Display for Pin {
        fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            write!(f, "P{}{}", self.port, self.num)
        }
    }

    impl Serialize for Pin {
        fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
        where
            S: serde::Serializer,
        {
            serializer.serialize_str(&format!("{self}"))
        }
    }

    struct PinVisitor;

    impl<'de> serde::de::Visitor<'de> for PinVisitor {
        type Value = Pin;

        fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
            formatter.write_str("pin")
        }

        fn visit_str<E>(self, v: &str) -> Result<Self::Value, E>
        where
            E: serde::de::Error,
        {
            Ok(Pin::parse(v).unwrap())
        }
    }

    impl<'de> Deserialize<'de> for Pin {
        fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
        where
            D: serde::Deserializer<'de>,
        {
            deserializer.deserialize_str(PinVisitor)
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Interrupt {
    pub signal: String,
    pub interrupt: String,
}

/// HC32 的 DMA 触发语义与 ch32 的固定通道/DMAMUX 均不同：
/// - DDL：request = DMAC_CONFAx.TRI_SEL 全局触发源索引，通道运行时软件路由；
/// - LL：request = EVT_SRC_* 事件号（9 位），写 AOS->DMAx_TRGSELy。
/// 因此只挂 request 号，不绑定物理通道。
#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct DmaChannel {
    pub signal: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dma: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub channel: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request: Option<u16>,
}
