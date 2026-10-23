//! Owner-managed read API mapping. No request can choose an upstream origin or route.
use super::{ClientClass, ClientIdentity};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashMap, HashSet};

fn manifest_version() -> u32 {
    1
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Service {
    #[serde(default = "manifest_version")]
    format_version: u32,
    pub service_id: String,
    pub operations: Vec<Operation>,
    credentials: Vec<Credential>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Operation {
    pub name: String,
    description: String,
    pub path: String,
    upstream_path: String,
    parameters: BTreeMap<String, Parameter>,
    #[serde(skip)]
    pub native: Option<crate::Operation>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Parameter {
    #[serde(rename = "type")]
    kind: ParameterType,
    upstream: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum ParameterType {
    String,
    Integer,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Credential {
    token_env: String,
    required: bool,
    principal_id: String,
    client_class: ClientClass,
    operations: Vec<String>,
    max_outstanding: usize,
}
pub struct UpstreamRequest {
    pub path: String,
    pub query: Vec<(String, String)>,
}
pub(crate) fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
}
fn route(value: &str) -> bool {
    value
        .strip_prefix('/')
        .is_some_and(|path| path.split('/').all(identifier))
}
pub(crate) fn operation_path(value: &str) -> bool {
    route(value)
        && value != "/healthz"
        && value != "/readyz"
        && value != "/agent-policy"
        && value != "/admin"
        && !value.starts_with("/admin/")
}
pub fn upstream_origin(value: String) -> Result<String, &'static str> {
    let url = reqwest::Url::parse(&value).map_err(|_| "Invalid upstream origin")?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(
            "UPSTREAM_URL must be an HTTP(S) origin without credentials, path, query or fragment",
        );
    }
    Ok(url.as_str().trim_end_matches('/').to_owned())
}
impl Service {
    pub fn catalog() -> Self {
        Self::parse(include_str!("../services/catalog.json"))
            .expect("Invalid built-in catalog manifest")
    }
    pub fn from_env() -> Result<Self, String> {
        match std::env::var("SERVICE_CONFIG") {
            Ok(path) => Self::parse(
                &std::fs::read_to_string(path).map_err(|_| "Cannot read SERVICE_CONFIG")?,
            ),
            Err(std::env::VarError::NotPresent) => Ok(Self::catalog()),
            Err(_) => Err("Invalid SERVICE_CONFIG".into()),
        }
    }
    pub fn parse(text: &str) -> Result<Self, String> {
        let service: Self = serde_json::from_str(text).map_err(|error| {
            format!(
                "Invalid service manifest JSON at line {}, column {}; check field names and types",
                error.line(),
                error.column()
            )
        })?;
        if service.format_version != 1 {
            return Err("Unsupported manifest format_version; expected 1".into());
        }
        let mut names = HashSet::new();
        let mut paths = HashSet::new();
        if !identifier(&service.service_id)
            || service.operations.is_empty()
            || service.credentials.is_empty()
        {
            return Err("Service requires an ID, operations and credentials".into());
        }
        for (index, op) in service.operations.iter().enumerate() {
            let mut parameters = HashSet::new();
            if !identifier(&op.name)
                || !names.insert(&op.name)
                || !operation_path(&op.path)
                || !paths.insert(&op.path)
                || !route(&op.upstream_path)
                || op.parameters.iter().any(|(name, p)| {
                    !identifier(name) || !identifier(&p.upstream) || !parameters.insert(&p.upstream)
                })
            {
                return Err(format!(
                    "Invalid or duplicate operations[{index}] mapping; check names, reserved paths and parameter mappings"
                ));
            }
        }
        let mut budgets = HashMap::new();
        let mut envs = HashSet::new();
        for (index, c) in service.credentials.iter().enumerate() {
            if !identifier(&c.token_env)
                || !envs.insert(&c.token_env)
                || c.principal_id.is_empty()
                || !(1..=1000).contains(&c.max_outstanding)
                || c.operations.iter().any(|name| !names.contains(name))
            {
                return Err(format!(
                    "Invalid credentials[{index}]; check environment name, principal, grants and max_outstanding"
                ));
            }
            if budgets
                .insert((&c.principal_id, c.client_class), c.max_outstanding)
                .is_some_and(|previous| previous != c.max_outstanding)
            {
                return Err("Tokens of one owner and class must specify the same budget".into());
            }
        }
        Ok(service)
    }
    pub fn identities(
        &self,
        mut read: impl FnMut(&str) -> Result<String, std::env::VarError>,
    ) -> Result<HashMap<String, ClientIdentity>, String> {
        let mut credentials = Vec::new();
        for c in &self.credentials {
            let token = match read(&c.token_env) {
                Ok(token) => token,
                Err(std::env::VarError::NotPresent) if !c.required => continue,
                Err(_) => return Err(format!("Missing or invalid {}", c.token_env)),
            };
            if !crate::gateway::valid_token(&token) {
                return Err(format!("Invalid bearer token in {}", c.token_env));
            }
            credentials.push(crate::Credential {
                token,
                principal_id: c.principal_id.clone(),
                client_class: c.client_class,
                operations: c.operations.clone(),
                max_outstanding: c.max_outstanding,
            });
        }
        crate::gateway::identities(credentials, &self.operations)
    }

    pub fn native(service_id: &str, operations: Vec<crate::Operation>) -> Result<Self, String> {
        let mut names = HashSet::new();
        let mut paths = HashSet::new();
        if !identifier(service_id) || operations.is_empty() {
            return Err("Service requires an ID and operations".into());
        }
        for op in &operations {
            if !names.insert(&op.name) || !paths.insert(&op.path) {
                return Err("Duplicate operation name or path".into());
            }
        }
        Ok(Self {
            format_version: 1,
            service_id: service_id.into(),
            operations: operations
                .into_iter()
                .map(|op| Operation {
                    name: op.name.clone(),
                    description: op.description.clone(),
                    path: op.path.clone(),
                    upstream_path: String::new(),
                    parameters: BTreeMap::new(),
                    native: Some(op),
                })
                .collect(),
            credentials: Vec::new(),
        })
    }
}
impl Operation {
    pub fn descriptor(&self) -> Value {
        if let Some(native) = &self.native {
            return json!({"name": self.name, "description": self.description, "method": "POST",
                "path": self.path, "input_schema": native.input_schema});
        }
        let properties: BTreeMap<_, _> = self
            .parameters
            .iter()
            .map(|(name, p)| {
                (
                    name,
                    match p.kind {
                        ParameterType::String => json!({"type":"string"}),
                        ParameterType::Integer => json!({"type":"integer", "minimum":0}),
                    },
                )
            })
            .collect();
        json!({"name":self.name,"description":self.description,"method":"POST","path":self.path,
            "input_schema":{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object",
                "properties":properties,"required":self.parameters.keys().collect::<Vec<_>>(),"additionalProperties":false}})
    }
    pub fn request(&self, params: &Value) -> Result<UpstreamRequest, &'static str> {
        let values = params
            .as_object()
            .filter(|v| v.len() == self.parameters.len())
            .ok_or("Invalid operation parameters")?;
        let query = self
            .parameters
            .iter()
            .map(|(name, p)| {
                let value = values.get(name).ok_or("Missing operation parameter")?;
                let text = match p.kind {
                    ParameterType::String => value.as_str().map(str::to_owned),
                    ParameterType::Integer => value.as_u64().map(|n| n.to_string()),
                }
                .ok_or("Invalid operation parameter type")?;
                Ok((p.upstream.clone(), text))
            })
            .collect::<Result<_, &'static str>>()?;
        Ok(UpstreamRequest {
            path: self.upstream_path.clone(),
            query,
        })
    }
}
