// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

use crate::common::utils::CPath;
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicU32, Ordering};

pub const ANNO_PMEM: &str = "cube.pmem";

const GUEST_MOUNT_DIR_PREFIX: &str = "/run/cube-containers/sandbox/pmem-cube/pmem";

pub static DEVICE_INDEX_OFFSET: AtomicU32 = AtomicU32::new(1);

#[derive(Eq, PartialEq, Clone, Debug, Default, Serialize, Deserialize)]
pub struct Pmem {
    pub file: String,
    #[serde(default)]
    pub discard_writes: bool,
    #[serde(default)]
    pub source_dir: String,
    pub fs_type: String,
    pub size: Option<u64>,
    pub id: String,
    #[serde(default)]
    pub placeholder: bool,
}

impl Pmem {
    // pmem0 is the guest root image
    pub fn guest_device_name(relative_index: u32) -> String {
        let offset = DEVICE_INDEX_OFFSET.load(Ordering::Relaxed);
        format!("pmem{}", relative_index + offset)
    }

    pub fn guest_device_path(relative_index: u32) -> String {
        let offset = DEVICE_INDEX_OFFSET.load(Ordering::Relaxed);
        format!("/dev/pmem{}", relative_index + offset)
    }

    pub fn guest_mount_point(relative_index: u32) -> String {
        let offset = DEVICE_INDEX_OFFSET.load(Ordering::Relaxed);
        format!("{}{}", GUEST_MOUNT_DIR_PREFIX, relative_index + offset)
    }

    pub fn guest_bind_source(&self, relative_index: u32) -> String {
        let offset = DEVICE_INDEX_OFFSET.load(Ordering::Relaxed);
        let base = format!("{}{}", GUEST_MOUNT_DIR_PREFIX, relative_index + offset);
        let mut src = CPath::new(base.as_str());
        src.join(self.source_dir.as_str());

        src.to_str()
            .unwrap_or_else(|| panic!("Invalid path string:{:?}", src))
            .to_string()
    }

    pub fn driver() -> String {
        "nvdimm".to_string()
    }
}
