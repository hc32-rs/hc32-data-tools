#!/usr/bin/env bash

# hc32-data-tools 工作流入口（对标 ch32-data 的 ./d）
# 用法：./d gen | ./d ci | ./d test-metapac <芯片小写名> [目标三元组]

set -ex

cd "$(dirname "$0")"

CMD=$1
shift

case "$CMD" in
    install-chiptool)
        cargo install --git https://github.com/embassy-rs/chiptool --rev e8902440a6eee471a9b14b38ee1cd2e526d2c495
    ;;
    gen)
        # 须在 hc32-data-tools 仓库根目录运行；数据仓库为同级兄弟目录
        rm -rf build/data
        cargo run -p hc32-data-gen && cargo run -p hc32-metapac-gen
        # chiptool 输出为 proc-macro2 token 流（未排版）；生成 crate 是 edition 2024
        # （unsafe extern / #[unsafe(...)]），rustfmt 须显式指定 edition 才能解析
        find build/hc32-metapac/src -name '*.rs' -print0 | xargs -0 rustfmt --edition 2024
    ;;
    test-metapac)
        CHIP=${1:-hc32f003c4pa}
        TARGET=${2:-thumbv6m-none-eabi}
        cd build/hc32-metapac
        cargo build --features pac,metadata,rt,memory-x,$CHIP --target $TARGET
        cargo doc --features pac,metadata,rt,memory-x,$CHIP --target $TARGET
        cargo test --no-default-features --features metadata,$CHIP
    ;;
    ci)
        ./d gen
        ./d test-metapac hc32f003c4pa thumbv6m-none-eabi
        ./d test-metapac hc32f460jcta thumbv7em-none-eabihf
    ;;
    *)
        echo "unknown command: $CMD (可用: gen, ci, test-metapac, install-chiptool)"
        exit 1
    ;;
esac
