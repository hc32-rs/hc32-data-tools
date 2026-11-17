/// Split a register filename stem into metadata kind and version.
/// Versions include the DDL/LL namespace, e.g. ("clk_trim", "ddl_v1_2").
/// Legacy unnamespaced names are retained only for excluded layouts.
pub fn split_register_name(stem: &str) -> Option<(&str, &str)> {
    for marker in ["_ddl_", "_ll_"] {
        if let Some((kind, version)) = stem.split_once(marker) {
            let mut numbers = version.strip_prefix('v')?.split('_');
            let positive = |s: &str| {
                !s.is_empty() && !s.starts_with('0') && s.bytes().all(|c| c.is_ascii_digit())
            };
            if !positive(numbers.next()?)
                || numbers.clone().count() > 1
                || numbers.any(|n| !positive(n))
                || kind.is_empty()
            {
                return None;
            }
            return Some((kind, &stem[kind.len() + 1..]));
        }
    }
    let (kind, version) = stem.rsplit_once('_')?;
    if !kind.is_empty()
        && (version == "common"
            || version
                .strip_prefix('v')
                .is_some_and(|n| !n.is_empty() && n.bytes().all(|c| c.is_ascii_digit())))
    {
        Some((kind, version))
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_preserve_kind_and_variant_boundaries() {
        assert_eq!(
            split_register_name("clk_trim_ddl_v1_2"),
            Some(("clk_trim", "ddl_v1_2"))
        );
        assert_eq!(
            split_register_name("tmr4_ecer_ll_v1"),
            Some(("tmr4_ecer", "ll_v1"))
        );
        assert_eq!(split_register_name("usbfs_v3"), Some(("usbfs", "v3")));
        assert_eq!(split_register_name("hash_common"), Some(("hash", "common")));
        for name in [
            "adc_ddl_v1a",
            "adc_ll_v1_0",
            "adc_ll_v1_2_3",
            "adc_ll_v0",
            "adc_ll_v",
        ] {
            assert_eq!(split_register_name(name), None, "{name}");
        }
    }
}
