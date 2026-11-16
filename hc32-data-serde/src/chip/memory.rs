use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Flash,
    Ram,
}

#[derive(Clone, Debug, Eq, PartialEq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Access {
    #[serde(default = "crate::default_true")]
    pub read: bool,
    #[serde(default)]
    pub write: bool,
    #[serde(default = "crate::default_true")]
    pub execute: bool,
}
