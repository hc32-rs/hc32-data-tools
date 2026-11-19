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

/// MODE 拆分定时器的一个 SVD 寄存器视图（TIM0_MODE0/1/23、ATIM0MODE0/1/23）。
/// 多视图实例走 `views` 列表（物理身份在外设项上，每视图挂自己的寄存器版本）；
/// 单视图实例仍用 `registers`，两者互斥。
#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct RegisterView {
    /// SVD 视图原名（= register_versions.csv / peripheral-version-map.json 的实例键）。
    pub name: String,
    /// 视图对应的工作模式档位：MODE0 / MODE1 / MODE23。
    pub mode: String,
    pub kind: String,
    pub version: String,
    pub block: String,
}

/// HC32 的时钟门控：门控寄存器某一位 + 访问总线域。
/// 门控极性（DDL 1=使能，LL 0=使能）由 csv_to_yaml.py 按系列写入 `polarity`，
/// HAL 不应从 FCG 编号或寄存器名推导总线名/极性，只读这里。
#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Rcc {
    pub enable: rcc::Enable,
    /// 门控位极性："high" = 写 1 使能（DDL SYSCTRL_*EN/PERI*_CLKEN 系列），
    /// "low" = 写 0 使能（LL PWC_FCG0..3，复位值 0xFFFF_FFFF）。
    /// 空缺 = 未确认（HAL 需 fallback 到按系列判断或 panic）。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub polarity: Option<String>,
    /// 访问总线域（RM 存储器映射标注）：apb0/apb1/apb2/ahb0/ahb1/… 小写。
    /// HAL 用于把外设频率路由到对应 PCLK。空缺 = 未从 RM 确认。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bus: Option<String>,
}

pub mod rcc {
    use serde::{Deserialize, Serialize};

    #[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
    pub struct Enable {
        pub register: String,
        pub field: String,
    }
}

/// 外设功能（内核）时钟来源。与门控位解耦：门控决定寄存器访问是否有时钟，
/// kernel 时钟决定波特率/采样等功能的输入频率。
#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct KernelClock {
    /// fixed 时：时钟树节点名（pclk0/pclk1/…），HAL 从其 rcc 状态取频率。
    /// selectable 时：选择寄存器与位域。
    #[serde(flatten)]
    pub source: KernelClockSource,
}

/// 内部 tag "type"（fixed/selectable）——与 family YAML 的
/// `kernel_clock: {type: fixed, name: pclk1}` 形态直接对应。
#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum KernelClockSource {
    /// type = "fixed"
    Fixed {
        name: String,
    },
    /// type = "selectable"
    Selectable {
        register: String,
        field: String,
        /// 编码值 → 时钟树节点名（来源 RM 位描述，如 F460 PERICKSEL:
        /// 0x0=pclk2/4, 0x8=mpllp …）；空缺位 HAL 须拒绝配置。
        options: std::collections::BTreeMap<String, String>,
    },
}

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Pin {
    pub pin: pin::Pin,
    pub signal: String,
    /// GPIO 复用功能号（目标功能在引脚上的 func 编号），待 pins 数据补齐后启用。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub func: Option<u8>,
}

/// data/pins/<series>_<PART>.yaml 的条目形态（实例名 → 本结构）。
/// 只承载 pins；实例的物理身份（地址/门控/版本）在 family YAML，
/// data-gen 按实例名把 pins 合并到 core.peripherals 对应项。
#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct PeripheralWithPins {
    pub pins: Vec<Pin>,
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
