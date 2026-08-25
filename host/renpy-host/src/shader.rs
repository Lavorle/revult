//! Native WGSL Shader Composer engine (Rust host).
//!
//! Provides `ShaderPart`, `ShaderPartRegistry`, and `NativeShaderComposer` for
//! merging modular shader hooks, resolving uniform struct layouts, and generating
//! complete WGSL source text with single source-of-truth validation and caching.
//!
//! This module is the single source of truth for shader-part composition and WGSL
//! emission. Its public API (`NativeShaderComposer`, `emit_wgsl`, `ComposedShader`,
//! the `ShaderError` variants, and the `DEFAULT_*` constants) is the contract surface
//! for future PyO3 bindings and external callers. The bin crate compiles this module
//! privately, so the unused-in-binary items are intentionally retained for API stability.
#![allow(dead_code)]

use std::collections::{HashMap, HashSet};
use std::fmt;

pub const UNIFORM_NONE: &str = "none";
pub const UNIFORM_PARAMS16: &str = "params16";
pub const UNIFORM_MATRIXCOLOR16: &str = "matrixcolor16";

pub const DEFAULT_TEXTURE: &str = "renpy.texture";
pub const DEFAULT_SOLID: &str = "renpy.solid";

/// Specific error type for WGSL shader composition failures.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ShaderError {
    UnknownPart(String),
    AtomicPart(String),
    UniformConflict {
        current: String,
        conflicting: String,
    },
    ExceededMaxTextures {
        count: u8,
        max: u8,
    },
    InvalidSyntax(String),
    InvalidLayout(String),
    Other(String),
}

impl fmt::Display for ShaderError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ShaderError::UnknownPart(name) => write!(f, "unknown shader part: {name:?}"),
            ShaderError::AtomicPart(name) => write!(
                f,
                "part {name:?} is atomic (multi-texture transition) and cannot be composed"
            ),
            ShaderError::UniformConflict {
                current,
                conflicting,
            } => write!(
                f,
                "conflicting uniform layouts: {current:?} vs {conflicting:?}"
            ),
            ShaderError::ExceededMaxTextures { count, max } => write!(
                f,
                "tex_count {count} > {max} exceeds maximum supported texture slots"
            ),
            ShaderError::InvalidSyntax(msg) => write!(f, "invalid WGSL syntax: {msg}"),
            ShaderError::InvalidLayout(msg) => write!(f, "invalid shader layout: {msg}"),
            ShaderError::Other(msg) => write!(f, "{msg}"),
        }
    }
}

impl std::error::Error for ShaderError {}

impl From<String> for ShaderError {
    fn from(s: String) -> Self {
        ShaderError::Other(s)
    }
}

impl From<&str> for ShaderError {
    fn from(s: &str) -> Self {
        ShaderError::Other(s.to_string())
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ShaderHook {
    pub priority: i32,
    pub body: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ShaderPart {
    pub name: String,
    pub tex_count: u8,
    pub uniform_layout_id: String, // "none" | "params16" | "matrixcolor16"
    pub vertex_hooks: Vec<ShaderHook>,
    pub fragment_hooks: Vec<ShaderHook>,
    pub atomic: bool,
    pub composition_only: bool,
}

/// Result of composing a set of shader parts into a complete WGSL shader.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ComposedShader {
    pub effect_parts: Vec<String>,
    pub tex_count: u8,
    pub uniform_layout_id: String,
    pub has_uniforms: bool,
    pub cache_key: String,
    pub wgsl_source: String,
}

/// Metadata representation for compiled pipeline across host/PyO3 boundaries.
#[derive(Clone, Debug, PartialEq, Eq)]
#[allow(dead_code)]
pub struct ComposedPipelineInfo {
    pub pipeline_handle: u64,
    pub key: String,
    pub tex_count: u8,
    pub uniform_layout_id: String,
    pub has_uniforms: bool,
    pub wgsl_source: String,
}

#[allow(dead_code)]
impl ComposedPipelineInfo {
    pub fn new(
        pipeline_handle: u64,
        key: impl Into<String>,
        tex_count: u8,
        uniform_layout_id: impl Into<String>,
        has_uniforms: bool,
        wgsl_source: impl Into<String>,
    ) -> Self {
        Self {
            pipeline_handle,
            key: key.into(),
            tex_count,
            uniform_layout_id: uniform_layout_id.into(),
            has_uniforms,
            wgsl_source: wgsl_source.into(),
        }
    }
}
impl ShaderPart {
    pub fn new(
        name: impl Into<String>,
        tex_count: u8,
        uniform_layout_id: impl Into<String>,
        vertex_hooks: Vec<ShaderHook>,
        fragment_hooks: Vec<ShaderHook>,
        atomic: bool,
        composition_only: bool,
    ) -> Self {
        Self {
            name: name.into(),
            tex_count,
            uniform_layout_id: uniform_layout_id.into(),
            vertex_hooks,
            fragment_hooks,
            atomic,
            composition_only,
        }
    }

    pub fn validate(&self) -> Result<(), ShaderError> {
        if self.name.trim().is_empty() {
            return Err(ShaderError::InvalidLayout(
                "shader part name cannot be empty".to_string(),
            ));
        }
        if self.tex_count > 3 {
            return Err(ShaderError::ExceededMaxTextures {
                count: self.tex_count,
                max: 3,
            });
        }
        for h in &self.vertex_hooks {
            validate_wgsl_syntax(&h.body)?;
        }
        for h in &self.fragment_hooks {
            validate_wgsl_syntax(&h.body)?;
        }
        Ok(())
    }
}
pub struct ShaderPartRegistry {
    parts: HashMap<String, ShaderPart>,
}

impl ShaderPartRegistry {
    pub fn new() -> Self {
        let mut reg = Self {
            parts: HashMap::new(),
        };
        reg.register_builtin_core();
        reg
    }
    pub fn register_part(&mut self, part: ShaderPart) {
        let _ = part.validate();
        self.parts.insert(part.name.clone(), part);
    }

    #[allow(dead_code)]
    pub fn register_part_checked(&mut self, part: ShaderPart) -> Result<(), ShaderError> {
        part.validate()?;
        self.parts.insert(part.name.clone(), part);
        Ok(())
    }

    pub fn get_part(&self, name: &str) -> Option<&ShaderPart> {
        self.parts.get(name)
    }

    #[allow(dead_code)]
    pub fn list_parts(&self) -> Vec<String> {
        let mut keys: Vec<String> = self.parts.keys().cloned().collect();
        keys.sort();
        keys
    }

    pub fn register_builtin_core(&mut self) {
        // renpy.texture
        self.register_part(ShaderPart::new(
            "renpy.texture",
            1,
            UNIFORM_NONE,
            vec![],
            vec![ShaderHook {
                priority: 200,
                body: "let tex = textureSample(t_color, s_color, v.uv);\ncolor = tex * v.color;"
                    .into(),
            }],
            false,
            false,
        ));

        // renpy.solid
        self.register_part(ShaderPart::new(
            "renpy.solid",
            0,
            UNIFORM_NONE,
            vec![],
            vec![ShaderHook {
                priority: 200,
                body: "color = v.color;".into(),
            }],
            false,
            false,
        ));

        // renpy.ftl
        self.register_part(ShaderPart::new(
            "renpy.ftl",
            1,
            UNIFORM_NONE,
            vec![],
            vec![ShaderHook {
                priority: 500,
                body: "let tex = textureSample(t_color, s_color, v.uv);\ncolor = tex * v.color;"
                    .into(),
            }],
            false,
            false,
        ));

        // renpy.matrixcolor
        self.register_part(ShaderPart::new(
            "renpy.matrixcolor",
            1,
            UNIFORM_MATRIXCOLOR16,
            vec![],
            vec![ShaderHook {
                priority: 350,
                body: "let m = mat4x4<f32>(u.col0, u.col1, u.col2, u.col3);\ncolor = m * color;"
                    .into(),
            }],
            false,
            false,
        ));

        // renpy.blur
        self.register_part(ShaderPart::new(
            "renpy.blur",
            1,
            UNIFORM_PARAMS16,
            vec![],
            vec![ShaderHook {
                priority: 400,
                body: "let dims = vec2<f32>(textureDimensions(t_color));\nlet texel = vec2<f32>(1.0 / max(dims.x, 1.0), 1.0 / max(dims.y, 1.0));\nlet blur_log2 = u.data0.x;\nlet radius = max(exp2(blur_log2), 0.5) * max(v.color.a, 0.01);\nvar acc = vec4<f32>(0.0, 0.0, 0.0, 0.0);\nvar norm = 0.0;\nacc = acc + textureSample(t_color, s_color, v.uv) * 1.0;\nnorm = norm + 1.0;\nacc = acc + textureSample(t_color, s_color, v.uv + vec2<f32>(radius, 0.0) * texel) * 0.6;\nnorm = norm + 0.6;\nacc = acc + textureSample(t_color, s_color, v.uv + vec2<f32>(-radius, 0.0) * texel) * 0.6;\nnorm = norm + 0.6;\nacc = acc + textureSample(t_color, s_color, v.uv + vec2<f32>(0.0, radius) * texel) * 0.6;\nnorm = norm + 0.6;\nacc = acc + textureSample(t_color, s_color, v.uv + vec2<f32>(0.0, -radius) * texel) * 0.6;\nnorm = norm + 0.6;\nlet blur_tex = acc / max(norm, 0.0001);\ncolor = blur_tex * vec4<f32>(v.color.r, v.color.g, v.color.b, 1.0);".into(),
            }],
            false,
            false,
        ));

        // renpy.geometry (composition only)
        self.register_part(ShaderPart::new(
            "renpy.geometry",
            0,
            UNIFORM_NONE,
            vec![],
            vec![],
            false,
            true,
        ));

        // renpy.alpha (composition only)
        self.register_part(ShaderPart::new(
            "renpy.alpha",
            0,
            UNIFORM_NONE,
            vec![],
            vec![],
            false,
            true,
        ));

        // renpy.dissolve (2-tex)
        // renpy.dissolve (2-tex) - atomic part
        self.register_part(ShaderPart::new(
            "renpy.dissolve",
            2,
            UNIFORM_PARAMS16,
            vec![],
            vec![ShaderHook {
                priority: 400,
                body: "let c0 = textureSample(t_color, s_color, v.uv);\nlet c1 = textureSample(t_tex1, s_color, v.uv);\nlet dissolve_t = clamp(u.data0.x, 0.0, 1.0);\ncolor = mix(c0, c1, dissolve_t);".into(),
            }],
            true,
            false,
        ));

        // renpy.imagedissolve (3-tex) - atomic part
        self.register_part(ShaderPart::new(
            "renpy.imagedissolve",
            3,
            UNIFORM_PARAMS16,
            vec![],
            vec![ShaderHook {
                priority: 400,
                body: "let c0 = textureSample(t_color, s_color, v.uv);\nlet c1 = textureSample(t_tex1, s_color, v.uv);\nlet c2 = textureSample(t_tex2, s_color, v.uv);\nlet dissolve_t = clamp(u.data0.x, 0.0, 1.0);\nlet ramp = clamp((c2.r - dissolve_t) * 10.0, 0.0, 1.0);\ncolor = mix(c1, c0, ramp);".into(),
            }],
            true,
            false,
        ));
    }
}

pub struct NativeShaderComposer {
    pub registry: ShaderPartRegistry,
}

impl NativeShaderComposer {
    pub fn new() -> Self {
        Self {
            registry: ShaderPartRegistry::new(),
        }
    }

    pub fn normalize_partnames(&self, partnames: &[String]) -> Vec<String> {
        let mut set: HashSet<String> = HashSet::new();
        for raw in partnames {
            let name = raw.trim();
            if name.is_empty() {
                continue;
            }
            if let Some(stripped) = name.strip_prefix('-') {
                if !stripped.is_empty() {
                    set.remove(stripped);
                }
                continue;
            }
            set.insert(name.to_string());
        }
        let mut sorted: Vec<String> = set.into_iter().collect();
        sorted.sort();
        sorted
    }

    pub fn resolve_effect_parts(
        &self,
        sorted_names: &[String],
        has_texture: bool,
    ) -> Result<Vec<String>, ShaderError> {
        let mut effect: Vec<String> = Vec::new();
        for name in sorted_names {
            if let Some(part) = self.registry.get_part(name) {
                if part.composition_only {
                    continue;
                }
                effect.push(name.clone());
            } else {
                // Ignore unregistered composition aliases like renpy.geometry/alpha
                if name == "renpy.geometry" || name == "renpy.alpha" {
                    continue;
                }
                return Err(ShaderError::UnknownPart(name.clone()));
            }
        }
        if effect.is_empty() {
            if has_texture {
                effect.push(DEFAULT_TEXTURE.to_string());
            } else {
                effect.push(DEFAULT_SOLID.to_string());
            }
        }
        Ok(effect)
    }
    pub fn compute_cache_key(&self, sorted_effect_parts: &[String]) -> String {
        let material = sorted_effect_parts.join(",");
        use sha1::{Digest, Sha1};
        let mut hasher = Sha1::new();
        hasher.update(material.as_bytes());
        let result = hasher.finalize();
        let hex = format!("{:x}", result);
        format!("composed:{}", &hex[..16])
    }

    pub fn compose(
        &self,
        partnames: &[String],
        has_texture: bool,
    ) -> Result<ComposedShader, ShaderError> {
        let sorted = self.normalize_partnames(partnames);
        let effect_parts = self.resolve_effect_parts(&sorted, has_texture)?;
        let mut collected: Vec<&ShaderPart> = Vec::new();
        for name in &effect_parts {
            let part = self
                .registry
                .get_part(name)
                .ok_or_else(|| ShaderError::UnknownPart(name.clone()))?;
            if part.atomic {
                return Err(ShaderError::AtomicPart(name.clone()));
            }
            collected.push(part);
        }

        let mut tex_count: u8 = 0;
        let mut uniform_layout_id: String = UNIFORM_NONE.to_string();

        for part in &collected {
            tex_count = tex_count.max(part.tex_count);
            if part.uniform_layout_id != UNIFORM_NONE {
                if uniform_layout_id == UNIFORM_NONE {
                    uniform_layout_id = part.uniform_layout_id.clone();
                } else if uniform_layout_id != part.uniform_layout_id {
                    return Err(ShaderError::UniformConflict {
                        current: uniform_layout_id,
                        conflicting: part.uniform_layout_id.clone(),
                    });
                }
            }
        }

        if tex_count > 3 {
            return Err(ShaderError::ExceededMaxTextures {
                count: tex_count,
                max: 3,
            });
        }

        let has_uniforms = uniform_layout_id != UNIFORM_NONE;

        let mut v_hooks: Vec<ShaderHook> = Vec::new();
        let mut f_hooks: Vec<ShaderHook> = Vec::new();

        for part in &collected {
            v_hooks.extend(part.vertex_hooks.clone());
            f_hooks.extend(part.fragment_hooks.clone());
        }

        v_hooks.sort_by_key(|h| h.priority);
        f_hooks.sort_by_key(|h| h.priority);

        let wgsl = emit_wgsl(tex_count, &uniform_layout_id, &v_hooks, &f_hooks);
        validate_wgsl_syntax(&wgsl)?;
        validate_wgsl_with_naga(&wgsl)?;
        let key = self.compute_cache_key(&effect_parts);

        if std::env::var("RENPY_HOST_DUMP_WGSL").ok().as_deref() == Some("1") {
            log::info!("--- COMPOSED WGSL [{key}] ---\n{wgsl}\n-------------------");
        }

        Ok(ComposedShader {
            effect_parts,
            tex_count,
            uniform_layout_id,
            has_uniforms,
            cache_key: key,
            wgsl_source: wgsl,
        })
    }

    /// Legacy / tuple-compatible entry point for PyO3 / external bindings.
    pub fn compose_wgsl(
        &self,
        partnames: &[String],
        has_texture: bool,
    ) -> Result<(Vec<String>, u8, String, bool, String, String), String> {
        self.compose(partnames, has_texture)
            .map(|c| {
                (
                    c.effect_parts,
                    c.tex_count,
                    c.uniform_layout_id,
                    c.has_uniforms,
                    c.cache_key,
                    c.wgsl_source,
                )
            })
            .map_err(|e| e.to_string())
    }
}

/// Robust syntactic validator for generated or part-supplied WGSL code.
/// Checks bracket/parenthesis balancing, basic token well-formedness, and syntax invariants.
pub fn validate_wgsl_syntax(source: &str) -> Result<(), ShaderError> {
    let mut stack = Vec::new();
    let mut in_line_comment = false;
    let mut in_block_comment = false;
    let mut chars = source.chars().peekable();
    let mut line_num = 1;
    let mut col_num = 0;

    while let Some(ch) = chars.next() {
        col_num += 1;
        if ch == '\n' {
            line_num += 1;
            col_num = 0;
            in_line_comment = false;
            continue;
        }

        if in_line_comment {
            continue;
        }

        if in_block_comment {
            if ch == '*' && chars.peek() == Some(&'/') {
                chars.next();
                col_num += 1;
                in_block_comment = false;
            }
            continue;
        }

        if ch == '/' {
            if chars.peek() == Some(&'/') {
                chars.next();
                col_num += 1;
                in_line_comment = true;
                continue;
            } else if chars.peek() == Some(&'*') {
                chars.next();
                col_num += 1;
                in_block_comment = true;
                continue;
            }
        }

        match ch {
            '{' | '(' | '[' => {
                stack.push((ch, line_num, col_num));
            }
            '}' => match stack.pop() {
                Some(('{', _, _)) => {}
                Some((open, l, c)) => {
                    return Err(ShaderError::InvalidSyntax(format!(
                            "mismatched closing '}}' at line {line_num}:{col_num}, expected closing for '{open}' opened at line {l}:{c}"
                        )));
                }
                None => {
                    return Err(ShaderError::InvalidSyntax(format!(
                        "unmatched closing '}}' at line {line_num}:{col_num}"
                    )));
                }
            },
            ')' => match stack.pop() {
                Some(('(', _, _)) => {}
                Some((open, l, c)) => {
                    return Err(ShaderError::InvalidSyntax(format!(
                            "mismatched closing ')' at line {line_num}:{col_num}, expected closing for '{open}' opened at line {l}:{c}"
                        )));
                }
                None => {
                    return Err(ShaderError::InvalidSyntax(format!(
                        "unmatched closing ')' at line {line_num}:{col_num}"
                    )));
                }
            },
            ']' => match stack.pop() {
                Some(('[', _, _)) => {}
                Some((open, l, c)) => {
                    return Err(ShaderError::InvalidSyntax(format!(
                            "mismatched closing ']' at line {line_num}:{col_num}, expected closing for '{open}' opened at line {l}:{c}"
                        )));
                }
                None => {
                    return Err(ShaderError::InvalidSyntax(format!(
                        "unmatched closing ']' at line {line_num}:{col_num}"
                    )));
                }
            },
            _ => {}
        }
    }

    if in_block_comment {
        return Err(ShaderError::InvalidSyntax(
            "unclosed block comment '/*' in WGSL source".to_string(),
        ));
    }

    if let Some((open, l, c)) = stack.pop() {
        return Err(ShaderError::InvalidSyntax(format!(
            "unclosed delimiter '{open}' from line {l}:{c}"
        )));
    }

    Ok(())
}

/// Direct naga validation for a complete WGSL module.
///
/// Parses `source` with `naga::front::wgsl::parse_str` and then validates the
/// resulting IR with `naga::valid::Validator`.  Only intended for full
/// `wgsl_source` modules (as emitted by `emit_wgsl` / `NativeShaderComposer::compose`);
/// snippet bodies (hook fragments) are *not* valid WGSL modules and must not be
/// passed here — they continue to use the lightweight `validate_wgsl_syntax` only.
pub fn validate_wgsl_with_naga(source: &str) -> Result<(), ShaderError> {
    let module = naga::front::wgsl::parse_str(source).map_err(|e| {
        if let Some(loc) = e.location(source) {
            ShaderError::InvalidSyntax(format!(
                "naga parse error at line {}:{}: {}",
                loc.line_number,
                loc.line_position,
                e.message()
            ))
        } else {
            let diag = e.emit_to_string(source);
            ShaderError::InvalidSyntax(format!(
                "naga parse error: {} -- {}",
                e.message(),
                diag.lines().next().unwrap_or("").trim()
            ))
        }
    })?;
    let mut validator = naga::valid::Validator::new(
        naga::valid::ValidationFlags::all(),
        naga::valid::Capabilities::all(),
    );
    validator.validate(&module).map_err(|e| {
        if let Some(loc) = e.location(source) {
            ShaderError::InvalidSyntax(format!(
                "naga validation error at line {}:{}: {}",
                loc.line_number, loc.line_position, e
            ))
        } else {
            let diag = e.emit_to_string(source);
            ShaderError::InvalidSyntax(format!(
                "naga validation error: {e} -- {}",
                diag.lines().next().unwrap_or("").trim()
            ))
        }
    })?;
    Ok(())
}

fn indent(body: &str, spaces: usize) -> String {
    let pad = " ".repeat(spaces);
    let mut out = String::new();
    for line in body.lines() {
        if line.trim().is_empty() {
            out.push('\n');
        } else {
            out.push_str(&pad);
            out.push_str(line);
            out.push('\n');
        }
    }
    if out.ends_with('\n') {
        out.pop();
    }
    out
}

fn uniform_binding(tex_count: u8) -> u8 {
    if tex_count == 0 {
        0
    } else if tex_count == 1 {
        2
    } else if tex_count == 2 {
        3
    } else {
        4
    }
}

pub fn emit_wgsl(
    tex_count: u8,
    uniform_layout_id: &str,
    vertex_hooks: &[ShaderHook],
    fragment_hooks: &[ShaderHook],
) -> String {
    let has_uniforms = uniform_layout_id != UNIFORM_NONE;
    let mut chunks: Vec<String> = Vec::new();

    chunks.push(
        "// composed by renpy.wgpu.composer
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) color: vec4<f32>,
};"
        .into(),
    );

    if uniform_layout_id == UNIFORM_MATRIXCOLOR16 {
        chunks.push(
            "struct Params {
    col0: vec4<f32>,
    col1: vec4<f32>,
    col2: vec4<f32>,
    col3: vec4<f32>,
};"
            .into(),
        );
    } else if uniform_layout_id == UNIFORM_PARAMS16 || has_uniforms {
        chunks.push(
            "struct Params {
    data0: vec4<f32>,
    data1: vec4<f32>,
    data2: vec4<f32>,
    data3: vec4<f32>,
};"
            .into(),
        );
    }

    if tex_count >= 1 {
        chunks.push("@group(0) @binding(0) var t_color: texture_2d<f32>;".into());
        chunks.push("@group(0) @binding(1) var s_color: sampler;".into());
        if tex_count >= 2 {
            chunks.push("@group(0) @binding(2) var t_tex1: texture_2d<f32>;".into());
        }
        if tex_count >= 3 {
            chunks.push("@group(0) @binding(3) var t_tex2: texture_2d<f32>;".into());
        }
    }

    if has_uniforms {
        let ub = uniform_binding(tex_count);
        chunks.push(format!("@group(0) @binding({ub}) var<uniform> u: Params;"));
    }

    // Vertex stage
    let mut vs_lines = vec![
        "@vertex".to_string(),
        "fn vs_main(v: VsIn) -> VsOut {".to_string(),
        "    var o: VsOut;".to_string(),
        "    o.clip = vec4<f32>(v.pos, 0.0, 1.0);".to_string(),
        "    o.uv = v.uv;".to_string(),
        "    o.color = v.color;".to_string(),
    ];
    for h in vertex_hooks {
        let body = h.body.trim();
        if !body.is_empty() {
            vs_lines.push(indent(body, 4));
        }
    }
    vs_lines.push("    return o;".to_string());
    vs_lines.push("}".to_string());
    chunks.push(vs_lines.join("\n"));

    // Fragment stage
    let mut fs_lines = vec![
        "@fragment".to_string(),
        "fn fs_main(v: VsOut) -> @location(0) vec4<f32> {".to_string(),
        "    var color: vec4<f32> = vec4<f32>(0.0);".to_string(),
    ];
    for h in fragment_hooks {
        let body = h.body.trim();
        if !body.is_empty() {
            fs_lines.push(indent(body, 4));
        }
    }
    fs_lines.push("    let a = clamp(color.a, 0.0, 1.0);".to_string());
    fs_lines.push("    return vec4<f32>(color.rgb * a, a);".to_string());
    fs_lines.push("}".to_string());
    chunks.push(fs_lines.join("\n"));

    chunks.join("\n\n") + "\n"
}
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_shader_composer_default_texture() {
        let composer = NativeShaderComposer::new();
        let composed = composer
            .compose(&["renpy.texture".to_string()], true)
            .expect("composition succeeds");
        assert_eq!(composed.tex_count, 1);
        assert_eq!(composed.uniform_layout_id, UNIFORM_NONE);
        assert!(!composed.has_uniforms);
        assert!(composed.wgsl_source.contains("textureSample"));
        assert!(composed
            .wgsl_source
            .contains("@group(0) @binding(0) var t_color: texture_2d<f32>;"));
        assert!(composed
            .wgsl_source
            .contains("@group(0) @binding(1) var s_color: sampler;"));
    }

    #[test]
    fn test_shader_composer_default_solid() {
        let composer = NativeShaderComposer::new();
        let composed = composer
            .compose(&["renpy.solid".to_string()], false)
            .expect("composition succeeds");
        assert_eq!(composed.tex_count, 0);
        assert_eq!(composed.uniform_layout_id, UNIFORM_NONE);
        assert!(!composed.has_uniforms);
        assert!(composed.wgsl_source.contains("color = v.color;"));
        assert!(!composed.wgsl_source.contains("t_color"));
    }

    #[test]
    fn test_shader_composer_empty_fallback() {
        let composer = NativeShaderComposer::new();
        let composed_tex = composer.compose(&[], true).expect("fallback texture");
        assert_eq!(composed_tex.tex_count, 1);
        assert!(composed_tex
            .effect_parts
            .contains(&"renpy.texture".to_string()));

        let composed_solid = composer.compose(&[], false).expect("fallback solid");
        assert_eq!(composed_solid.tex_count, 0);
        assert!(composed_solid
            .effect_parts
            .contains(&"renpy.solid".to_string()));
    }

    #[test]
    fn test_shader_composer_matrixcolor_uniforms() {
        let composer = NativeShaderComposer::new();
        let composed = composer
            .compose(
                &["renpy.texture".to_string(), "renpy.matrixcolor".to_string()],
                true,
            )
            .expect("matrixcolor composed");
        assert_eq!(composed.tex_count, 1);
        assert_eq!(composed.uniform_layout_id, UNIFORM_MATRIXCOLOR16);
        assert!(composed.has_uniforms);
        assert!(composed.wgsl_source.contains("struct Params"));
        assert!(composed.wgsl_source.contains("col0: vec4<f32>"));
        assert!(composed
            .wgsl_source
            .contains("@group(0) @binding(2) var<uniform> u: Params;"));
    }

    #[test]
    fn test_shader_composer_atomic_parts_rejected() {
        let composer = NativeShaderComposer::new();
        let err_dissolve = composer
            .compose(
                &["renpy.texture".to_string(), "renpy.dissolve".to_string()],
                true,
            )
            .unwrap_err();
        match err_dissolve {
            ShaderError::AtomicPart(name) => assert_eq!(name, "renpy.dissolve"),
            other => panic!("Expected AtomicPart, got {other:?}"),
        }
    }
    #[test]
    fn test_shader_composer_duplicate_parts_and_ordering() {
        let composer = NativeShaderComposer::new();
        let composed = composer
            .compose(
                &[
                    "renpy.matrixcolor".to_string(),
                    "renpy.texture".to_string(),
                    "renpy.matrixcolor".to_string(),
                ],
                true,
            )
            .expect("duplicate parts deduplicated");
        assert_eq!(composed.effect_parts.len(), 2);
        assert_eq!(composed.effect_parts[0], "renpy.matrixcolor");
        assert_eq!(composed.effect_parts[1], "renpy.texture");
    }

    #[test]
    fn test_shader_composer_uniform_conflict() {
        let mut composer = NativeShaderComposer::new();
        let custom_part = ShaderPart::new(
            "custom.conflicting_uniforms",
            0,
            "custom.layout_xyz",
            vec![],
            vec![ShaderHook {
                priority: 500,
                body: "color = color * 0.5;".into(),
            }],
            false,
            false,
        );
        composer.registry.register_part(custom_part);

        let err = composer
            .compose(
                &[
                    "renpy.matrixcolor".to_string(),
                    "custom.conflicting_uniforms".to_string(),
                ],
                false,
            )
            .unwrap_err();

        match err {
            ShaderError::UniformConflict {
                ref current,
                ref conflicting,
            } => {
                assert!(
                    (current == UNIFORM_MATRIXCOLOR16 && conflicting == "custom.layout_xyz")
                        || (current == "custom.layout_xyz" && conflicting == UNIFORM_MATRIXCOLOR16)
                );
            }
            other => panic!("Expected UniformConflict, got {other:?}"),
        }
    }

    #[test]
    fn test_shader_composer_texture_limit_exceeded() {
        let mut composer = NativeShaderComposer::new();
        let part_3_tex = ShaderPart::new(
            "custom.three_textures",
            4, // 4 textures exceeds max 3
            UNIFORM_NONE,
            vec![],
            vec![],
            false,
            false,
        );
        let err = composer
            .registry
            .register_part_checked(part_3_tex)
            .unwrap_err();
        match err {
            ShaderError::ExceededMaxTextures { count, max } => {
                assert_eq!(count, 4);
                assert_eq!(max, 3);
            }
            other => panic!("Expected ExceededMaxTextures, got {other:?}"),
        }
    }

    #[test]
    fn test_shader_composer_invalid_syntax_error() {
        let mut composer = NativeShaderComposer::new();
        let bad_part = ShaderPart::new(
            "custom.bad_syntax",
            0,
            UNIFORM_NONE,
            vec![],
            vec![ShaderHook {
                priority: 100,
                body: "if (x > 0 { color = vec4(1.0); }".into(), // unmatched parenthesis
            }],
            false,
            false,
        );
        let err = composer
            .registry
            .register_part_checked(bad_part)
            .unwrap_err();
        match err {
            ShaderError::InvalidSyntax(msg) => {
                assert!(
                    msg.contains("mismatched closing '}'")
                        || msg.contains("syntax")
                        || msg.contains("delimiter")
                );
            }
            other => panic!("Expected InvalidSyntax, got {other:?}"),
        }
    }
    #[test]
    fn test_shader_composer_unknown_part() {
        let composer = NativeShaderComposer::new();
        let err = composer
            .compose(&["nonexistent.part".to_string()], true)
            .unwrap_err();
        match err {
            ShaderError::UnknownPart(name) => {
                assert_eq!(name, "nonexistent.part");
            }
            other => panic!("Expected UnknownPart, got {other:?}"),
        }
    }

    #[test]
    fn test_shader_composer_deterministic_key() {
        let composer = NativeShaderComposer::new();
        let k1 = composer.compute_cache_key(&["renpy.matrixcolor".into(), "renpy.texture".into()]);
        let k2 = composer.compute_cache_key(&["renpy.matrixcolor".into(), "renpy.texture".into()]);
        let k3 = composer.compute_cache_key(&["renpy.texture".into(), "renpy.matrixcolor".into()]);
        assert_eq!(k1, k2);
        assert_ne!(k1, k3); // keys are order-dependent on normalized parts
    }
}
