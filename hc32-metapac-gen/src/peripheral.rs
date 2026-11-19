use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::fs::File;
use std::io::Write;
use std::path::{Path, PathBuf};

use chiptool::{generate, ir, transform};
use proc_macro2::TokenStream;
use quote::quote;
use regex::Regex;

use crate::{gen_opts, stringify};

/// 一个字段的类型化入口描述（字段 enum 引用摘除后由后处理追加）。
struct TypedField {
    /// 字段集 IR 路径（已是 `regs::<Name>` 形式）。
    fieldset: String,
    /// 字段名（snake_case，与基础访问器同名）。
    field: String,
    /// 枚举 IR 路径（已是 `vals::<Name>` 形式）。
    enumm: String,
    /// 枚举描述（含 mode_note），渲染为类型化入口的 #[doc]。
    description: Option<String>,
    /// 字段位宽（1 位字段的基础访问器是 bool，需显式换算）。
    bit_size: u32,
    /// 数组字段暂不生成类型化入口（基础访问器带下标参数）。
    is_array: bool,
}

fn load_and_normalize_ir(
    json_path: &Path,
    module: &str,
    version: &str,
) -> (ir::IR, Vec<TypedField>) {
    let mut ir: ir::IR = serde_json::from_reader(
        File::open(json_path).unwrap_or_else(|_| panic!("open {}", json_path.display())),
    )
    .unwrap();

    transform::expand_extends::ExpandExtends {}
        .run(&mut ir)
        .unwrap();

    transform::map_names(&mut ir, |k, s| match k {
        transform::NameKind::Block => *s = s.to_string(),
        transform::NameKind::Fieldset => *s = format!("regs::{}", s),
        transform::NameKind::Enum => *s = format!("vals::{}", s),
        _ => {}
    });

    transform::sort::Sort {}.run(&mut ir).unwrap();
    crate::sanitize().run(&mut ir).unwrap();

    // 双轨 API 只用于明确需要保留既有数值访问器的补录字段。
    // 原生枚举维持 chiptool 的访问器行为。
    // 在名称规范化（snake_case 字段 / vals:: 枚举路径）之后摘除字段级 enum 引用，
    // 让 chiptool 渲染原始数值访问器
    // （toe() -> u8 / set_toe(u8)，保持既有 PAC 用户零破坏），再在渲染后
    // 追加类型化入口（toe_val()/set_toe_val(vals::Toe)）。enum/ 节点保留，
    // 供类型化 impl 引用与 metadata。
    let mut typed: Vec<TypedField> = Vec::new();
    let fieldsets: Vec<String> = ir.fieldsets.keys().cloned().collect();
    for fs_path in fieldsets {
        let Some(fs) = ir.fieldsets.get_mut(&fs_path) else {
            continue;
        };
        let mut fields: Vec<(String, String, u32, bool)> = Vec::new();
        for f in fs.fields.iter_mut() {
            if (module, version, fs_path.as_str(), f.name.as_str())
                != ("usart", "ddl_v1", "regs::Cr1", "toe")
            {
                continue;
            }
            if let Some(e) = f.enumm.take() {
                fields.push((f.name.clone(), e, f.bit_size, f.array.is_some()));
            }
        }
        for (field_name, enumm, bit_size, is_array) in fields {
            let description = ir.enums.get(&enumm).and_then(|e| e.description.clone());
            typed.push(TypedField {
                fieldset: fs_path.clone(),
                field: field_name,
                enumm,
                description,
                bit_size,
                is_array,
            });
        }
    }

    (ir, typed)
}

fn emit_registers_rs(out_dir: &Path, module: &str, version: &str, ir: &ir::IR, const_name: &str) {
    let our_ir = crate::data::ir::IR::from_chiptool(ir.clone());
    let mut data = String::new();
    write!(
        &mut data,
        "
                    use crate::metadata::ir::*;
                    pub(crate) static {}: IR = {};
                ",
        const_name,
        stringify(&our_ir),
    )
    .unwrap();

    let mut file = File::create(
        out_dir
            .join("src/registers")
            .join(format!("{}_{}.rs", module, version)),
    )
    .unwrap();
    file.write_all(data.as_bytes()).unwrap();
}

fn registers_json_path(data_dir: &Path, module: &str, version: &str) -> PathBuf {
    data_dir
        .join("registers")
        .join(format!("{}_{}.json", module, version))
}

/// 追加类型化访问器：对每个摘除过 enum 引用的字段生成
/// `<field>_val() -> vals::<Enum>` / `set_<field>_val(vals::<Enum>)`，
/// 挂在字段集 impl 块（按字段集分组追加一个 impl）。
/// 数组字段（bit_offset 为 Cursed 或带 array）不支持，注入侧已校验为普通字段。
fn render_typed_accessors(typed: &[TypedField]) -> TokenStream {
    use proc_macro2::{Ident, Span};

    let span = Span::call_site();
    // fieldset 路径 -> 该字段集下的类型化访问器
    let mut by_fieldset: BTreeMap<&str, Vec<&TypedField>> = BTreeMap::new();
    for t in typed {
        by_fieldset.entry(t.fieldset.as_str()).or_default().push(t);
    }

    let mut out = TokenStream::new();
    for (fs_path, fields) in by_fieldset {
        // 数组字段不生成类型化入口（基础访问器带下标参数，位宽/下标语义需逐个核对）
        let fields: Vec<_> = fields.into_iter().filter(|t| !t.is_array).collect();
        if fields.is_empty() {
            continue;
        }
        let fs_ident = {
            // 路径形如 "regs::Cr1"，取末段作为 impl 目标名
            let last = fs_path.rsplit("::").next().unwrap_or(fs_path);
            Ident::new(last, span)
        };
        let mut items = TokenStream::new();
        for t in fields {
            // 枚举路径与字段集同文件（peripheral 模块顶层），按相对路径引用：
            // vals 模块与 regs 模块平级，字段集 impl 在 regs:: 内部，故用 super::vals。
            let enum_ident = {
                let last = t.enumm.rsplit("::").next().unwrap_or(t.enumm.as_str());
                Ident::new(last, span)
            };
            let enum_path = quote!(super::vals::#enum_ident);
            // 数值访问器的底层类型按字段位宽推导需重读 IR；此处直接复用枚举的
            // from_bits/to_bits（位宽已在注入侧校验与字段一致），经 raw 位运算读写。
            let getter = Ident::new(&t.field, span);
            let getter_val = Ident::new(&format!("{}_val", t.field), span);
            let setter_raw = Ident::new(&format!("set_{}", t.field), span);
            let setter_val = Ident::new(&format!("set_{}_val", t.field), span);
            // 1 位字段的基础访问器是 bool，读写换算；多位字段直接传数值
            // （位宽与枚举 bit_size 一致已在注入侧校验）。
            let (read_expr, write_expr) = if t.bit_size == 1 {
                (
                    quote!(self.#getter() as u8),
                    quote!(self.#setter_raw(val.to_bits() != 0)),
                )
            } else {
                (
                    quote!(self.#getter()),
                    quote!(self.#setter_raw(val.to_bits())),
                )
            };
            let doc = t
                .description
                .as_deref()
                .map(|d| quote!(#[doc = #d]))
                .unwrap_or_default();
            items.extend(quote!(
                #doc
                #[must_use]
                #[inline(always)]
                pub const fn #getter_val(&self) -> #enum_path {
                    #enum_path::from_bits(#read_expr)
                }
                #doc
                #[inline(always)]
                pub const fn #setter_val(&mut self, val: #enum_path) {
                    #write_expr;
                }
            ));
        }
        out.extend(quote! {
            impl #fs_ident {
                #items
            }
        });
    }
    out
}

pub(crate) fn gen_peripheral(out_dir: &Path, data_dir: &Path, module: &str, version: &str) {
    println!("Generate Peripheral {} {}", module, version);

    let json_path = registers_json_path(data_dir, module, version);
    let (ir, typed) = load_and_normalize_ir(&json_path, module, version);

    let items = generate::render(&ir, &gen_opts()).unwrap();
    let mut file = File::create(
        out_dir
            .join("src/peripherals")
            .join(format!("{}_{}.rs", module, version)),
    )
    .unwrap();

    // Allow a few warning
    file.write_all(
        b"#![allow(clippy::missing_safety_doc)]
                #![allow(clippy::identity_op)]
                #![allow(clippy::unnecessary_cast)]
                #![allow(clippy::erasing_op)]",
    )
    .unwrap();

    let mut data = items.to_string().replace("] ", "]\n");

    // 类型化入口（双轨 API 的第二轨）：插入到 `pub mod regs` 模块内部
    // （字段集 impl 需与 regs 平级的 vals 可达：`super::vals`）。
    if !typed.is_empty() {
        let accessors = render_typed_accessors(&typed)
            .to_string()
            .replace("] ", "]\n");
        // chiptool 输出顺序：regs 模块在 vals 模块之前收尾。
        // 在 `pub mod vals` 声明前的最后一个 `}`（regs 收尾）处插入。
        let anchor = data.rfind("pub mod vals");
        match anchor {
            Some(pos) => {
                // 向前找 regs 收尾的 `}`（vals 声明前的最后一个右花括号）
                let close = data[..pos].rfind('}').expect("regs module close brace");
                data.insert_str(close, &accessors);
            }
            // 无 vals 模块（枚举为空但 typed 非空不可能出现——typed 依赖枚举存在）
            None => panic!("typed fields but no vals module in {module}_{version}"),
        }
    }

    // Remove inner attributes like #![no_std]
    let re = Regex::new("# *! *\\[.*\\]").unwrap();
    let data = re.replace_all(&data, "");
    file.write_all(data.as_bytes()).unwrap();

    // metadata IR 保留字段 enum 引用（HAL/工具侧读元数据仍能看到类型关系）。
    let mut meta_ir = ir.clone();
    for t in &typed {
        if let Some(fs) = meta_ir.fieldsets.get_mut(&t.fieldset) {
            if let Some(f) = fs.fields.iter_mut().find(|f| f.name == t.field) {
                f.enumm = Some(t.enumm.clone());
            }
        }
    }
    emit_registers_rs(out_dir, module, version, &meta_ir, "REGISTERS");
}
